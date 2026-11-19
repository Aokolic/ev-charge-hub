"""业务线 4（用户、车辆、企业车队与优惠券）端到端验收测试。

覆盖：
1. OpenAPI 契约：/api/users 及其子路由已挂载；
2. 视图脱敏隔离：v_user_masked_profile 不含手机号密文/盲索引/口令哈希；
3. 一车一主：同一车牌二次绑定触发 1062（uk_vehicle_plate_hash）；
4. 越权解绑：非本人车辆返回 None（不暴露他人车辆是否存在）；
5. 发券限领：同一活动重复发券触发 1644（trg_coupon_issue_guard）；
6. 核销闭环：核销成功后券置 REDEEMED 且订单折扣/应付同步减少；
7. 一单一券：同一订单核销第二张券触发 1062（uk_redeem_order）；
8. 重复核销：同一张券二次核销触发 1644（券状态已非 ISSUED）。

运行：python tests/test_user.py
依赖本机 MySQL 8（连接信息取自 .env 的 DATABASE_URL，仅把库名换成隔离测试库），
会自建并删除 ev_charge_hub_test_user，不污染开发库。
"""

import sys
import unittest
from datetime import date, timedelta
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
from app.dao import user as user_dao
from app.schemas.common import PageParams
from app.schemas.user import (
    CampaignIn,
    CouponIssueIn,
    CouponRedeemIn,
    VehicleBindIn,
)

_SQL_DIR = _PROJECT_ROOT / "sql"
_TEST_DB = "ev_charge_hub_test_user"

# 连接信息统一从项目配置取（.env 或环境变量），只把库名换成隔离测试库
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
    """按 DELIMITER 切分脚本，保证存储过程/触发器能整体执行。"""
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
                    # 库名由测试自己指定：跳过脚本里的 USE 与 CREATE DATABASE
                    if head.startswith("USE ") or head.startswith("CREATE DATABASE"):
                        continue
                    conn.execute(text(stmt))
    finally:
        engine.dispose()


class UserModuleTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _rebuild_database()
        cls.engine = create_engine(_URL, future=True)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False, future=True)
        # 活动编号在整类测试间递增：DAO 会提交事务，逐个用例重置计数器会撞 uk_campaign_code
        cls._campaign_seq = 0

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

    # ---------- 辅助 ----------
    def _new_campaign(self, *, per_user_limit: int = 1, face: str = "5.00") -> int:
        """建一个当场生效的立减券活动，避免依赖种子数据的有效期与限领口径。"""
        type(self)._campaign_seq += 1
        seq = type(self)._campaign_seq
        today = date.today()
        campaign = user_dao.create_campaign(
            self.db,
            CampaignIn(
                campaign_code=f"CP-TEST-{seq:03d}",
                campaign_name="测试立减券",
                coupon_kind="CASH",
                face_value=Decimal(face),
                threshold_amount=Decimal("0.00"),
                applicable_station_id=None,
                valid_from=today - timedelta(days=1),
                valid_to=today + timedelta(days=30),
                issue_total=100,
                per_user_limit=per_user_limit,
                status="ACTIVE",
            ),
            created_by=1,
        )
        return campaign.campaign_id

    def _free_paid_order(self) -> tuple[int, int, Decimal]:
        """取第一张“已支付且尚未核销过任何券”的订单（连同车主与金额）。

        种子数据已经为 10 张订单各核销过一张券（一单一券），若测试复用这些订单，
        第二张券必然撞 uk_redeem_order，所以这里只挑没有核销记录的空闲订单。
        """
        row = self.db.execute(
            text(
                "SELECT o.order_id, o.user_id, o.total_amount "
                "FROM charge_order AS o "
                "LEFT JOIN coupon_redeem_record AS d ON d.order_id = o.order_id "
                "WHERE o.status = 'PAID' AND o.total_amount > 0.00 AND d.order_id IS NULL "
                "ORDER BY o.order_id LIMIT 1"
            )
        ).one()
        return int(row[0]), int(row[1]), Decimal(str(row[2]))

    # ---------- 1. 契约 ----------
    def test_routes_registered(self) -> None:
        from app.main import app

        paths = app.openapi()["paths"]
        for p in (
            "/api/users",
            "/api/users/me/profile",
            "/api/users/vehicles",
            "/api/users/fleets/credit",
            "/api/users/campaigns",
            "/api/users/coupons/mine",
            "/api/users/coupons/redeem",
            "/api/users/redeems",
            "/api/users/{user_id}",
        ):
            self.assertIn(p, paths)

    # ---------- 2. 视图脱敏 ----------
    def test_masked_view_hides_sensitive_columns(self) -> None:
        cols = set(
            self.db.execute(text("SELECT * FROM v_user_masked_profile LIMIT 1")).keys()
        )
        for forbidden in ("password_hash", "phone_cipher", "phone_hash"):
            self.assertNotIn(forbidden, cols)
        self.assertIn("phone_masked", cols)

    def test_list_users_pagination(self) -> None:
        result = user_dao.list_users(self.db, PageParams(page=1, page_size=10))
        self.assertGreaterEqual(result["total"], 20)
        self.assertEqual(len(result["items"]), 10)
        # 种子账号 9001~9020 与业务线2 订单车主对齐，必须存在
        row = user_dao.get_user_masked(self.db, 9001)
        self.assertIsNotNone(row)
        self.assertEqual(row["role"], "driver")
        self.assertRegex(str(row["phone_masked"]), r"^\d{3}\*{4}\d{4}$")

    # ---------- 3. 一车一主 ----------
    def test_duplicate_plate_raises_1062(self) -> None:
        plate = "京AZ98765"
        payload = VehicleBindIn(
            plate_no=plate,
            plate_color="GREEN",
            vehicle_model="测试车型",
            battery_capacity_kwh=Decimal("60.00"),
            max_charge_power=Decimal("120.00"),
            supports_fast_charge=True,
        )
        user_dao.bind_vehicle(self.db, 9001, payload)
        self.db.commit()

        with self.assertRaises(IntegrityError) as ctx:
            user_dao.bind_vehicle(self.db, 9002, payload)
        self.assertIn("1062", str(ctx.exception.orig))
        self.db.rollback()

    def test_unbind_other_users_vehicle_returns_none(self) -> None:
        # 种子车辆 5001 属于车主 9001
        self.assertIsNone(user_dao.unbind_vehicle(self.db, 5001, user_id=9002))
        self.assertIsNotNone(user_dao.unbind_vehicle(self.db, 5001, user_id=9001))
        self.db.commit()

    # ---------- 4. 发券限领 ----------
    def test_issue_respects_per_user_limit(self) -> None:
        campaign_id = self._new_campaign(per_user_limit=1)
        payload = CouponIssueIn(user_id=9001, count=1, channel="OPS_GRANT")
        result = user_dao.issue_coupons(self.db, campaign_id, payload)
        self.assertEqual(result["issued_count"], 1)
        self.assertEqual(len(result["coupon_nos"]), 1)

        with self.assertRaises(OperationalError) as ctx:
            user_dao.issue_coupons(self.db, campaign_id, payload)
        self.assertIn("1644", str(ctx.exception.orig))
        self.assertIn("限领", str(ctx.exception.orig))
        self.db.rollback()

    # ---------- 5. 核销闭环 ----------
    def test_redeem_updates_coupon_and_order(self) -> None:
        order_id, owner_id, total_before = self._free_paid_order()
        campaign_id = self._new_campaign()
        issued = user_dao.issue_coupons(
            self.db, campaign_id, CouponIssueIn(user_id=owner_id, count=1, channel="ACTIVITY")
        )
        coupon_no = issued["coupon_nos"][0]
        discount_before = Decimal(
            str(
                self.db.execute(
                    text("SELECT discount_amount FROM charge_order WHERE order_id = :o"),
                    {"o": order_id},
                ).scalar_one()
            )
        )

        out = user_dao.redeem_coupon(
            self.db, owner_id, CouponRedeemIn(coupon_no=coupon_no, order_id=order_id)
        )
        self.assertEqual(out.face_value, Decimal("5.00"))
        self.assertEqual(out.payable_amount, total_before - Decimal("5.00"))

        coupon_status = self.db.execute(
            text("SELECT status FROM coupon_record WHERE coupon_no = :no"), {"no": coupon_no}
        ).scalar_one()
        self.assertEqual(coupon_status, "REDEEMED")

        row = self.db.execute(
            text(
                "SELECT discount_amount, total_amount FROM charge_order WHERE order_id = :o"
            ),
            {"o": order_id},
        ).one()
        self.assertEqual(Decimal(str(row[0])), discount_before + Decimal("5.00"))
        self.assertEqual(Decimal(str(row[1])), total_before - Decimal("5.00"))

        redeem_cnt = self.db.execute(
            text("SELECT COUNT(*) FROM coupon_redeem_record WHERE coupon_id = ("
                 "SELECT coupon_id FROM coupon_record WHERE coupon_no = :no)"),
            {"no": coupon_no},
        ).scalar_one()
        self.assertEqual(int(redeem_cnt), 1)

    # ---------- 6. 一单一券 / 重复核销 ----------
    def test_second_coupon_on_same_order_raises_1062(self) -> None:
        order_id, owner_id, _ = self._free_paid_order()
        campaign_id = self._new_campaign(per_user_limit=2)
        issued = user_dao.issue_coupons(
            self.db, campaign_id, CouponIssueIn(user_id=owner_id, count=2, channel="ACTIVITY")
        )
        first, second = issued["coupon_nos"]

        user_dao.redeem_coupon(
            self.db, owner_id, CouponRedeemIn(coupon_no=first, order_id=order_id)
        )
        with self.assertRaises(IntegrityError) as ctx:
            user_dao.redeem_coupon(
                self.db, owner_id, CouponRedeemIn(coupon_no=second, order_id=order_id)
            )
        self.assertIn("1062", str(ctx.exception.orig))
        self.db.rollback()

    def test_double_redeem_same_coupon_raises_1644(self) -> None:
        order_id, owner_id, _ = self._free_paid_order()
        campaign_id = self._new_campaign()
        issued = user_dao.issue_coupons(
            self.db, campaign_id, CouponIssueIn(user_id=owner_id, count=1, channel="ACTIVITY")
        )
        coupon_no = issued["coupon_nos"][0]

        user_dao.redeem_coupon(
            self.db, owner_id, CouponRedeemIn(coupon_no=coupon_no, order_id=order_id)
        )
        with self.assertRaises(OperationalError) as ctx:
            user_dao.redeem_coupon(
                self.db, owner_id, CouponRedeemIn(coupon_no=coupon_no, order_id=order_id)
            )
        self.assertIn("1644", str(ctx.exception.orig))
        self.assertIn("重复核销", str(ctx.exception.orig))
        self.db.rollback()

    # ---------- 7. 车队隔离 ----------
    def test_fleet_credit_scoped_by_fleet_id(self) -> None:
        result = user_dao.list_fleet_credit(self.db, PageParams(page=1, page_size=10), fleet_id=1)
        self.assertGreaterEqual(result["total"], 1)
        for item in result["items"]:
            self.assertEqual(item["fleet_id"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
