"""公共底座（config / main / dao.common / auth）与站桩补充校验端到端测试。

覆盖范围：
1. config 校验：ENABLE_DEV_TOKEN 开关、_get_secret_key 空串拦截、正整数阈值校验；
2. common 工具：Fernet lru_cache 复用、fetch_view_page 注入/分号/参数冲突拦截与派表表别名兼容；
3. /api/auth/login：正常登录、失败累计、423 锁定、锁定期满后重置计数（无单次复锁）、FROZEN/DISABLED 403；
4. /api/auth/register：driver 201、fleet/ops/settlement/admin 403、重复用户名 409；
5. /api/auth/me：回显库内 phone_masked、合成 dev-token 兜底、冻结账号即时拦截 403；
6. /api/auth/dev-token：关闭开关返回 403、fleet 缺少 fleet_id 返回 400；
7. DataError 异常处理器：全链路 ASGI 与真实 MySQL DataError 返回 HTTP 400；
8. station 补充规则：gun_seq > 8 返回 422、超大 Decimal 返回 422、update_pile_status 级联子枪及多枪变量不丢失、
   check_heartbeat_offline 本地时区对齐。
"""

import asyncio
import dataclasses
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

# 确保直接执行 `python tests/test_common_auth.py` 时也能正确导入顶层 `app` 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pymysql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from app import config as app_config
from app.auth import CurrentUser, RoleEnum, create_access_token
from app.config import Settings, settings
from app.dao import common as common_dao
from app.dao import station as station_dao
from app.db import get_db
from app.main import app, data_error_handler
from app.routers import auth as auth_router
from app.schemas.common import PageParams
from app.schemas.station import DeviceStatusUpdateIn

_SQL_DIR = _PROJECT_ROOT / "sql"
_TEST_DB_NAME = "ev_charge_hub_test_common_auth"


def _split_mysql_script(sql_text: str) -> list[str]:
    """按 DELIMITER 语句拆分可在 PyMySQL 顺序执行的 SQL 语句列表。"""
    delimiter = ";"
    buffer: list[str] = []
    statements: list[str] = []
    for raw_line in sql_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped.upper().startswith("DELIMITER "):
            delimiter = stripped.split(None, 1)[1].strip()
            continue
        buffer.append(raw_line)
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


def _connect_mysql_admin(database: str | None = None) -> pymysql.connections.Connection:
    """使用配置连接串凭据连接 MySQL（支持指定临时隔离库）。"""
    url = make_url(settings.DATABASE_URL)
    kwargs: dict[str, Any] = {
        "host": url.host or "127.0.0.1",
        "port": int(url.port or 3306),
        "user": url.username,
        "password": url.password or "",
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if database is not None:
        kwargs["database"] = database
    return pymysql.connect(**kwargs)


def _rebuild_isolated_test_db() -> None:
    """在独立临时库按 01->02->03->04 顺序重建结构与种子（不触碰共享开发库）。"""
    admin_conn = _connect_mysql_admin()
    try:
        with admin_conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{_TEST_DB_NAME}`")
            cur.execute(
                f"CREATE DATABASE `{_TEST_DB_NAME}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        admin_conn.close()

    db_conn = _connect_mysql_admin(database=_TEST_DB_NAME)
    try:
        with db_conn.cursor() as cur:
            for fname in ("01_ddl.sql", "02_views.sql", "03_procs_triggers.sql", "04_seed.sql"):
                sql_text = (_SQL_DIR / fname).read_text(encoding="utf-8")
                for stmt in _split_mysql_script(sql_text):
                    head = stmt.lstrip().upper()
                    # 跳过 USE / CREATE DATABASE，确保所有对象严格落在临时隔离库内
                    if head.startswith("USE ") or head.startswith("CREATE DATABASE"):
                        continue
                    cur.execute(stmt)
    finally:
        db_conn.close()


def _drop_isolated_test_db() -> None:
    """销毁临时隔离测试库，确保测试结束无残留库表。"""
    conn = _connect_mysql_admin()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{_TEST_DB_NAME}`")
    finally:
        conn.close()


def _make_dummy_request(method: str = "POST", path: str = "/api/test") -> Request:
    """构造供全局异常处理器直接调用的 Starlette Request 对象。"""
    return Request({"type": "http", "method": method, "path": path, "headers": []})


async def _call_asgi(
    method: str,
    path: str,
    query_string: str = "",
    token: str | None = None,
    payload: dict[str, Any] | list[Any] | None = None,
) -> tuple[int, dict[str, Any] | list[Any]]:
    """无 httpx 依赖的原生 ASGI JSON 请求调度器，完整经过路由、依赖注入、DB 与异常处理。"""
    body_bytes = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else b""
    )
    headers: list[tuple[bytes, bytes]] = [(b"host", b"testserver")]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(body_bytes)).encode("ascii")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string.encode("ascii"),
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    sent_messages: list[dict[str, Any]] = []
    body_sent = False
    response_done = asyncio.Event()

    async def receive() -> dict[str, Any]:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        await response_done.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_done.set()

    await app(scope, receive, send)
    start_msg = next(msg for msg in sent_messages if msg["type"] == "http.response.start")
    resp_bytes = b"".join(
        msg.get("body", b"") for msg in sent_messages if msg["type"] == "http.response.body"
    )
    parsed = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
    return int(start_msg["status"]), parsed


class CommonAndAuthUnitTest(unittest.TestCase):
    """不依赖数据库的公共配置与工具单测。"""

    def test_01_config_secret_key_and_positive_ints(self) -> None:
        """校验 _get_secret_key 空串拒绝、默认回退以及正整数参数校验。"""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECRET_KEY", None)
            self.assertEqual(
                app_config._get_secret_key(),
                "dev-secret-change-in-production",
            )

        with patch.dict(os.environ, {"SECRET_KEY": "   "}, clear=False):
            with self.assertRaises(ValueError):
                app_config._get_secret_key()

        with patch.dict(os.environ, {"SECRET_KEY": "  custom-key-123  "}, clear=False):
            self.assertEqual(app_config._get_secret_key(), "custom-key-123")

        with patch.dict(os.environ, {"LOGIN_MAX_FAILS": "0"}, clear=False):
            with self.assertRaises(ValueError):
                app_config._get_positive_int("LOGIN_MAX_FAILS", 5)

        with patch.dict(os.environ, {"LOGIN_LOCK_MINUTES": "-3"}, clear=False):
            with self.assertRaises(ValueError):
                app_config._get_positive_int("LOGIN_LOCK_MINUTES", 15)

        with self.assertRaises(ValueError):
            Settings(
                APP_TITLE="test",
                DATABASE_URL=settings.DATABASE_URL,
                SQL_ECHO=False,
                SECRET_KEY="valid",
                ENABLE_DEV_TOKEN=True,
                ACCESS_TOKEN_EXPIRE_MINUTES=0,
                LOGIN_MAX_FAILS=5,
                LOGIN_LOCK_MINUTES=15,
            )

    def test_03_production_config_requires_strong_secret_and_disables_dev_token(self) -> None:
        """校验 APP_ENV=production 默认关闭 ENABLE_DEV_TOKEN 并拦截缺失/默认/过短 SECRET_KEY。"""
        strong_key = "prod-strong-random-secret-key-9876543210"
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "SECRET_KEY": strong_key},
            clear=False,
        ):
            os.environ.pop("ENABLE_DEV_TOKEN", None)
            prod_settings = Settings()
            self.assertEqual(prod_settings.APP_ENV, "production")
            self.assertFalse(prod_settings.ENABLE_DEV_TOKEN)
            self.assertEqual(prod_settings.SECRET_KEY, strong_key)

        # 缺失、空白、默认占位及过短密钥在非本地环境均必须被拒绝
        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            os.environ.pop("SECRET_KEY", None)
            os.environ.pop("ENABLE_DEV_TOKEN", None)
            with self.assertRaises(ValueError):
                app_config._get_secret_key("production")
            with self.assertRaises(ValueError):
                Settings()

        for bad_secret in (
            "   ",
            "dev-secret-change-in-production",
            "replace-with-a-random-secret-key",
            "too-short-key",
        ):
            with patch.dict(
                os.environ,
                {"APP_ENV": "production", "SECRET_KEY": bad_secret},
                clear=False,
            ):
                with self.assertRaises(ValueError, msg=f"未拦截非法生产密钥: {bad_secret!r}"):
                    app_config._get_secret_key("production")
                with self.assertRaises(ValueError, msg=f"Settings 未拦截非法生产密钥: {bad_secret!r}"):
                    Settings()

        # 直接构造 Settings(APP_ENV="production", ...) 同样受 __post_init__ 兜底校验保护
        with self.assertRaises(ValueError):
            Settings(
                APP_TITLE="prod-test",
                APP_ENV="production",
                DATABASE_URL=settings.DATABASE_URL,
                SQL_ECHO=False,
                SECRET_KEY="dev-secret-change-in-production",
                ENABLE_DEV_TOKEN=False,
                ACCESS_TOKEN_EXPIRE_MINUTES=120,
                LOGIN_MAX_FAILS=5,
                LOGIN_LOCK_MINUTES=15,
            )

    def test_02_fernet_cache_and_fetch_view_page_guards(self) -> None:
        """校验 Fernet 实例按密钥缓存复用，以及 fetch_view_page 防注入校验。"""
        f1 = common_dao._build_fernet("key-alpha")
        f2 = common_dao._build_fernet("key-alpha")
        f3 = common_dao._build_fernet("key-beta")
        self.assertIs(f1, f2)
        self.assertIsNot(f1, f3)

        cipher = common_dao.encrypt_sensitive("13800001234")
        self.assertEqual(common_dao.decrypt_sensitive(cipher), "13800001234")

        # 拒绝 sql 内分号
        with self.assertRaises(ValueError):
            common_dao.fetch_view_page(
                None,  # type: ignore[arg-type]
                "SELECT 1; SELECT 2",
                None,
                PageParams(page=1, page_size=10),
            )

        # 拒绝非法 order_by 注入串
        for bad_order in (
            "user_id; DROP TABLE user_account",
            "user_id DESC UNION SELECT 1",
            "1=1",
            "user_id..",
        ):
            with self.assertRaises(ValueError):
                common_dao.fetch_view_page(
                    None,  # type: ignore[arg-type]
                    "SELECT * FROM v_user_masked_profile",
                    None,
                    PageParams(page=1, page_size=10),
                    order_by=bad_order,
                )

        # 拒绝保留分页参数冲突
        with self.assertRaises(ValueError):
            common_dao.fetch_view_page(
                None,  # type: ignore[arg-type]
                "SELECT * FROM v_user_masked_profile",
                {"_page_limit": 5},
                PageParams(page=1, page_size=10),
            )


class CommonAuthAndStationFixesTest(unittest.TestCase):
    """公共认证、异常处理及站桩边界修复端到端验收测试（独立临时库）。"""

    db: Session
    _test_engine: Any
    _TestSessionLocal: sessionmaker[Session]

    @classmethod
    def setUpClass(cls) -> None:
        _rebuild_isolated_test_db()
        test_url = make_url(settings.DATABASE_URL).set(database=_TEST_DB_NAME)
        cls._test_engine = create_engine(
            test_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
        )
        cls._TestSessionLocal = sessionmaker(
            autoflush=False,
            autocommit=False,
            bind=cls._test_engine,
        )

        def _override_get_db():
            session = cls._TestSessionLocal()
            try:
                yield session
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        cls._prev_get_db_override = app.dependency_overrides.get(get_db)
        app.dependency_overrides[get_db] = _override_get_db

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._prev_get_db_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = cls._prev_get_db_override
        cls._test_engine.dispose()
        _drop_isolated_test_db()

    def setUp(self) -> None:
        self.db = self._TestSessionLocal()

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def test_01_login_lockout_expiry_reset_and_status_gate(self) -> None:
        """验证登录成功、连续失败 423 锁定、锁定过期后重置计数（无单次复锁）与禁用拦截。"""
        # 1.1 正常登录（seed admin / Demo@123456）
        st_ok, body_ok = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/login",
                payload={"username": "admin", "password": "Demo@123456"},
            )
        )
        self.assertEqual(st_ok, 200)
        self.assertIn("access_token", body_ok)
        self.assertEqual(body_ok["user"]["phone_masked"], "138****0001")

        # 1.2 先注册一个独立测试车主用于暴破锁定测试，避免影响其他用例
        st_reg, body_reg = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/register",
                payload={
                    "username": "lock_tester",
                    "password": "PassWord123",
                    "phone": "13900001111",
                    "role": "driver",
                },
            )
        )
        self.assertEqual(st_reg, 201)
        tester_id = int(body_reg["user_id"])

        # 连续 4 次输错密码：第 1~4 次应返回 401，剩余次数递减且未锁定
        for attempt in range(1, settings.LOGIN_MAX_FAILS):
            st_fail, body_fail = asyncio.run(
                _call_asgi(
                    "POST",
                    "/api/auth/login",
                    payload={"username": "lock_tester", "password": "WrongPassword"},
                )
            )
            self.assertEqual(st_fail, 401)
            self.assertIn(f"还可尝试 {settings.LOGIN_MAX_FAILS - attempt} 次", body_fail["detail"])

        # 第 5 次输错触发 423 锁定
        st_lock, body_lock = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/login",
                payload={"username": "lock_tester", "password": "WrongPassword"},
            )
        )
        self.assertEqual(st_lock, 423)
        self.assertIn("锁定", body_lock["detail"])

        # 锁定期内即使输入正确密码也必须被 423 拦截
        st_blocked, _ = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/login",
                payload={"username": "lock_tester", "password": "PassWord123"},
            )
        )
        self.assertEqual(st_blocked, 423)

        # 1.3 模拟锁定已到期（locked_until <= UTC now），再次输错 1 次密码应重置计数为 1 而非单次复锁
        expired_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)
        self.db.execute(
            text(
                "UPDATE user_account SET locked_until = :lu, failed_login_count = :cnt "
                "WHERE user_id = :uid"
            ),
            {"lu": expired_at, "cnt": settings.LOGIN_MAX_FAILS, "uid": tester_id},
        )
        self.db.commit()

        st_after_exp, body_after_exp = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/login",
                payload={"username": "lock_tester", "password": "WrongPassword"},
            )
        )
        self.assertEqual(st_after_exp, 401)
        self.assertIn(f"还可尝试 {settings.LOGIN_MAX_FAILS - 1} 次", body_after_exp["detail"])

        row_after = self.db.execute(
            text(
                "SELECT failed_login_count, locked_until FROM user_account WHERE user_id = :uid"
            ),
            {"uid": tester_id},
        ).mappings().one()
        self.assertEqual(int(row_after["failed_login_count"]), 1)
        self.assertIsNone(row_after["locked_until"])

        # 正确密码登录清零计数（先结束当前会话隐式读快照，再读其他会话已提交的新状态）
        st_recover, _ = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/login",
                payload={"username": "lock_tester", "password": "PassWord123"},
            )
        )
        self.assertEqual(st_recover, 200)
        self.db.rollback()
        row_reset = self.db.execute(
            text("SELECT failed_login_count, locked_until FROM user_account WHERE user_id = :uid"),
            {"uid": tester_id},
        ).mappings().one()
        self.assertEqual(int(row_reset["failed_login_count"]), 0)
        self.assertIsNone(row_reset["locked_until"])

        # 1.4 FROZEN 与 DISABLED 账号均返回 403
        for account_status in ("FROZEN", "DISABLED"):
            self.db.execute(
                text("UPDATE user_account SET status = :st WHERE user_id = :uid"),
                {"st": account_status, "uid": tester_id},
            )
            self.db.commit()
            st_forbidden, body_forbidden = asyncio.run(
                _call_asgi(
                    "POST",
                    "/api/auth/login",
                    payload={"username": "lock_tester", "password": "PassWord123"},
                )
            )
            self.assertEqual(st_forbidden, 403)
            self.assertIn("禁用", body_forbidden["detail"])

    def test_02_register_restricts_to_driver_and_conflicts_on_duplicate(self) -> None:
        """验证自助注册仅允许 driver（强制 fleet_id=None），其余角色含 fleet 返回 403，重名 409。"""
        st_driver, body_driver = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/register",
                payload={
                    "username": "reg_driver_ok",
                    "password": "PassWord123",
                    "phone": "13900002222",
                    "role": "driver",
                    "fleet_id": 1,
                },
            )
        )
        self.assertEqual(st_driver, 201)
        self.assertEqual(body_driver["role"], "driver")
        self.assertIsNone(body_driver["fleet_id"])
        self.assertEqual(body_driver["phone_masked"], "139****2222")

        # 重复用户名返回 409
        st_dup, body_dup = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/register",
                payload={
                    "username": "reg_driver_ok",
                    "password": "PassWord123",
                    "phone": "13900002223",
                    "role": "driver",
                },
            )
        )
        self.assertEqual(st_dup, 409)
        self.assertEqual(body_dup["code"], 1062)

        # fleet（带或不带 fleet_id）、ops、settlement、admin 均必须返回 403（不能是 422）
        forbidden_cases = [
            {"role": "fleet"},
            {"role": "fleet", "fleet_id": 1},
            {"role": "ops"},
            {"role": "settlement"},
            {"role": "admin"},
        ]
        for idx, extra in enumerate(forbidden_cases):
            payload = {
                "username": f"reg_forbid_{idx}",
                "password": "PassWord123",
                "phone": f"1390000330{idx}",
                **extra,
            }
            st_forbid, _ = asyncio.run(_call_asgi("POST", "/api/auth/register", payload=payload))
            self.assertEqual(st_forbid, 403, f"角色越权注册期望 403，实际 {st_forbid}: {extra}")

    def test_03_auth_me_phone_masked_and_frozen_block(self) -> None:
        """验证 /api/auth/me 回显数据库脱敏手机号、兜底合成 dev-token、冻结用户实时返回 403。"""
        # 3.1 真实账号（admin user_id=1）回显 phone_masked
        admin_token = create_access_token(
            CurrentUser(user_id=1, username="admin", role=RoleEnum.ADMIN)
        )
        st_me, body_me = asyncio.run(_call_asgi("GET", "/api/auth/me", token=admin_token))
        self.assertEqual(st_me, 200)
        self.assertEqual(body_me["phone_masked"], "138****0001")

        # 3.2 合成 dev-token 用户 ID（库中无此 ID）正常兜底返回 phone_masked=None
        synth_token = create_access_token(
            CurrentUser(user_id=888888, username="demo_driver", role=RoleEnum.DRIVER)
        )
        st_synth, body_synth = asyncio.run(_call_asgi("GET", "/api/auth/me", token=synth_token))
        self.assertEqual(st_synth, 200)
        self.assertEqual(body_synth["user_id"], 888888)
        self.assertIsNone(body_synth["phone_masked"])

        # 3.3 已签发有效 Token 的用户在库内被冻结后，/api/auth/me 立即返回 403
        st_reg, body_reg = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/register",
                payload={
                    "username": "me_freeze_user",
                    "password": "PassWord123",
                    "phone": "13900004444",
                },
            )
        )
        self.assertEqual(st_reg, 201)
        uid = int(body_reg["user_id"])
        token = create_access_token(
            CurrentUser(user_id=uid, username="me_freeze_user", role=RoleEnum.DRIVER)
        )

        self.db.execute(
            text("UPDATE user_account SET status = 'FROZEN' WHERE user_id = :uid"),
            {"uid": uid},
        )
        self.db.commit()

        st_frozen, body_frozen = asyncio.run(_call_asgi("GET", "/api/auth/me", token=token))
        self.assertEqual(st_frozen, 403)
        self.assertIn("禁用", body_frozen["detail"])

    def test_04_dev_token_gate_and_fleet_validation(self) -> None:
        """验证 ENABLE_DEV_TOKEN=False 时返回 403，且 fleet 缺少 fleet_id 时拒绝签发。"""
        st_missing_fleet, body_missing = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/dev-token",
                payload={"user_id": 9101, "username": "demo_fleet", "role": "fleet"},
            )
        )
        self.assertIn(st_missing_fleet, (400, 422))
        self.assertIn("fleet_id", str(body_missing["detail"]))

        st_fleet_ok, body_fleet_ok = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/dev-token",
                payload={
                    "user_id": 9101,
                    "username": "demo_fleet",
                    "role": "fleet",
                    "fleet_id": 1,
                },
            )
        )
        self.assertEqual(st_fleet_ok, 200)
        self.assertEqual(body_fleet_ok["user"]["fleet_id"], 1)

        disabled_settings = dataclasses.replace(auth_router.settings, ENABLE_DEV_TOKEN=False)
        with patch.object(auth_router, "settings", disabled_settings):
            st_disabled, body_disabled = asyncio.run(
                _call_asgi(
                    "POST",
                    "/api/auth/dev-token",
                    payload={"user_id": 1, "username": "demo_admin", "role": "admin"},
                )
            )
        self.assertEqual(st_disabled, 403)
        self.assertIn("关闭", body_disabled["detail"])

    def test_05_data_error_handler_returns_400_and_fetch_view_page_alias_sort(self) -> None:
        """验证 DataError 映射为 HTTP 400（含 ASGI 链路与真实 MySQL DataError）及带别名的分页排序。"""
        self.assertIn(DataError, app.exception_handlers)
        self.assertIs(app.exception_handlers[DataError], data_error_handler)

        # 5.1 真实 MySQL 触发 DataError (1264 Out of range) 经 data_error_handler 译为 400
        with self.assertRaises(DataError) as ctx_1264:
            self.db.execute(
                text(
                    "INSERT INTO operator (operator_code, operator_name, credit_code, contact_name, service_phone) "
                    "VALUES (:code, '超长测试', '91110108MA00009999', '测试', '13800000000')"
                ),
                {"code": "X" * 64},  # VARCHAR(32) 触发 1406 DataError
            )
        self.db.rollback()
        resp_1406 = asyncio.run(data_error_handler(_make_dummy_request(), ctx_1264.exception))
        body_1406 = json.loads(resp_1406.body.decode("utf-8"))
        self.assertEqual(resp_1406.status_code, 400)
        self.assertEqual(body_1406["code"], 1406)
        self.assertEqual(body_1406["detail"], "提交数据超出字段长度或数值范围")

        # 5.2 完整 ASGI 链路触发真实 MySQL DataError (1264) -> HTTP 400（INT UNSIGNED 溢出）
        admin_token = create_access_token(
            CurrentUser(user_id=1, username="admin", role=RoleEnum.ADMIN)
        )
        st_asgi, body_asgi = asyncio.run(
            _call_asgi(
                "POST",
                "/api/stations",
                token=admin_token,
                payload={
                    "operator_id": 1,
                    "station_code": "ST-DATAERR-1264",
                    "station_name": "数值溢出测试站",
                    "address": "北京市海淀区测试路1264号",
                    "longitude": "116.350000",
                    "latitude": "40.000000",
                    "total_spaces": 5_000_000_000,  # 超出 INT UNSIGNED 上限触发 1264
                    "available_spaces": 5,
                },
            )
        )
        self.assertEqual(st_asgi, 400)
        self.assertEqual(body_asgi["code"], 1264)

        # 5.3 验证 fetch_view_page 包裹 _v_page 后对带表别名的 order_by（如 user_dao.list_redeem_records）依然在 MySQL 下正常执行
        redeem_sql = (
            "SELECT d.redeem_id, d.coupon_id, c.coupon_no, d.order_id, d.user_id, "
            "       d.station_id, d.face_value, d.order_amount, d.redeemed_at "
            "FROM coupon_redeem_record AS d "
            "INNER JOIN coupon_record AS c ON c.coupon_id = d.coupon_id "
            "WHERE 1 = 1"
        )
        page_res = common_dao.fetch_view_page(
            self.db,
            redeem_sql,
            None,
            PageParams(page=1, page_size=5),
            "d.redeemed_at DESC, d.redeem_id DESC",
        )
        self.assertGreaterEqual(page_res["total"], 1)
        self.assertLessEqual(len(page_res["items"]), 5)

    def test_06_station_schema_bounds_cascade_and_local_heartbeat(self) -> None:
        """验证站桩 Schema 精度/枪号拦截（422）、update_pile_status 级联多枪与心跳本地时区对齐。"""
        admin_token = create_access_token(
            CurrentUser(user_id=1, username="admin", role=RoleEnum.ADMIN)
        )
        # 6.1 gun_seq > 8 -> 422
        st_seq, _ = asyncio.run(
            _call_asgi(
                "POST",
                "/api/stations/guns",
                token=admin_token,
                payload={
                    "pile_id": 1,
                    "gun_code": "GN-SEQ-BAD",
                    "gun_seq": 9,
                    "gun_type": "DC_FAST",
                    "connector_standard": "GB/T 20234.3",
                    "max_power": "60.00",
                },
            )
        )
        self.assertEqual(st_seq, 422)

        # 6.2 超出 DECIMAL(8,2) / DECIMAL(9,6) 的入参 -> 422
        st_power, _ = asyncio.run(
            _call_asgi(
                "POST",
                "/api/stations/piles",
                token=admin_token,
                payload={
                    "station_id": 1,
                    "pile_code": "PL-OVERFLOW-1",
                    "device_model": "TEST-MAX",
                    "rated_power": "1000000.00",
                    "gun_count": 2,
                    "warranty_end_date": "2032-12-31",
                },
            )
        )
        self.assertEqual(st_power, 422)

        st_lng, _ = asyncio.run(
            _call_asgi(
                "POST",
                "/api/stations",
                token=admin_token,
                payload={
                    "operator_id": 1,
                    "station_code": "ST-OVERFLOW-1",
                    "station_name": "经度超精度测试站",
                    "address": "北京市海淀区测试路99号",
                    "longitude": "116.1234567",
                    "latitude": "40.000000",
                    "total_spaces": 10,
                    "available_spaces": 5,
                },
            )
        )
        self.assertEqual(st_lng, 422)

        # 6.3 check_heartbeat_offline 传入带本地时区的 check_time（不可误转 UTC 导致滞后 8 小时）
        local_now_aware = datetime.now().astimezone()
        hb_res = station_dao.check_heartbeat_offline(self.db, check_time=local_now_aware)
        self.assertEqual(hb_res["affected_piles"], 2)
        self.assertEqual(hb_res["affected_guns"], 3)

        # 6.4 update_pile_status 将 pile_id=3（名下 gun_id=5,6 均为 IDLE）置为 FAULT，应级联两把枪且日志来源不回退 MANUAL
        max_log_before = int(
            self.db.execute(text("SELECT COALESCE(MAX(log_id), 0) FROM device_status_log")).scalar_one()
        )
        ops_token = create_access_token(
            CurrentUser(user_id=2, username="wang_ops", role=RoleEnum.OPS)
        )
        st_patch, body_patch = asyncio.run(
            _call_asgi(
                "PATCH",
                "/api/stations/piles/3/status",
                token=ops_token,
                payload={
                    "new_status": "FAULT",
                    "change_source": "ALARM",
                    "todo_status": "PENDING",
                    "remark": "整桩主控故障联动级联测试",
                },
            )
        )
        self.assertEqual(st_patch, 200)
        self.assertEqual(body_patch["current_status"], "FAULT")

        # 结束 self.db 之前的隐式读快照，确保读取到 ASGI 事务提交后的级联状态与审计日志
        self.db.rollback()
        guns_pile_3 = station_dao.list_guns_by_pile(self.db, 3)
        self.assertEqual([g.current_status for g in guns_pile_3], ["FAULT", "FAULT"])

        new_logs = self.db.execute(
            text(
                "SELECT device_type, pile_id, gun_id, new_status, change_source, todo_status "
                "FROM device_status_log "
                "WHERE log_id > :lid "
                "ORDER BY log_id ASC"
            ),
            {"lid": max_log_before},
        ).mappings().all()
        self.assertEqual(len(new_logs), 3)
        self.assertTrue(all(row["change_source"] == "ALARM" for row in new_logs))
        self.assertTrue(all(row["todo_status"] == "PENDING" for row in new_logs))

        # pile_id=1（gun_id=1 为 IDLE，gun_id=2 已是 FAULT）置为 OFFLINE，仅 IDLE 枪级联为 OFFLINE，原 FAULT 枪保持 FAULT
        station_dao.update_pile_status(
            self.db,
            1,
            DeviceStatusUpdateIn(
                new_status="OFFLINE",
                change_source="MANUAL",
                todo_status="PENDING",
                remark="桩手动下线保留已故障枪状态",
            ),
        )
        guns_pile_1 = {g.gun_id: g.current_status for g in station_dao.list_guns_by_pile(self.db, 1)}
        self.assertEqual(guns_pile_1[1], "OFFLINE")
        self.assertEqual(guns_pile_1[2], "FAULT")

        # 验证触发器会话变量无残留
        vars_row = self.db.execute(
            text("SELECT @status_change_source, @status_todo_status, @status_change_remark")
        ).one()
        self.assertEqual(tuple(vars_row), (None, None, None))

    def test_07_frozen_user_jwt_blocked_on_business_routes(self) -> None:
        """验证账号被冻结或禁用后，此前已签发的有效 JWT 访问业务路由（/api/stations/overview、/api/orders）立即返回 403。"""
        st_reg, body_reg = asyncio.run(
            _call_asgi(
                "POST",
                "/api/auth/register",
                payload={
                    "username": "biz_freeze_driver",
                    "password": "PassWord123",
                    "phone": "13900005555",
                    "role": "driver",
                },
            )
        )
        self.assertEqual(st_reg, 201)
        uid = int(body_reg["user_id"])
        jwt_token = create_access_token(
            CurrentUser(user_id=uid, username="biz_freeze_driver", role=RoleEnum.DRIVER)
        )

        # 冻结前携带该 JWT 可正常访问业务路由
        st_ok_station, _ = asyncio.run(
            _call_asgi("GET", "/api/stations/overview", token=jwt_token)
        )
        st_ok_orders, _ = asyncio.run(
            _call_asgi("GET", "/api/orders", token=jwt_token)
        )
        self.assertEqual(st_ok_station, 200)
        self.assertEqual(st_ok_orders, 200)

        # 库内冻结/禁用后，同一张未过期 JWT 在业务路由均必须被实时拦截返回 403
        for blocked_status in ("FROZEN", "DISABLED"):
            self.db.execute(
                text("UPDATE user_account SET status = :st WHERE user_id = :uid"),
                {"st": blocked_status, "uid": uid},
            )
            self.db.commit()

            st_blk_station, body_blk_station = asyncio.run(
                _call_asgi("GET", "/api/stations/overview", token=jwt_token)
            )
            st_blk_orders, body_blk_orders = asyncio.run(
                _call_asgi("GET", "/api/orders", token=jwt_token)
            )
            self.assertEqual(st_blk_station, 403, f"{blocked_status} 应拦截 /api/stations/overview")
            self.assertEqual(st_blk_orders, 403, f"{blocked_status} 应拦截 /api/orders")
            self.assertIn("禁用", body_blk_station["detail"])
            self.assertIn("禁用", body_blk_orders["detail"])

        # 合成 dev-token（库中无对应 user_account 行）仍正常放行
        synth_token = create_access_token(
            CurrentUser(user_id=888889, username="demo_synth_driver", role=RoleEnum.DRIVER)
        )
        st_synth_station, _ = asyncio.run(
            _call_asgi("GET", "/api/stations/overview", token=synth_token)
        )
        st_synth_orders, _ = asyncio.run(
            _call_asgi("GET", "/api/orders", token=synth_token)
        )
        self.assertEqual(st_synth_station, 200)
        self.assertEqual(st_synth_orders, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
