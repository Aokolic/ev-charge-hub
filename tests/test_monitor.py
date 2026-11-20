"""业务线3 监控/告警/工单关键回归测试。

运行：.venv\\Scripts\\python.exe tests/test_monitor.py
依赖本机 MySQL 8；测试会创建并删除隔离库 ev_charge_hub_test_monitor。
"""

import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pymysql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.auth import RoleEnum
from app.config import settings
from app.dao import monitor as monitor_dao
from app.dao.monitor import MonitorError
from app.models.monitor import RepairOrder
from app.schemas.common import PageParams
from app.schemas.monitor import RepairDispatchIn, TelemetryIn

_SQL_DIR = _PROJECT_ROOT / "sql"
_TEST_DB = "ev_charge_hub_test_monitor"
_BASE_URL = make_url(settings.DATABASE_URL)
_URL = _BASE_URL.set(database=_TEST_DB)
_MYSQL = dict(
    host=_BASE_URL.host or "127.0.0.1",
    port=_BASE_URL.port or 3306,
    user=_BASE_URL.username or "root",
    password=_BASE_URL.password or "",
    charset="utf8mb4",
)


def _split_statements(sql_text: str) -> list[str]:
    """按 DELIMITER 切分脚本，保证存储过程整体执行。"""
    delimiter = ";"
    buffer: list[str] = []
    statements: list[str] = []
    for raw in sql_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        if line.upper().startswith("DELIMITER "):
            delimiter = line.split(None, 1)[1].strip()
            continue
        buffer.append(raw)
        joined = "\n".join(buffer)
        if joined.rstrip().endswith(delimiter):
            statement = joined.rstrip()[: -len(delimiter)].strip()
            if statement:
                statements.append(statement)
            buffer = []
    tail = "\n".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def _rebuild_database() -> None:
    conn = pymysql.connect(**_MYSQL)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{_TEST_DB}`")
            cur.execute(
                f"CREATE DATABASE `{_TEST_DB}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(_URL, future=True)
    try:
        with engine.begin() as conn:
            for name in ("01_ddl.sql", "03_procs_triggers.sql", "04_seed.sql", "02_views.sql"):
                script = (_SQL_DIR / name).read_text(encoding="utf-8")
                for statement in _split_statements(script):
                    head = statement.lstrip().upper()
                    if head.startswith("USE ") or head.startswith("CREATE DATABASE"):
                        continue
                    conn.execute(text(statement))
    finally:
        engine.dispose()


class MonitorIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            conn = pymysql.connect(**_MYSQL)
            conn.close()
        except Exception as exc:
            raise unittest.SkipTest(f"MySQL 不可用：{exc}") from exc
        _rebuild_database()
        cls.engine = create_engine(_URL, future=True)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False, future=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        try:
            conn = pymysql.connect(**_MYSQL)
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{_TEST_DB}`")
            conn.commit()
            conn.close()
        except Exception:
            pass

    def setUp(self) -> None:
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_issue18_regressions(self) -> None:
        """覆盖升级方向、无遥测误恢复、管理员查询、派单校验和超时保护。"""
        db = self.db
        base = datetime.now().replace(microsecond=0) - timedelta(hours=2)

        # 1. 告警升级方向：L4 -> L3 -> L2 -> L1；L2 升级后必须得到 15 分钟时限
        db.execute(text("UPDATE alarm_record SET status = 'CLOSED'"))
        db.execute(
            text(
                "UPDATE alarm_record SET alarm_level='L4', original_level='L4', "
                "response_limit_min=480, status='PROCESSING', escalated_count=0, "
                "escalated_at=NULL, occurred_at='2026-11-18 00:00:00', "
                "recovered_at=NULL "
                "WHERE alarm_id=20"
            )
        )
        db.execute(
            text(
                "UPDATE alarm_record SET alarm_level='L2', original_level='L2', "
                "response_limit_min=30, status='PROCESSING', escalated_count=0, "
                "escalated_at=NULL, occurred_at='2026-11-18 00:00:00', "
                "recovered_at=NULL "
                "WHERE alarm_id=21"
            )
        )
        db.execute(text("UPDATE repair_order SET status='CLOSED' WHERE alarm_id=20"))
        db.execute(
            text(
                "UPDATE repair_order SET status='DISPATCHED', accepted_at=NULL, "
                "arrived_at=NULL, repaired_at=NULL, verified_at=NULL, closed_at=NULL "
                "WHERE work_order_id=22"
            )
        )
        db.commit()

        count = monitor_dao.escalate_overdue_alarms(
            db, datetime(2026, 11, 20, 12, 0, 0)
        )
        self.assertEqual(count, 2)
        level_20, limit_20 = db.execute(
            text(
                "SELECT alarm_level, response_limit_min FROM alarm_record "
                "WHERE alarm_id=20"
            )
        ).one()
        level_21, limit_21, escalated_21 = db.execute(
            text(
                "SELECT alarm_level, response_limit_min, escalated_count "
                "FROM alarm_record WHERE alarm_id=21"
            )
        ).one()
        self.assertEqual((level_20, limit_20), ("L3", 120))
        self.assertEqual((level_21, limit_21, escalated_21), ("L1", 15, 1))

        # 2. 无效维修人不得派单
        db.execute(text("UPDATE alarm_record SET status='ACTIVE' WHERE alarm_id=1"))
        db.execute(text("DELETE FROM repair_order WHERE alarm_id=1"))
        db.commit()
        with self.assertRaises(MonitorError) as missing_user:
            monitor_dao.dispatch_repair_order(
                db, 1, RepairDispatchIn(maintainer_id=999999)
            )
        self.assertEqual(missing_user.exception.status_code, 404)
        with self.assertRaises(MonitorError) as non_ops:
            monitor_dao.dispatch_repair_order(db, 1, RepairDispatchIn(maintainer_id=1))
        self.assertEqual(non_ops.exception.status_code, 400)

        work_order_id = monitor_dao.dispatch_repair_order(
            db, 1, RepairDispatchIn(maintainer_id=2)
        )
        self.assertGreater(work_order_id, 0)
        admin_page = monitor_dao.list_workorders(
            db, PageParams(), RoleEnum.ADMIN, 1
        )
        self.assertGreater(admin_page["total"], 0)
        with self.assertRaises(MonitorError) as idor:
            monitor_dao.get_workorder(db, work_order_id, RoleEnum.OPS, 3)
        self.assertEqual(idor.exception.status_code, 403)

        # 3. 修复后无遥测不得恢复告警；连续两条正常遥测才可以恢复
        db.execute(
            text(
                "UPDATE alarm_record SET status='PROCESSING' WHERE alarm_id=2"
            )
        )
        db.execute(text("DELETE FROM repair_order WHERE alarm_id=2"))
        db.execute(text("DELETE FROM pile_telemetry WHERE pile_id=13"))
        db.commit()

        first_order = RepairOrder(
            alarm_id=2,
            maintainer_id=3,
            status="REPAIRED",
            dispatched_at=base,
            accepted_at=base + timedelta(minutes=5),
            arrived_at=base + timedelta(minutes=20),
            repaired_at=base + timedelta(minutes=40),
            created_at=base,
            updated_at=base + timedelta(minutes=40),
        )
        db.add(first_order)
        db.commit()
        message = monitor_dao.close_repair_order(
            db, first_order.work_order_id, 3, RoleEnum.OPS, None
        )
        self.assertIn("没有遥测", message)
        alarm_status = db.execute(
            text("SELECT status FROM alarm_record WHERE alarm_id=2")
        ).scalar_one()
        self.assertEqual(alarm_status, "PROCESSING")

        db.execute(
            text(
                "INSERT INTO pile_telemetry "
                "(telemetry_id, pile_id, collected_at, output_voltage, output_current, "
                " power, module_temp, offline_duration_sec, fault_code) VALUES "
                "(9001, 13, :t1, 0, NULL, 0, 30, 0, NULL), "
                "(9002, 13, :t2, 0, NULL, 0, 30, 0, NULL)"
            ),
            {"t1": base + timedelta(minutes=60), "t2": base + timedelta(minutes=61)},
        )
        second_order = RepairOrder(
            alarm_id=2,
            maintainer_id=3,
            status="REPAIRED",
            dispatched_at=base + timedelta(minutes=50),
            accepted_at=base + timedelta(minutes=51),
            arrived_at=base + timedelta(minutes=52),
            repaired_at=base + timedelta(minutes=55),
            created_at=base + timedelta(minutes=50),
            updated_at=base + timedelta(minutes=55),
        )
        db.add(second_order)
        db.commit()
        message = monitor_dao.close_repair_order(
            db, second_order.work_order_id, 3, RoleEnum.OPS, None
        )
        self.assertIn("告警已解除", message)
        alarm_status = db.execute(
            text("SELECT status FROM alarm_record WHERE alarm_id=2")
        ).scalar_one()
        self.assertEqual(alarm_status, "RECOVERED")

        # 4. 管理员可以复核/关闭他人工单，关闭后进入 CLOSED
        db.execute(text("UPDATE alarm_record SET status='PROCESSING' WHERE alarm_id=3"))
        db.execute(text("DELETE FROM repair_order WHERE alarm_id=3"))
        admin_base = datetime.now().replace(microsecond=0) - timedelta(hours=1)
        admin_order = RepairOrder(
            alarm_id=3,
            maintainer_id=4,
            status="REPAIRED",
            dispatched_at=admin_base,
            accepted_at=admin_base + timedelta(minutes=5),
            arrived_at=admin_base + timedelta(minutes=10),
            repaired_at=admin_base + timedelta(minutes=30),
            created_at=admin_base,
            updated_at=admin_base + timedelta(minutes=30),
        )
        db.add(admin_order)
        db.commit()
        with self.assertRaises(MonitorError) as future_close:
            monitor_dao.close_repair_order(
                db,
                admin_order.work_order_id,
                1,
                RoleEnum.ADMIN,
                datetime.now() + timedelta(days=1),
            )
        self.assertEqual(future_close.exception.status_code, 400)
        monitor_dao.verify_repair_order(
            db, admin_order.work_order_id, 1, RoleEnum.ADMIN, None
        )
        self.assertEqual(
            db.execute(
                text("SELECT status FROM repair_order WHERE work_order_id=:id"),
                {"id": admin_order.work_order_id},
            ).scalar_one(),
            "VERIFIED",
        )
        monitor_dao.close_repair_order(
            db, admin_order.work_order_id, 1, RoleEnum.ADMIN, None
        )
        self.assertEqual(
            db.execute(
                text("SELECT status FROM repair_order WHERE work_order_id=:id"),
                {"id": admin_order.work_order_id},
            ).scalar_one(),
            "CLOSED",
        )

        # 5. 遥测批量校验：桩不存在和超过额定功率都必须拒绝
        with self.assertRaises(MonitorError) as missing_pile:
            monitor_dao.create_telemetry_batch(
                db,
                [
                    TelemetryIn(
                        pile_id=99999,
                        collected_at=datetime(2026, 11, 20, 14, 0, 0),
                        power=Decimal("1"),
                    )
                ],
            )
        self.assertEqual(missing_pile.exception.status_code, 404)
        with self.assertRaises(MonitorError) as over_power:
            monitor_dao.create_telemetry_batch(
                db,
                [
                    TelemetryIn(
                        pile_id=1,
                        collected_at=datetime(2026, 11, 20, 14, 1, 0),
                        power=Decimal("181"),
                    )
                ],
            )
        self.assertEqual(over_power.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
