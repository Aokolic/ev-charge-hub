"""业务线 2/5（订单计费、结算对账）端到端验收测试。

覆盖：
1. OpenAPI 契约：订单与结算路由已注册；
2. RBAC 隔离视图：v_order_owner 只出本人订单；
3. 同枪并发：第二单触发 1062（uk_gun_active）；
4. 结束充电：调用 sp_order_calc 按峰平谷拆明细，金额 > 0；
5. 支付守卫：超付触发 1644；
6. 月结与开票：生成账单、超额开票触发 1644。

运行：python tests/test_order.py
依赖本机 MySQL 8（默认 127.0.0.1:3307，root/123456），会自建并删除 ev_charge_hub_test_order。
"""

import asyncio
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pymysql
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.auth import CurrentUser, RoleEnum, create_access_token
from app.config import settings
from app.dao import order as order_dao
from app.dao import settle as settle_dao
from app.db import get_db
from app.main import app
from app.routers.order import _check_owner
from app.schemas.common import PageParams
from app.schemas.order import OrderFinishIn, OrderStartIn, PaymentIn, PriceRuleIn, RefundIn
from app.schemas.settle import BillGenerateIn, InvoiceIn

_SQL_DIR = _PROJECT_ROOT / "sql"
_TEST_DB = "ev_charge_hub_test_order"

# 连接信息统一从项目配置取（.env 或环境变量），只把库名换成隔离测试库，
# 不在测试里硬编码端口和密码，否则换台机器就失效。
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
    """按 DELIMITER 切分脚本，保证存储过程能整体执行。"""
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
            stmt = joined.rstrip()[: -len(delimiter)].strip()
            if stmt:
                statements.append(stmt)
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
                f"CREATE DATABASE `{_TEST_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(_URL, future=True)
    try:
        with engine.begin() as conn:
            for name in ("01_ddl.sql", "02_views.sql", "03_procs_triggers.sql", "04_seed.sql"):
                script = (_SQL_DIR / name).read_text(encoding="utf-8")
                for stmt in _split_statements(script):
                    head = stmt.lstrip().upper()
                    # 库名由测试自己指定：跳过脚本里的 USE 与 CREATE DATABASE，
                    # 否则会写进生产库、或在生产库名上建出空库。
                    if head.startswith("USE ") or head.startswith("CREATE DATABASE"):
                        continue
                    conn.execute(text(stmt))
    finally:
        engine.dispose()


def _asgi_get(path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    """不依赖 httpx，直接通过 ASGI 3.0 协议调用 FastAPI app（走完整路由与鉴权链路）。"""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    status_code = 0
    body = bytearray()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = message["status"]
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    asyncio.run(app(scope, receive, send))
    return status_code, bytes(body)


class OrderSettleTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _rebuild_database()
        cls.engine = create_engine(_URL, future=True)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False, future=True)
        cls._prev_get_db_override = app.dependency_overrides.get(get_db)

        def _override_get_db():
            session = cls.Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = _override_get_db

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._prev_get_db_override is not None:
            app.dependency_overrides[get_db] = cls._prev_get_db_override
        else:
            app.dependency_overrides.pop(get_db, None)
        cls.engine.dispose()
        conn = pymysql.connect(**_MYSQL)
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{_TEST_DB}`")
            conn.commit()
        finally:
            conn.close()

    def setUp(self) -> None:
        self.db = self.Session()

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    # ---------- 辅助构造隔离测试资产 ----------
    def _tag(self, prefix: str) -> str:
        return f"{prefix}{uuid4().hex[:8].upper()}"

    def _create_facility(
        self,
        *,
        station_status: str = "OPEN",
        acceptance_status: str = "PASSED",
        pile_status: str = "IDLE",
        gun_status: str = "IDLE",
        gun_count: int = 1,
    ) -> dict[str, int]:
        op_code = self._tag("OP")
        st_code = self._tag("ST")
        pl_code = self._tag("PL")
        credit_code = f"91110108{uuid4().hex[:10].upper()}"
        self.db.execute(
            text(
                "INSERT INTO operator (operator_code, operator_name, credit_code, contact_name, service_phone, status) "
                "VALUES (:code, :name, :cc, '测试员', '4000000001', 'ACTIVE')"
            ),
            {"code": op_code, "name": f"测试运营商-{op_code}", "cc": credit_code},
        )
        op_id = int(self.db.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())

        self.db.execute(
            text(
                "INSERT INTO station (operator_id, station_code, station_name, address, "
                "longitude, latitude, total_spaces, available_spaces, station_status) "
                "VALUES (:op, :code, :name, '北京市测试路1号', 116.350000, 40.000000, 10, 10, :st)"
            ),
            {"op": op_id, "code": st_code, "name": f"测试站-{st_code}", "st": station_status},
        )
        st_id = int(self.db.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())

        self.db.execute(
            text(
                "INSERT INTO charging_pile (station_id, pile_code, device_model, rated_power, gun_count, "
                "commissioned_date, warranty_end_date, acceptance_status, current_status) "
                "VALUES (:st, :code, 'TEST-120KW', 360.00, :gc, '2025-01-01', '2030-01-01', :acc, :ps)"
            ),
            {"st": st_id, "code": pl_code, "gc": gun_count, "acc": acceptance_status, "ps": pile_status},
        )
        pile_id = int(self.db.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())

        gun_ids: list[int] = []
        for seq in range(1, gun_count + 1):
            self.db.execute(
                text(
                    "INSERT INTO charging_gun (pile_id, gun_code, gun_seq, gun_type, connector_standard, max_power, current_status) "
                    "VALUES (:pid, :code, :seq, 'DC_FAST', 'GB/T 20234.3', 120.00, :gs)"
                ),
                {"pid": pile_id, "code": f"{pl_code}-G{seq}", "seq": seq, "gs": gun_status},
            )
            gun_ids.append(int(self.db.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()))

        # 覆盖全天 24 小时无空隙的两段电价，确保任意时刻开单/结单都能匹配到规则
        self.db.execute(
            text(
                "INSERT INTO pricing_rule (station_id, period, start_time, end_time, energy_price, service_price, "
                "free_minutes, min_charge, effective_from, effective_to, is_active) VALUES "
                "(:st, 'FLAT', '00:00:00', '12:00:00', 1.0000, 0.5000, 0, 0.00, '2025-01-01', NULL, 1), "
                "(:st, 'PEAK', '12:00:00', '00:00:00', 1.0000, 0.5000, 0, 0.00, '2025-01-01', NULL, 1)"
            ),
            {"st": st_id},
        )
        self.db.commit()
        return {
            "operator_id": op_id,
            "station_id": st_id,
            "pile_id": pile_id,
            "gun_id": gun_ids[0],
            "gun_ids": gun_ids,
        }

    # ---------- 1. 契约 ----------
    def test_routes_registered(self) -> None:
        paths = app.openapi()["paths"]
        for p in ("/api/orders", "/api/orders/start", "/api/orders/{order_id}/finish",
                  "/api/orders/{order_id}/refund", "/api/settlements", "/api/settlements/generate"):
            self.assertIn(p, paths)

    # ---------- 2. 视图隔离 ----------
    def test_owner_view_only_returns_own_orders(self) -> None:
        total = self.db.execute(text("SELECT COUNT(*) FROM v_order_owner")).scalar_one()
        self.assertEqual(total, 20)
        page = order_dao.list_my_orders(self.db, 9001, PageParams(page=1, page_size=10))
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["user_id"], 9001)

    # ---------- 3. 同枪并发 ----------
    def test_second_order_on_same_gun_raises_1062(self) -> None:
        row = self.db.execute(
            text(
                "SELECT g.gun_id, p.station_id FROM charging_gun g "
                "JOIN charging_pile p ON p.pile_id = g.pile_id "
                "JOIN station s ON s.station_id = p.station_id "
                "WHERE g.current_status = 'IDLE' "
                "  AND p.acceptance_status = 'PASSED' "
                "  AND p.current_status NOT IN ('FAULT', 'OFFLINE') "
                "  AND s.station_status = 'OPEN' "
                "  AND g.gun_id NOT IN (SELECT gun_id FROM charge_order WHERE status='CHARGING') "
                "ORDER BY g.gun_id LIMIT 1"
            )
        ).one()
        payload = OrderStartIn(gun_id=row[0], station_id=row[1], start_soc=Decimal("30"))
        started = order_dao.start_order(self.db, 9101, payload)
        self.db.commit()

        # 服务层先看枪状态，第二个人会拿到友好的 ValueError
        with self.assertRaises(ValueError):
            order_dao.start_order(self.db, 9102, payload)
        self.db.rollback()

        # 绕过服务层的状态检查直接插一行，才是唯一索引兜底的场景（真并发）
        with self.assertRaises(IntegrityError) as ctx:
            self.db.execute(
                text(
                    "INSERT INTO charge_order "
                    "(order_no, user_id, vehicle_id, gun_id, station_id, start_time, "
                    " start_soc, status, energy_fee, service_fee, discount_amount, "
                    " total_amount, paid_amount) "
                    "VALUES ('RACE0001', 9102, 5002, :g, :s, NOW(), 30, 'CHARGING', 0, 0, 0, 0, 0)"
                ),
                {"g": payload.gun_id, "s": payload.station_id},
            )
        self.assertIn("1062", str(ctx.exception.orig))
        self.db.rollback()

        # 清理本用例开的在充订单，复位枪状态
        order_dao.cancel_order(self.db, started.order_id)

    # ---------- 3.5 RBAC / IDOR 负向 ----------
    def test_driver_cannot_touch_others_order(self) -> None:
        order = self.db.execute(
            text("SELECT * FROM charge_order ORDER BY order_id LIMIT 1")
        ).mappings().one()
        other_driver = CurrentUser(user_id=9999, username="intruder", role=RoleEnum.DRIVER)
        with self.assertRaises(HTTPException) as ctx:
            _check_owner(self.db, order, other_driver, "支付")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_fleet_cannot_touch_other_fleet_order(self) -> None:
        # 种子里的司机多半不挂车队，取第一单即可：fleet 侧拿不到 fleet_id 也必须 403
        order = self.db.execute(
            text("SELECT * FROM charge_order ORDER BY order_id LIMIT 1")
        ).mappings().one()
        fleet_id = self.db.execute(
            text("SELECT fleet_id FROM user_account WHERE user_id = :u"), {"u": order["user_id"]}
        ).scalar_one()
        outsider = CurrentUser(user_id=8888, username="fleet_x", role=RoleEnum.FLEET,
                               fleet_id=(fleet_id or 0) + 99)
        with self.assertRaises(HTTPException) as ctx:
            _check_owner(self.db, order, outsider, "查看")
        self.assertEqual(ctx.exception.status_code, 403)

    # ---------- 4. 结束充电计费 ----------
    def test_finish_order_generates_fee_detail(self) -> None:
        order_id = self.db.execute(
            text("SELECT order_id FROM charge_order WHERE status='CHARGING' ORDER BY order_id LIMIT 1")
        ).scalar_one()
        order = order_dao.finish_order(
            self.db, order_id, OrderFinishIn(kwh=Decimal("30"), end_soc=Decimal("80"))
        )
        self.assertIsNotNone(order)
        self.db.commit()

        fees = self.db.execute(
            text("SELECT COUNT(*), COALESCE(SUM(energy_fee + service_fee), 0) FROM order_fee_detail WHERE order_id=:o"),
            {"o": order_id},
        ).one()
        self.assertGreaterEqual(fees[0], 1)
        self.assertGreater(Decimal(str(fees[1])), Decimal("0"))

        paid = self.db.execute(
            text("SELECT total_amount FROM charge_order WHERE order_id=:o"), {"o": order_id}
        ).scalar_one()
        self.assertGreater(Decimal(str(paid)), Decimal("0"))

    # ---------- 5. 超付守卫 ----------
    def test_overpay_raises_1644(self) -> None:
        order_id = self.db.execute(
            text("SELECT order_id FROM charge_order WHERE status='PAID' ORDER BY order_id LIMIT 1")
        ).scalar_one()
        with self.assertRaises(OperationalError) as ctx:
            order_dao.pay_order(
                self.db,
                order_id,
                PaymentIn(amount=Decimal("999999"), method="WECHAT", txn_no="T2025000001"),
            )
        self.assertIn("1644", str(ctx.exception.orig))
        self.assertIn("超过", str(ctx.exception.orig))
        self.db.rollback()

    # ---------- 6. 月结 + 开票守卫 ----------
    def test_settle_generate_then_invoice_guard(self) -> None:
        month, operator_id = self.db.execute(
            text("SELECT bill_month, operator_id FROM settlement_bill WHERE status <> 'CLOSED' ORDER BY bill_id LIMIT 1")
        ).one()
        created = settle_dao.generate_bill(
            self.db, BillGenerateIn(operator_id=operator_id, bill_month=month, platform_rate=Decimal("0.05"))
        )
        self.db.commit()
        self.assertGreaterEqual(created, 0)

        bill_id, bill_status = self.db.execute(
            text(
                "SELECT bill_id, status FROM settlement_bill "
                "WHERE bill_month=:m AND operator_id=:op AND status <> 'CLOSED' "
                "ORDER BY bill_id LIMIT 1"
            ),
            {"m": month, "op": operator_id},
        ).one()
        if bill_status == "DRAFT":
            settle_dao.set_bill_status(self.db, bill_id, "confirm")

        with self.assertRaises(OperationalError) as ctx:
            settle_dao.issue_invoice(
                self.db, bill_id, InvoiceIn(invoice_no="INV-TEST-0001", amount=Decimal("999999"), invoice_type="NORMAL")
            )
        self.assertIn("1644", str(ctx.exception.orig))
        self.db.rollback()

    # ---------- P0-3: 开单站/桩/枪健康校验与故障离线容错结单/撤单 ----------
    def test_p0_3_start_order_guards_and_unhealthy_release_safe(self) -> None:
        # 1) 枪不属于指定场站
        fac_open = self._create_facility(station_status="OPEN", acceptance_status="PASSED", pile_status="IDLE", gun_status="IDLE")
        with self.assertRaisesRegex(ValueError, "不属于"):
            order_dao.start_order(
                self.db,
                9001,
                OrderStartIn(gun_id=fac_open["gun_id"], station_id=fac_open["station_id"] + 9999, start_soc=Decimal("20")),
            )
        self.db.rollback()

        # 2) 非 OPEN 场站拒绝开单
        fac_maint = self._create_facility(station_status="MAINTENANCE", acceptance_status="PASSED", pile_status="IDLE", gun_status="IDLE")
        with self.assertRaisesRegex(ValueError, "非营业"):
            order_dao.start_order(
                self.db,
                9001,
                OrderStartIn(gun_id=fac_maint["gun_id"], station_id=fac_maint["station_id"], start_soc=Decimal("20")),
            )
        self.db.rollback()

        # 3) 未验收桩拒绝开单
        fac_pending = self._create_facility(station_status="OPEN", acceptance_status="PENDING", pile_status="OFFLINE", gun_status="OFFLINE")
        with self.assertRaisesRegex(ValueError, "验收"):
            order_dao.start_order(
                self.db,
                9001,
                OrderStartIn(gun_id=fac_pending["gun_id"], station_id=fac_pending["station_id"], start_soc=Decimal("20")),
            )
        self.db.rollback()

        # 4) 故障桩拒绝开单
        fac_fault = self._create_facility(station_status="OPEN", acceptance_status="PASSED", pile_status="FAULT", gun_status="FAULT")
        with self.assertRaisesRegex(ValueError, "故障或离线"):
            order_dao.start_order(
                self.db,
                9001,
                OrderStartIn(gun_id=fac_fault["gun_id"], station_id=fac_fault["station_id"], start_soc=Decimal("20")),
            )
        self.db.rollback()

        # 5) 充电途中桩/枪变成 FAULT，结单仍能成功且不触发 45000 死锁，枪状态保持 FAULT
        order1 = order_dao.start_order(
            self.db,
            9001,
            OrderStartIn(gun_id=fac_open["gun_id"], station_id=fac_open["station_id"], start_soc=Decimal("30")),
        )
        self.db.execute(text("UPDATE charging_pile SET current_status = 'FAULT' WHERE pile_id = :pid"), {"pid": fac_open["pile_id"]})
        self.db.execute(text("UPDATE charging_gun SET current_status = 'FAULT' WHERE gun_id = :gid"), {"gid": fac_open["gun_id"]})
        self.db.commit()

        done1 = order_dao.finish_order(self.db, order1.order_id, OrderFinishIn(kwh=Decimal("10.000"), end_soc=Decimal("70")))
        self.assertIsNotNone(done1)
        self.assertEqual(done1.status, "FINISHED")
        gun_st = self.db.execute(text("SELECT current_status FROM charging_gun WHERE gun_id = :gid"), {"gid": fac_open["gun_id"]}).scalar_one()
        self.assertEqual(gun_st, "FAULT")

        # 6) 充电途中桩离线，撤单仍能成功且保持 OFFLINE；健康枪同秒撤单钳位 end_time > start_time 并释放为 IDLE
        fac_offline = self._create_facility(station_status="OPEN", acceptance_status="PASSED", pile_status="IDLE", gun_status="IDLE")
        order2 = order_dao.start_order(
            self.db,
            9002,
            OrderStartIn(gun_id=fac_offline["gun_id"], station_id=fac_offline["station_id"], start_soc=Decimal("20")),
        )
        self.db.execute(text("UPDATE charging_pile SET current_status = 'OFFLINE' WHERE pile_id = :pid"), {"pid": fac_offline["pile_id"]})
        self.db.execute(text("UPDATE charging_gun SET current_status = 'OFFLINE' WHERE gun_id = :gid"), {"gid": fac_offline["gun_id"]})
        self.db.commit()

        cancelled2 = order_dao.cancel_order(self.db, order2.order_id)
        self.assertEqual(cancelled2.status, "CANCELLED")
        self.assertGreater(cancelled2.end_time, cancelled2.start_time)
        gun_st2 = self.db.execute(text("SELECT current_status FROM charging_gun WHERE gun_id = :gid"), {"gid": fac_offline["gun_id"]}).scalar_one()
        self.assertEqual(gun_st2, "OFFLINE")

        fac_cancel_ok = self._create_facility(station_status="OPEN", acceptance_status="PASSED", pile_status="IDLE", gun_status="IDLE")
        order3 = order_dao.start_order(
            self.db,
            9002,
            OrderStartIn(gun_id=fac_cancel_ok["gun_id"], station_id=fac_cancel_ok["station_id"], start_soc=Decimal("25")),
        )
        cancelled3 = order_dao.cancel_order(self.db, order3.order_id)
        self.assertEqual(cancelled3.status, "CANCELLED")
        self.assertGreater(cancelled3.end_time, cancelled3.start_time)
        gun_st3 = self.db.execute(text("SELECT current_status FROM charging_gun WHERE gun_id = :gid"), {"gid": fac_cancel_ok["gun_id"]}).scalar_one()
        self.assertEqual(gun_st3, "IDLE")

    # ---------- P1-5: 枪状态日志由触发器唯一写入 ORDER 来源且不泄漏会话变量 ----------
    def test_p1_5_single_order_status_log_and_vars_cleared(self) -> None:
        fac = self._create_facility()
        gun_id = fac["gun_id"]
        prev_max_log = int(
            self.db.execute(text("SELECT COALESCE(MAX(log_id), 0) FROM device_status_log")).scalar_one()
        )

        order = order_dao.start_order(
            self.db,
            9001,
            OrderStartIn(gun_id=gun_id, station_id=fac["station_id"], start_soc=Decimal("20")),
        )
        start_logs = self.db.execute(
            text(
                "SELECT prev_status, new_status, change_source, todo_status "
                "FROM device_status_log WHERE gun_id = :gid AND log_id > :m ORDER BY log_id"
            ),
            {"gid": gun_id, "m": prev_max_log},
        ).mappings().all()
        self.assertEqual(len(start_logs), 1)
        self.assertEqual(start_logs[0]["prev_status"], "IDLE")
        self.assertEqual(start_logs[0]["new_status"], "OCCUPIED")
        self.assertEqual(start_logs[0]["change_source"], "ORDER")
        self.assertEqual(start_logs[0]["todo_status"], "NONE")

        vars_after_start = self.db.execute(
            text("SELECT @status_change_source, @status_todo_status, @status_change_remark")
        ).one()
        self.assertEqual(tuple(vars_after_start), (None, None, None))

        mid_max_log = int(
            self.db.execute(text("SELECT COALESCE(MAX(log_id), 0) FROM device_status_log")).scalar_one()
        )
        order_dao.finish_order(self.db, order.order_id, OrderFinishIn(kwh=Decimal("8.000"), end_soc=Decimal("60")))
        finish_logs = self.db.execute(
            text(
                "SELECT prev_status, new_status, change_source, todo_status "
                "FROM device_status_log WHERE gun_id = :gid AND log_id > :m ORDER BY log_id"
            ),
            {"gid": gun_id, "m": mid_max_log},
        ).mappings().all()
        self.assertEqual(len(finish_logs), 1)
        self.assertEqual(finish_logs[0]["prev_status"], "OCCUPIED")
        self.assertEqual(finish_logs[0]["new_status"], "IDLE")
        self.assertEqual(finish_logs[0]["change_source"], "ORDER")
        self.assertEqual(finish_logs[0]["todo_status"], "NONE")

        manual_cnt = int(
            self.db.execute(
                text(
                    "SELECT COUNT(*) FROM device_status_log "
                    "WHERE gun_id = :gid AND log_id > :m AND change_source = 'MANUAL'"
                ),
                {"gid": gun_id, "m": prev_max_log},
            ).scalar_one()
        )
        self.assertEqual(manual_cnt, 0)

        vars_after_finish = self.db.execute(
            text("SELECT @status_change_source, @status_todo_status, @status_change_remark")
        ).one()
        self.assertEqual(tuple(vars_after_finish), (None, None, None))

    # ---------- P0-4: 余额支付/退款与车队授信联动 ----------
    def test_p0_4_driver_balance_and_fleet_credit_sync(self) -> None:
        fac = self._create_facility(gun_count=3)
        gun1, gun2, gun3 = fac["gun_ids"]

        # 1) driver_9003 初始余额为 0.00，余额支付被触发器拒绝（1644）
        zero_order = order_dao.start_order(
            self.db, 9003, OrderStartIn(gun_id=gun1, station_id=fac["station_id"], start_soc=Decimal("20"))
        )
        zero_order = order_dao.finish_order(self.db, zero_order.order_id, OrderFinishIn(kwh=Decimal("4.000"), end_soc=Decimal("50")))
        with self.assertRaises(OperationalError) as ctx:
            order_dao.pay_order(
                self.db,
                zero_order.order_id,
                PaymentIn(amount=zero_order.total_amount, method="BALANCE", txn_no=self._tag("TXN")),
            )
        self.assertIn("1644", str(ctx.exception.orig))
        self.assertIn("余额不足", str(ctx.exception.orig))
        self.db.rollback()
        zero_bal = Decimal(str(self.db.execute(text("SELECT balance FROM user_account WHERE user_id = 9003")).scalar_one()))
        self.assertEqual(zero_bal, Decimal("0.00"))

        # 2) driver_9001 余额支付扣款、余额退款回充
        bal_before = Decimal(str(self.db.execute(text("SELECT balance FROM user_account WHERE user_id = 9001")).scalar_one()))
        drv_order = order_dao.start_order(
            self.db, 9001, OrderStartIn(gun_id=gun2, station_id=fac["station_id"], start_soc=Decimal("20"))
        )
        drv_order = order_dao.finish_order(self.db, drv_order.order_id, OrderFinishIn(kwh=Decimal("10.000"), end_soc=Decimal("70")))
        order_dao.pay_order(
            self.db,
            drv_order.order_id,
            PaymentIn(amount=drv_order.total_amount, method="BALANCE", txn_no=self._tag("TXN")),
        )
        bal_after_pay = Decimal(str(self.db.execute(text("SELECT balance FROM user_account WHERE user_id = 9001")).scalar_one()))
        self.assertEqual(bal_after_pay, bal_before - Decimal(str(drv_order.total_amount)))

        order_dao.refund_order(
            self.db,
            drv_order.order_id,
            RefundIn(amount=Decimal("-2.00"), method="BALANCE", txn_no=self._tag("REF")),
        )
        bal_after_ref = Decimal(str(self.db.execute(text("SELECT balance FROM user_account WHERE user_id = 9001")).scalar_one()))
        self.assertEqual(bal_after_ref, bal_after_pay + Decimal("2.00"))

        # 3) fleet_9101（fleet_id=1 ACTIVE）余额支付累加 credit_used，退款扣减 credit_used
        used_before = Decimal(str(self.db.execute(text("SELECT credit_used FROM fleet_account WHERE fleet_id = 1")).scalar_one()))
        flt_order = order_dao.start_order(
            self.db, 9101, OrderStartIn(gun_id=gun3, station_id=fac["station_id"], start_soc=Decimal("20"))
        )
        flt_order = order_dao.finish_order(self.db, flt_order.order_id, OrderFinishIn(kwh=Decimal("10.000"), end_soc=Decimal("70")))
        order_dao.pay_order(
            self.db,
            flt_order.order_id,
            PaymentIn(amount=flt_order.total_amount, method="BALANCE", txn_no=self._tag("TXN")),
        )
        used_after_pay = Decimal(str(self.db.execute(text("SELECT credit_used FROM fleet_account WHERE fleet_id = 1")).scalar_one()))
        self.assertEqual(used_after_pay, used_before + Decimal(str(flt_order.total_amount)))

        order_dao.refund_order(
            self.db,
            flt_order.order_id,
            RefundIn(amount=Decimal("-3.00"), method="BALANCE", txn_no=self._tag("REF")),
        )
        used_after_ref = Decimal(str(self.db.execute(text("SELECT credit_used FROM fleet_account WHERE fleet_id = 1")).scalar_one()))
        self.assertEqual(used_after_ref, used_after_pay - Decimal("3.00"))

        # 4) 暂停状态车队 fleet_9105（fleet_id=5 SUSPENDED）余额支付被触发器拒绝
        susp_order = order_dao.start_order(
            self.db, 9105, OrderStartIn(gun_id=gun3, station_id=fac["station_id"], start_soc=Decimal("20"))
        )
        susp_order = order_dao.finish_order(self.db, susp_order.order_id, OrderFinishIn(kwh=Decimal("6.000"), end_soc=Decimal("60")))
        with self.assertRaises(OperationalError) as ctx2:
            order_dao.pay_order(
                self.db,
                susp_order.order_id,
                PaymentIn(amount=susp_order.total_amount, method="BALANCE", txn_no=self._tag("TXN")),
            )
        self.assertIn("1644", str(ctx2.exception.orig))
        self.db.rollback()

    # ---------- P1-2: 部分退款状态机 + sp_settle_generate 按实收净额汇总且不误报 UNPAID ----------
    def test_p1_2_partial_refund_and_sp_settle_generate_paid_amount(self) -> None:
        fac = self._create_facility(gun_count=3)
        gun1, gun2, gun3 = fac["gun_ids"]
        month = datetime.now().strftime("%Y-%m")

        # 单1: 全付后部分退款 -> 状态保持 PAID，paid_amount 净额 = total - 2.00
        o1 = order_dao.start_order(self.db, 9001, OrderStartIn(gun_id=gun1, station_id=fac["station_id"], start_soc=Decimal("20")))
        o1 = order_dao.finish_order(self.db, o1.order_id, OrderFinishIn(kwh=Decimal("10.000"), end_soc=Decimal("70")))
        order_dao.pay_order(self.db, o1.order_id, PaymentIn(amount=o1.total_amount, method="WECHAT", txn_no=self._tag("TXN")))
        order_dao.refund_order(self.db, o1.order_id, RefundIn(amount=Decimal("-2.00"), method="WECHAT", txn_no=self._tag("REF")))
        self.db.expire_all()
        o1_db = order_dao.get_order(self.db, o1.order_id)
        self.assertEqual(o1_db.status, "PAID")
        self.assertEqual(Decimal(str(o1_db.paid_amount)), Decimal(str(o1.total_amount)) - Decimal("2.00"))

        # 单2: 部分支付后部分退款 -> 保持 FINISHED（不得误标 PAID），全额退完后变为 REFUNDED
        o2 = order_dao.start_order(self.db, 9002, OrderStartIn(gun_id=gun2, station_id=fac["station_id"], start_soc=Decimal("20")))
        o2 = order_dao.finish_order(self.db, o2.order_id, OrderFinishIn(kwh=Decimal("10.000"), end_soc=Decimal("70")))
        order_dao.pay_order(self.db, o2.order_id, PaymentIn(amount=Decimal("5.00"), method="WECHAT", txn_no=self._tag("TXN")))
        order_dao.refund_order(self.db, o2.order_id, RefundIn(amount=Decimal("-2.00"), method="WECHAT", txn_no=self._tag("REF")))
        self.db.expire_all()
        o2_mid = order_dao.get_order(self.db, o2.order_id)
        self.assertEqual(o2_mid.status, "FINISHED")
        self.assertEqual(Decimal(str(o2_mid.paid_amount)), Decimal("3.00"))

        order_dao.refund_order(self.db, o2.order_id, RefundIn(amount=Decimal("-3.00"), method="WECHAT", txn_no=self._tag("REF")))
        self.db.expire_all()
        o2_end = order_dao.get_order(self.db, o2.order_id)
        self.assertEqual(o2_end.status, "REFUNDED")
        self.assertEqual(Decimal(str(o2_end.paid_amount)), Decimal("0.00"))

        # 单3: 已结束完全未支付，应被记为 UNPAID 差异
        o3 = order_dao.start_order(self.db, 9004, OrderStartIn(gun_id=gun3, station_id=fac["station_id"], start_soc=Decimal("20")))
        o3 = order_dao.finish_order(self.db, o3.order_id, OrderFinishIn(kwh=Decimal("10.000"), end_soc=Decimal("70")))

        cnt = settle_dao.generate_bill(
            self.db, BillGenerateIn(operator_id=fac["operator_id"], bill_month=month, platform_rate=Decimal("0.0500"))
        )
        self.assertEqual(cnt, 1)

        bill = self.db.execute(
            text("SELECT bill_id, order_count, total_amount FROM settlement_bill WHERE operator_id = :op AND bill_month = :m"),
            {"op": fac["operator_id"], "m": month},
        ).mappings().one()
        self.assertEqual(bill["order_count"], 1)
        self.assertEqual(Decimal(str(bill["total_amount"])), Decimal(str(o1_db.paid_amount)))

        unpaid_order_ids = self.db.execute(
            text("SELECT order_id FROM reconcile_diff WHERE bill_id = :bid AND diff_type = 'UNPAID'"),
            {"bid": bill["bill_id"]},
        ).scalars().all()
        self.assertEqual(list(unpaid_order_ids), [o3.order_id])

    # ---------- P0-6: 结算单 DRAFT / INVOICED / CLOSED 状态与开票/红冲守卫 ----------
    def test_p0_6_bill_draft_invoiced_red_flush_and_closed_guards(self) -> None:
        fac = self._create_facility()
        self.db.execute(
            text(
                "INSERT INTO settlement_bill "
                "(operator_id, station_id, bill_month, order_count, total_kwh, total_amount, platform_fee, payable_amount, status) "
                "VALUES (:op, :st, '2026-08', 2, 40.000, 100.00, 5.00, 95.00, 'DRAFT')"
            ),
            {"op": fac["operator_id"], "st": fac["station_id"]},
        )
        bill_id = int(self.db.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
        self.db.commit()

        # 1) DRAFT 账单禁止开票（DAO 与 DB 触发器双拦）
        with self.assertRaisesRegex(ValueError, "草稿"):
            settle_dao.issue_invoice(
                self.db, bill_id, InvoiceIn(invoice_no=self._tag("INV"), amount=Decimal("50.00"), invoice_type="NORMAL")
            )
        self.db.rollback()

        with self.assertRaises(OperationalError) as ctx:
            self.db.execute(
                text(
                    "INSERT INTO invoice_record (bill_id, invoice_no, amount, invoice_type, status) "
                    "VALUES (:bid, :ino, 50.00, 'NORMAL', 'ISSUED')"
                ),
                {"bid": bill_id, "ino": self._tag("INV")},
            )
        self.assertIn("1644", str(ctx.exception.orig))
        self.db.rollback()

        # 2) CONFIRMED 开票后自动转 INVOICED；全额红冲后自动回退 CONFIRMED
        confirmed = settle_dao.set_bill_status(self.db, bill_id, "confirm")
        self.assertEqual(confirmed.status, "CONFIRMED")

        inv1 = settle_dao.issue_invoice(
            self.db, bill_id, InvoiceIn(invoice_no=self._tag("INV"), amount=Decimal("95.00"), invoice_type="NORMAL")
        )
        self.db.expire_all()
        self.assertEqual(settle_dao.get_bill(self.db, bill_id).status, "INVOICED")

        flush1 = settle_dao.red_flush_invoice(self.db, inv1.invoice_id, self._tag("RED"), None)
        self.assertEqual(Decimal(str(flush1.amount)), Decimal("-95.00"))
        self.db.expire_all()
        self.assertEqual(settle_dao.get_bill(self.db, bill_id).status, "CONFIRMED")

        # 3) 再次开票后关单 -> CLOSED 禁止再开票与红冲
        inv2 = settle_dao.issue_invoice(
            self.db, bill_id, InvoiceIn(invoice_no=self._tag("INV"), amount=Decimal("40.00"), invoice_type="SPECIAL")
        )
        closed = settle_dao.set_bill_status(self.db, bill_id, "close")
        self.assertEqual(closed.status, "CLOSED")

        with self.assertRaisesRegex(ValueError, "已关单"):
            settle_dao.issue_invoice(
                self.db, bill_id, InvoiceIn(invoice_no=self._tag("INV"), amount=Decimal("10.00"), invoice_type="NORMAL")
            )
        self.db.rollback()

        with self.assertRaisesRegex(ValueError, "已关单"):
            settle_dao.red_flush_invoice(self.db, inv2.invoice_id, self._tag("RED"), None)
        self.db.rollback()

    # ---------- P1-3: OPS 禁止查支付流水 + 未绑定车队禁止落入全量订单 ----------
    def test_p1_3_ops_payments_forbidden_and_fleet_unbound_forbidden(self) -> None:
        paid_order_id = self.db.execute(
            text("SELECT order_id FROM charge_order WHERE status = 'PAID' ORDER BY order_id LIMIT 1")
        ).scalar_one()

        ops_token = create_access_token(CurrentUser(user_id=2, username="wang_ops", role=RoleEnum.OPS))
        status_ops, _ = _asgi_get(
            f"/api/orders/{paid_order_id}/payments",
            headers={"Authorization": f"Bearer {ops_token}"},
        )
        self.assertEqual(status_ops, 403)

        settle_token = create_access_token(CurrentUser(user_id=6, username="zhang_settle", role=RoleEnum.SETTLEMENT))
        status_settle, _ = _asgi_get(
            f"/api/orders/{paid_order_id}/payments",
            headers={"Authorization": f"Bearer {settle_token}"},
        )
        self.assertEqual(status_settle, 200)

        unbound_fleet = CurrentUser(user_id=999999, username="fleet_unbound", role=RoleEnum.FLEET, fleet_id=None)
        unbound_token = create_access_token(unbound_fleet)
        status_list, _ = _asgi_get(
            "/api/orders",
            headers={"Authorization": f"Bearer {unbound_token}"},
        )
        self.assertEqual(status_list, 403)

        order_row = self.db.execute(text("SELECT * FROM charge_order WHERE order_id = :oid"), {"oid": paid_order_id}).mappings().one()
        with self.assertRaises(HTTPException) as ctx:
            _check_owner(self.db, order_row, unbound_fleet, "查看")
        self.assertEqual(ctx.exception.status_code, 403)

    # ---------- P1-4: 调价跨时段与跨零点重叠规则停用 ----------
    def test_p1_4_create_price_rule_cross_period_and_cross_midnight_deactivation(self) -> None:
        fac = self._create_facility()
        sid = fac["station_id"]
        self.db.execute(text("DELETE FROM pricing_rule WHERE station_id = :sid"), {"sid": sid})
        self.db.commit()

        r_flat = order_dao.create_price_rule(
            self.db,
            PriceRuleIn(
                station_id=sid, period="FLAT", start_time="08:00", end_time="10:00",
                energy_price=Decimal("0.6000"), service_price=Decimal("0.3000"),
                effective_from=date(2026, 1, 1), effective_to=None,
            ),
        )
        r_peak = order_dao.create_price_rule(
            self.db,
            PriceRuleIn(
                station_id=sid, period="PEAK", start_time="10:00", end_time="15:00",
                energy_price=Decimal("0.9000"), service_price=Decimal("0.4000"),
                effective_from=date(2026, 1, 1), effective_to=None,
            ),
        )
        r_valley = order_dao.create_price_rule(
            self.db,
            PriceRuleIn(
                station_id=sid, period="VALLEY", start_time="22:00", end_time="08:00",
                energy_price=Decimal("0.3000"), service_price=Decimal("0.2000"),
                effective_from=date(2026, 1, 1), effective_to=None,
            ),
        )
        r_old_year = order_dao.create_price_rule(
            self.db,
            PriceRuleIn(
                station_id=sid, period="PEAK", start_time="09:00", end_time="12:00",
                energy_price=Decimal("0.8500"), service_price=Decimal("0.3500"),
                effective_from=date(2024, 1, 1), effective_to=date(2024, 12, 31),
            ),
        )

        # 跨时段 [09:00, 12:00) (PEAK) 应同时停用 FLAT [08:00, 10:00) 与 PEAK [10:00, 15:00)，
        # 但不影响 2024 失效规则和跨零点 [22:00, 08:00)
        order_dao.create_price_rule(
            self.db,
            PriceRuleIn(
                station_id=sid, period="PEAK", start_time="09:00", end_time="12:00",
                energy_price=Decimal("1.1000"), service_price=Decimal("0.4000"),
                effective_from=date(2026, 6, 1), effective_to=None,
            ),
        )
        self.db.expire_all()
        states = dict(
            self.db.execute(
                text("SELECT rule_id, is_active FROM pricing_rule WHERE station_id = :sid"),
                {"sid": sid},
            ).all()
        )
        self.assertEqual(states[r_flat.rule_id], 0)
        self.assertEqual(states[r_peak.rule_id], 0)
        self.assertEqual(states[r_valley.rule_id], 1)
        self.assertEqual(states[r_old_year.rule_id], 1)

        # 凌晨段 [06:00, 09:00) 应识别并停用跨零点规则 [22:00, 08:00)
        order_dao.create_price_rule(
            self.db,
            PriceRuleIn(
                station_id=sid, period="FLAT", start_time="06:00", end_time="09:00",
                energy_price=Decimal("0.6500"), service_price=Decimal("0.3000"),
                effective_from=date(2026, 6, 1), effective_to=None,
            ),
        )
        valley_active = self.db.execute(
            text("SELECT is_active FROM pricing_rule WHERE rule_id = :rid"),
            {"rid": r_valley.rule_id},
        ).scalar_one()
        self.assertEqual(valley_active, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
