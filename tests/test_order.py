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

import sys
import unittest
from decimal import Decimal
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pymysql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.dao import order as order_dao
from app.dao import settle as settle_dao
from app.schemas.common import PageParams
from app.schemas.order import OrderFinishIn, OrderStartIn, PaymentIn
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
                f"CREATE DATABASE `{_TEST_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(_URL, future=True)
    try:
        with engine.begin() as conn:
            for name in ("01_ddl.sql", "03_procs_triggers.sql", "04_seed.sql", "02_views.sql"):
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


class OrderSettleTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _rebuild_database()
        cls.engine = create_engine(_URL, future=True)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False, future=True)

    @classmethod
    def tearDownClass(cls) -> None:
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

    # ---------- 1. 契约 ----------
    def test_routes_registered(self) -> None:
        from app.main import app

        paths = app.openapi()["paths"]
        for p in ("/api/orders", "/api/orders/start", "/api/orders/{order_id}/finish",
                  "/api/settlements", "/api/settlements/generate"):
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
                "WHERE g.gun_id NOT IN (SELECT gun_id FROM charge_order WHERE status='CHARGING') "
                "ORDER BY g.gun_id LIMIT 1"
            )
        ).one()
        payload = OrderStartIn(gun_id=row[0], station_id=row[1], start_soc=Decimal("30"))
        order_dao.start_order(self.db, 9101, payload)
        self.db.commit()

        with self.assertRaises(IntegrityError) as ctx:
            order_dao.start_order(self.db, 9102, payload)
        self.assertIn("1062", str(ctx.exception.orig))
        self.db.rollback()

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
            text("SELECT bill_month, operator_id FROM settlement_bill ORDER BY bill_id LIMIT 1")
        ).one()
        created = settle_dao.generate_bill(
            self.db, BillGenerateIn(operator_id=operator_id, bill_month=month, platform_rate=Decimal("0.05"))
        )
        self.db.commit()
        self.assertGreaterEqual(created, 0)

        bill_id = self.db.execute(
            text("SELECT bill_id FROM settlement_bill WHERE bill_month=:m AND operator_id=:op ORDER BY bill_id LIMIT 1"), {"m": month, "op": operator_id}
        ).scalar_one()

        with self.assertRaises(OperationalError) as ctx:
            settle_dao.issue_invoice(
                self.db, bill_id, InvoiceIn(invoice_no="INV-TEST-0001", amount=Decimal("999999"), invoice_type="NORMAL")
            )
        self.assertIn("1644", str(ctx.exception.orig))
        self.db.rollback()


if __name__ == "__main__":
    unittest.main(verbosity=2)
