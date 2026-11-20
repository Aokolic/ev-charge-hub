"""业务线 4 接口层验收测试（HTTP + RBAC + IDOR），针对 Issue #19 的整改项。

覆盖：
1. RBAC：/api/users 仅 admin/settlement；车主查活动内部指标被拒；
2. 活动脱敏：/campaigns/available 不含预算与核销统计，且只返回投放中且在有效期内的活动；
3. 车队隔离：fleet 只能看本企业车辆、不能解绑他企业车辆；
4. 核销拦截：已支付订单不能用券（400），已结束未付订单可用券且应付归零后订单自动 PAID；
5. 车牌复用：解绑后同一车牌可被重新绑定（不再永久锁死）；
6. 台账强类型：/redeems 返回结构完整（走 v_coupon_redeem_ledger）；
7. 参数校验：路径 ID 0 → 422；LIKE 通配符被转义；
8. 发券审计：coupon_record.issued_by 记录发券管理员。

运行：python tests/test_user_api.py
依赖本机 MySQL 8（连接信息取自 .env），会自建并删除库 ev_charge_hub_test_user_api。
"""

import os
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
from sqlalchemy.orm import sessionmaker

from app.config import settings

_SQL_DIR = _PROJECT_ROOT / "sql"
_TEST_DB = "ev_charge_hub_test_user_api"

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
    """按 DELIMITER 切分脚本，保证存储过程/触发器整体执行。"""
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
            for name in ("01_ddl.sql", "03_procs_triggers.sql", "04_seed.sql", "02_views.sql"):
                script = (_SQL_DIR / name).read_text(encoding="utf-8")
                for stmt in _split_statements(script):
                    head = stmt.lstrip().upper()
                    if head.startswith("USE ") or head.startswith("CREATE DATABASE"):
                        continue
                    conn.execute(text(stmt))
    finally:
        engine.dispose()


class UserApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _rebuild_database()
        cls.engine = create_engine(_URL, future=True)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False, future=True)

        # 让 FastAPI 的 get_db() 走测试库：get_db 在运行时读取 app.db.SessionLocal
        from app import db as app_db

        app_db.SessionLocal = cls.Session
        app_db.engine = cls.engine

        from fastapi.testclient import TestClient

        from app.main import app

        cls.client = TestClient(app)

        # 通过 dev-token 端点签发各角色令牌（无需口令）
        cls.tokens = {
            "driver": cls._dev_token(9016, "driver_9016", "driver"),
            "driver2": cls._dev_token(9019, "driver_9019", "driver"),
            "fleet": cls._dev_token(9101, "fleet_9101", "fleet", fleet_id=1),
            "admin": cls._dev_token(1, "admin", "admin"),
            "settlement": cls._dev_token(6, "zhang_settle", "settlement"),
            "ops": cls._dev_token(2, "wang_ops", "ops"),
        }

    @classmethod
    def _dev_token(cls, user_id: int, username: str, role: str, fleet_id: int | None = None) -> str:
        payload = {"user_id": user_id, "username": username, "role": role, "fleet_id": fleet_id}
        response = cls.client.post("/api/auth/dev-token", json=payload)
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

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
    def _headers(self, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[role]}"}

    def _new_active_campaign(self, face: str = "8.00") -> int:
        type(self)._campaign_seq = getattr(type(self), "_campaign_seq", 0) + 1
        seq = type(self)._campaign_seq
        today = date.today()
        response = self.client.post(
            "/api/users/campaigns",
            headers=self._headers("admin"),
            json={
                "campaign_code": f"CP-API-{today.strftime('%m%d')}-{seq:02d}",
                "campaign_name": f"接口测试券 {face}",
                "coupon_kind": "CASH",
                "face_value": face,
                "threshold_amount": "0.00",
                "valid_from": str(today - timedelta(days=1)),
                "valid_to": str(today + timedelta(days=30)),
                "issue_total": 100,
                "per_user_limit": 3,
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return int(response.json()["id"])

    def _issue(self, campaign_id: int, user_id: int) -> str:
        response = self.client.post(
            f"/api/users/campaigns/{campaign_id}/issue",
            headers=self._headers("admin"),
            json={"user_id": user_id, "count": 1, "channel": "OPS_GRANT"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["coupon_nos"][0]

    def _finished_unpaid_order(self, user_id: int, amount: str) -> int:
        """直接造一笔“已结束且未支付”的订单（种子数据里没有该状态，用于核销正向用例）。"""
        row = self.db.execute(
            text(
                "SELECT g.gun_id, p.station_id FROM charging_gun g "
                "JOIN charging_pile p ON p.pile_id = g.pile_id ORDER BY g.gun_id LIMIT 1"
            )
        ).one()
        self.db.execute(
            text(
                "INSERT INTO charge_order "
                "(order_no, user_id, gun_id, station_id, start_time, end_time, kwh, "
                " total_amount, paid_amount, status) "
                "VALUES (:no, :uid, :gid, :sid, NOW() - INTERVAL 1 HOUR, NOW(), 20.000, "
                "        :amt, 0.00, 'FINISHED')"
            ),
            {"no": f"APITEST{user_id}", "uid": user_id, "gid": row[0], "sid": row[1], "amt": amount},
        )
        self.db.commit()
        return int(
            self.db.execute(
                text("SELECT order_id FROM charge_order WHERE user_id = :uid AND status='FINISHED' "
                     "ORDER BY order_id DESC LIMIT 1"),
                {"uid": user_id},
            ).scalar_one()
        )

    # ---------- 1. RBAC ----------
    def test_user_list_rbac(self) -> None:
        for role in ("driver", "fleet", "ops"):
            self.assertEqual(
                self.client.get("/api/users", headers=self._headers(role)).status_code, 403, role
            )
        for role in ("admin", "settlement"):
            self.assertEqual(
                self.client.get("/api/users", headers=self._headers(role)).status_code, 200, role
            )

    # ---------- 2. 活动指标不对车主泄露 ----------
    def test_driver_cannot_read_internal_campaign_metrics(self) -> None:
        response = self.client.get("/api/users/campaigns", headers=self._headers("driver"))
        self.assertEqual(response.status_code, 403, response.text)

    def test_available_campaigns_hide_internal_metrics(self) -> None:
        response = self.client.get("/api/users/campaigns/available", headers=self._headers("driver"))
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertTrue(items, "种子里应当有投放中的活动")
        forbidden = {"issue_total", "issued_count", "redeemed_cnt", "redeemed_amount", "redeem_rate"}
        for item in items:
            self.assertFalse(forbidden & set(item), f"泄露内部指标: {forbidden & set(item)}")
            self.assertLessEqual(item["valid_from"], str(date.today()))
            self.assertGreaterEqual(item["valid_to"], str(date.today()))

    # ---------- 3. 车队隔离 ----------
    def test_fleet_sees_only_own_company_vehicles(self) -> None:
        response = self.client.get("/api/users/vehicles?page_size=100", headers=self._headers("fleet"))
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertTrue(items)
        self.assertEqual({row["fleet_id"] for row in items}, {1})

    def test_fleet_cannot_unbind_other_company_vehicle(self) -> None:
        other = self.db.execute(
            text("SELECT vehicle_id FROM vehicle WHERE fleet_id = 2 AND bind_status='BOUND' LIMIT 1")
        ).scalar_one()
        response = self.client.delete(
            f"/api/users/vehicles/{other}", headers=self._headers("fleet")
        )
        self.assertEqual(response.status_code, 404, response.text)

    # ---------- 4. 核销拦截与归零 ----------
    def test_redeem_on_paid_order_is_rejected(self) -> None:
        paid_order, owner_id = self.db.execute(
            text(
                "SELECT order_id, user_id FROM charge_order "
                "WHERE status = 'PAID' AND paid_amount > 0 ORDER BY order_id LIMIT 1"
            )
        ).one()
        paid_order, owner_id = int(paid_order), int(owner_id)

        campaign_id = self._new_active_campaign()
        coupon_no = self._issue(campaign_id, owner_id)

        before = self.db.execute(
            text("SELECT total_amount, paid_amount, discount_amount FROM charge_order WHERE order_id = :o"),
            {"o": paid_order},
        ).one()

        owner_headers = {"Authorization": f"Bearer {self._dev_token(owner_id, f'driver_{owner_id}', 'driver')}"}
        response = self.client.post(
            "/api/users/coupons/redeem",
            headers=owner_headers,
            json={"coupon_no": coupon_no, "order_id": paid_order},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("仅已结束且未支付", response.json()["detail"])

        # 已支付订单的金额与折扣不得被任何改动
        after = self.db.execute(
            text("SELECT total_amount, paid_amount, discount_amount FROM charge_order WHERE order_id = :o"),
            {"o": paid_order},
        ).one()
        self.assertEqual([str(v) for v in after], [str(v) for v in before])

    def test_redeem_finished_order_zeroes_payable_and_marks_paid(self) -> None:
        order_id = self._finished_unpaid_order(9020, amount=Decimal("8.00"))
        campaign_id = self._new_active_campaign(face="8.00")
        coupon_no = self._issue(campaign_id, 9020)

        response = self.client.post(
            "/api/users/coupons/redeem",
            headers=self._driver_9020_headers(),
            json={"coupon_no": coupon_no, "order_id": order_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(Decimal(body["face_value"]), Decimal("8.00"))
        self.assertEqual(Decimal(body["payable_amount"]), Decimal("0.00"))

        self.db.rollback()  # API 在另一条连接上提交，先结束本会话读快照再校验落库结果
        row = self.db.execute(
            text("SELECT total_amount, status FROM charge_order WHERE order_id = :o"),
            {"o": order_id},
        ).one()
        self.assertEqual(Decimal(str(row[0])), Decimal("0.00"))
        self.assertEqual(row[1], "PAID")

    def _driver_9020_headers(self) -> dict[str, str]:
        token = self._dev_token(9020, "driver_9020", "driver")
        return {"Authorization": f"Bearer {token}"}

    # ---------- 5. 车牌解绑后可重新绑定 ----------
    def test_plate_can_be_rebound_after_unbind(self) -> None:
        plate = "京A91200"
        body = {
            "plate_no": plate,
            "plate_color": "GREEN",
            "vehicle_model": "测试车型",
            "battery_capacity_kwh": "60.00",
            "max_charge_power": "120.00",
            "supports_fast_charge": True,
        }
        first = self.client.post("/api/users/vehicles", headers=self._headers("driver"), json=body)
        self.assertEqual(first.status_code, 201, first.text)
        vehicle_id = first.json()["vehicle_id"]

        bound = self.client.post("/api/users/vehicles", headers=self._headers("driver2"), json=body)
        self.assertEqual(bound.status_code, 409, "在绑状态的车牌不允许他人绑定")

        removed = self.client.delete(
            f"/api/users/vehicles/{vehicle_id}", headers=self._headers("driver")
        )
        self.assertEqual(removed.status_code, 200, removed.text)

        again = self.client.post("/api/users/vehicles", headers=self._headers("driver2"), json=body)
        self.assertEqual(again.status_code, 201, "解绑后同一车牌应可重新绑定")
        self.assertEqual(again.json()["user_id"], 9019)

    # ---------- 6. 台账强类型 ----------
    def test_redeem_ledger_is_typed(self) -> None:
        response = self.client.get("/api/users/redeems?page_size=5", headers=self._headers("admin"))
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertTrue(items)
        for key in ("coupon_no", "station_name", "face_value", "order_amount", "redeemed_at"):
            self.assertIn(key, items[0])

    # ---------- 7. 参数校验 ----------
    def test_path_param_must_be_positive(self) -> None:
        response = self.client.get("/api/users/0", headers=self._headers("admin"))
        self.assertEqual(response.status_code, 422, response.text)

    def test_like_wildcard_is_escaped(self) -> None:
        response = self.client.get("/api/users?keyword=%25", headers=self._headers("admin"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 0, "通配符 % 不应匹配到任何用户")

    # ---------- 8. 发券审计 ----------
    def test_issue_records_operator(self) -> None:
        campaign_id = self._new_active_campaign()
        coupon_no = self._issue(campaign_id, 9017)
        issued_by = self.db.execute(
            text("SELECT issued_by FROM coupon_record WHERE coupon_no = :no"), {"no": coupon_no}
        ).scalar_one()
        self.assertEqual(int(issued_by), 1, "应当记录发券管理员 ID")


if __name__ == "__main__":
    unittest.main(verbosity=2)
