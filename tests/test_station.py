"""业务线 1（场站与充电设施）四层代码与真实 MySQL 端到端验收测试。

覆盖范围：
1. OpenAPI 契约与静态路由优先级防遮蔽校验；
2. 认证与 5 类角色 RBAC 权限守卫校验（401 / 403 / 200）；
3. 5 个 RBAC 隔离视图的 DAO + 路由双层端到端校验（条数、过滤、Decimal 精度）；
4. MySQL 异常转译校验（1062 -> 409，3819 / 1452 / 1644 -> 400 中文透传）；
5. 存储过程（投运、心跳离线巡检）、待办闭环与触发器会话变量无泄漏校验；
6. 资产 CRUD（运营商/场站/桩/枪）与 404 边界校验，并在前后自动复位业务线 1 种子数据。
"""

import asyncio
import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

# 确保直接执行 `python tests/test_station.py` 时也能正确导入顶层 `app` 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.routing import Match
import pymysql

from app.auth import CurrentUser, RoleEnum, create_access_token, get_current_user
from app.config import settings
from app.dao import station as station_dao
from app.db import get_db
from app.main import app, integrity_error_handler, operational_error_handler
from app.routers import station as station_router
from app.schemas.common import PageParams
from app.schemas.station import (
    DeviceStatusUpdateIn,
    GunCreateIn,
    HeartbeatCheckIn,
    OperatorCreateIn,
    OperatorUpdateIn,
    PileCommissionIn,
    PileCreateIn,
    StationCreateIn,
    StationUpdateIn,
    TodoResolveIn,
)

_SQL_DIR = _PROJECT_ROOT / "sql"
_TEST_DB_NAME = "ev_charge_hub_test_station"


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


def _connect_mysql_admin() -> pymysql.connections.Connection:
    """使用配置连接串中的凭据建立管理连接，用于创建与销毁隔离测试库。"""
    url = make_url(settings.DATABASE_URL)
    return pymysql.connect(
        host=url.host or "127.0.0.1",
        port=int(url.port or 3306),
        user=url.username,
        password=url.password or "",
        charset="utf8mb4",
        autocommit=True,
    )


def _rebuild_isolated_test_db() -> None:
    """在独立临时库 ev_charge_hub_test_station 中全量重建并灌入种子数据（不触碰共享库）。"""
    conn = _connect_mysql_admin()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{_TEST_DB_NAME}`")
            cur.execute(
                f"CREATE DATABASE `{_TEST_DB_NAME}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            for fname in ("01_ddl.sql", "02_views.sql", "03_procs_triggers.sql", "04_seed.sql"):
                sql_text = (_SQL_DIR / fname).read_text(encoding="utf-8").replace(
                    "ev_charge_hub", _TEST_DB_NAME
                )
                for stmt in _split_mysql_script(sql_text):
                    cur.execute(stmt)
    finally:
        conn.close()


def _drop_isolated_test_db() -> None:
    """销毁独立临时测试库，确保测试无任何跨库或数据残留影响。"""
    conn = _connect_mysql_admin()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{_TEST_DB_NAME}`")
    finally:
        conn.close()


def _make_user(role: RoleEnum, user_id: int = 100, fleet_id: int | None = None) -> CurrentUser:
    """构造测试用只读认证用户上下文。"""
    return CurrentUser(
        user_id=user_id,
        username=f"test_{role.value}",
        role=role,
        fleet_id=fleet_id,
    )


def _make_dummy_request(method: str = "POST", path: str = "/api/stations") -> Request:
    """构造供全局异常处理器直调使用的 Starlette Request 对象。"""
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
        }
    )


async def _call_asgi_raw(
    method: str,
    path: str,
    query_string: str = "",
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, bytes]:
    """无 httpx 依赖的原生 ASGI 原始字节调度器，完整经过路由、中间件与静态文件挂载。"""
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
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
    return int(start_msg["status"]), resp_bytes


async def _call_asgi(
    method: str,
    path: str,
    query_string: str = "",
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | list[Any]]:
    """无 httpx 依赖的原生 ASGI JSON 请求调度器，完整经过路由、依赖注入、DB 与全局异常处理器。"""
    status_code, resp_bytes = await _call_asgi_raw(
        method, path, query_string=query_string, token=token, payload=payload
    )
    parsed = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
    return status_code, parsed


def _find_station_api_route(path: str, method: str) -> APIRoute:
    """从 FastAPI 应用路由树（含 _IncludedRouter）中定位指定路径与 HTTP 方法对应的 APIRoute。"""
    upper_method = method.upper()
    queue = list(app.routes)
    while queue:
        route = queue.pop(0)
        if isinstance(route, APIRoute) and route.path == path and upper_method in route.methods:
            return route
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            queue.extend(original_router.routes)
    raise AssertionError(f"未在 app.routes 中找到路由: {upper_method} {path}")


def _extract_role_guard(route: APIRoute):
    """提取挂载在路由上的 require_roles 守卫函数（即唯一接收 CurrentUser 的子依赖）。"""
    for dep in route.dependant.dependencies:
        # require_roles 返回的内部函数名为 _guard，且自身依赖 get_current_user
        if dep.call.__name__ == "_guard":
            return dep.call
    raise AssertionError(f"路由 {route.methods} {route.path} 未配置 require_roles 权限守卫")


class StationLine1VerificationTest(unittest.TestCase):
    """业务线 1 Python 四层代码与隔离 MySQL 测试库端到端验收测试集。"""

    db: Session
    _test_engine: Any
    _TestSessionLocal: sessionmaker[Session]

    @classmethod
    def setUpClass(cls) -> None:
        # 在独立临时库构建 schema + 视图 + 存储过程 + 种子，绝不修改共享开发库
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
        self.db.close()

    # ------------------------------------------------------------------------
    # 1. OpenAPI 生成与静态路径防遮蔽验证
    # ------------------------------------------------------------------------

    def test_01_openapi_and_route_precedence(self) -> None:
        """验证 OpenAPI 规范完整导出 auth 与 station 全部路径，且静态前缀未被 /{station_id} 遮蔽。"""
        spec = app.openapi()
        paths = spec["paths"]

        self.assertIn("/api/auth/login", paths)
        self.assertIn("/api/auth/register", paths)
        self.assertIn("/api/auth/me", paths)
        self.assertIn("/api/auth/roles", paths)
        self.assertIn("/api/auth/dev-token", paths)

        expected_station_paths = {
            "/api/stations/ping": {"get"},
            "/api/stations/overview": {"get"},
            "/api/stations/guns/availability": {"get"},
            "/api/stations/faults": {"get"},
            "/api/stations/status-logs": {"get"},
            "/api/stations/realtime-status": {"get"},
            "/api/stations/status-logs/{log_id}/resolve-todo": {"post"},
            "/api/stations/piles/heartbeat-check": {"post"},
            "/api/stations/piles/{pile_id}/commission": {"post"},
            "/api/stations/piles/{pile_id}/heartbeat": {"post"},
            "/api/stations/piles/{pile_id}/status": {"patch"},
            "/api/stations/guns/{gun_id}/status": {"patch"},
            "/api/stations/operators": {"get", "post"},
            "/api/stations/operators/summary": {"get"},
            "/api/stations/operators/{operator_id}": {"patch"},
            "/api/stations": {"post"},
            "/api/stations/piles": {"post"},
            "/api/stations/piles/{pile_id}/guns": {"get"},
            "/api/stations/guns": {"post"},
            "/api/stations/{station_id}": {"patch"},
            "/api/stations/{station_id}/piles": {"get"},
        }
        for path, methods in expected_station_paths.items():
            self.assertIn(path, paths, f"OpenAPI 缺失路径: {path}")
            self.assertTrue(
                methods.issubset(set(paths[path].keys())),
                f"路径 {path} 缺失 HTTP 方法: {methods - set(paths[path].keys())}",
            )

        # 验证真实请求路径匹配优先级（重点排查 /piles/...、/guns/...、/operators 未被 /{station_id} 遮蔽）
        route_match_cases = [
            ("GET", "/api/stations/ping", "ping_station"),
            ("GET", "/api/stations/operators", "list_operators"),
            ("GET", "/api/stations/operators/summary", "list_operator_summaries"),
            ("POST", "/api/stations/operators", "create_operator"),
            ("PATCH", "/api/stations/operators/1", "update_operator"),
            ("POST", "/api/stations", "create_station"),
            ("POST", "/api/stations/piles", "create_pile"),
            ("POST", "/api/stations/piles/heartbeat-check", "check_heartbeat_offline"),
            ("POST", "/api/stations/piles/21/commission", "commission_pile"),
            ("POST", "/api/stations/piles/1/heartbeat", "report_pile_heartbeat"),
            ("PATCH", "/api/stations/piles/1/status", "update_pile_status"),
            ("GET", "/api/stations/piles/1/guns", "list_guns_by_pile"),
            ("POST", "/api/stations/guns", "create_gun"),
            ("GET", "/api/stations/guns/availability", "list_driver_guns"),
            ("PATCH", "/api/stations/guns/1/status", "update_gun_status"),
            ("PATCH", "/api/stations/1", "update_station"),
            ("GET", "/api/stations/1/piles", "list_piles_by_station"),
        ]
        for method, raw_path, expected_endpoint_name in route_match_cases:
            scope = {"type": "http", "method": method, "path": raw_path, "root_path": ""}
            matched_endpoint = None
            for route in station_router.router.routes:
                match, _ = route.matches(scope)
                if match == Match.FULL:
                    matched_endpoint = getattr(route, "endpoint", None)
                    break
            self.assertIsNotNone(matched_endpoint, f"未匹配到路由: {method} {raw_path}")
            self.assertEqual(
                matched_endpoint.__name__,
                expected_endpoint_name,
                f"路由 {method} {raw_path} 被错误路由遮蔽，命中 {matched_endpoint.__name__}",
            )

        ping_route = _find_station_api_route("/api/stations/ping", "GET")
        self.assertIsNotNone(ping_route.response_model)

        # 同时通过完整 ASGI 调度验证静态前缀与路径参数路由均无遮蔽（404 来自具体业务逻辑而非错配）
        admin_token = create_access_token(_make_user(RoleEnum.ADMIN, user_id=299))
        st_op, body_op = asyncio.run(_call_asgi("GET", "/api/stations/operators", token=admin_token))
        self.assertEqual(st_op, 200)
        self.assertIsInstance(body_op, list)
        st_guns, body_guns = asyncio.run(
            _call_asgi("GET", "/api/stations/piles/1/guns", token=admin_token)
        )
        self.assertEqual(st_guns, 200)
        self.assertEqual(len(body_guns), 2)

    # ------------------------------------------------------------------------
    # 2. RBAC 权限守卫验证（401 / 403 / 200）
    # ------------------------------------------------------------------------

    def test_02_rbac_guards_and_asgi_auth(self) -> None:
        """验证未认证抛 401、越权角色调用路由守卫抛 403、合法角色放行。"""
        # 2.1 get_current_user(None) -> 401
        with self.assertRaises(HTTPException) as ctx_none:
            get_current_user(None)
        self.assertEqual(ctx_none.exception.status_code, 401)

        # 非法 Bearer Token -> 401
        with self.assertRaises(HTTPException) as ctx_bad:
            get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid.jwt.token"))
        self.assertEqual(ctx_bad.exception.status_code, 401)

        # 合法 Token 解析还原 CurrentUser
        driver_user = _make_user(RoleEnum.DRIVER, user_id=201)
        driver_token = create_access_token(driver_user)
        parsed_driver = get_current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=driver_token)
        )
        self.assertEqual(parsed_driver, driver_user)

        ops_user = _make_user(RoleEnum.OPS, user_id=202)
        admin_user = _make_user(RoleEnum.ADMIN, user_id=203)
        settlement_user = _make_user(RoleEnum.SETTLEMENT, user_id=204)
        fleet_user = _make_user(RoleEnum.FLEET, user_id=205, fleet_id=1)

        # 2.2 检验路由端点真实绑定的 require_roles 守卫
        faults_guard = _extract_role_guard(_find_station_api_route("/api/stations/faults", "GET"))
        commission_guard = _extract_role_guard(
            _find_station_api_route("/api/stations/piles/{pile_id}/commission", "POST")
        )
        create_station_guard = _extract_role_guard(_find_station_api_route("/api/stations", "POST"))
        overview_guard = _extract_role_guard(_find_station_api_route("/api/stations/overview", "GET"))
        realtime_guard = _extract_role_guard(
            _find_station_api_route("/api/stations/realtime-status", "GET")
        )
        list_ops_guard = _extract_role_guard(_find_station_api_route("/api/stations/operators", "GET"))
        ops_summary_guard = _extract_role_guard(
            _find_station_api_route("/api/stations/operators/summary", "GET")
        )

        # driver 调用高权限路由守卫均抛 403
        for guard in (
            faults_guard,
            commission_guard,
            create_station_guard,
            realtime_guard,
            list_ops_guard,
            ops_summary_guard,
        ):
            with self.assertRaises(HTTPException) as ctx_403:
                guard(user=driver_user)
            self.assertEqual(ctx_403.exception.status_code, 403)

        # ops 不能新建场站（仅 admin）且不能看全平台资产看板（仅 admin / settlement），但可访问故障与投运接口
        for ops_forbidden in (create_station_guard, realtime_guard, list_ops_guard, ops_summary_guard):
            with self.assertRaises(HTTPException) as ctx_ops_403:
                ops_forbidden(user=ops_user)
            self.assertEqual(ctx_ops_403.exception.status_code, 403)

        # settlement 不能查运营商完整档案（含信用代码与明文电话），但可查脱敏摘要与全平台看板
        with self.assertRaises(HTTPException) as ctx_settle_403:
            list_ops_guard(user=settlement_user)
        self.assertEqual(ctx_settle_403.exception.status_code, 403)

        self.assertEqual(faults_guard(user=ops_user), ops_user)
        self.assertEqual(faults_guard(user=admin_user), admin_user)
        self.assertEqual(commission_guard(user=ops_user), ops_user)
        self.assertEqual(commission_guard(user=admin_user), admin_user)
        self.assertEqual(create_station_guard(user=admin_user), admin_user)
        self.assertEqual(list_ops_guard(user=admin_user), admin_user)
        self.assertEqual(overview_guard(user=driver_user), driver_user)
        self.assertEqual(overview_guard(user=fleet_user), fleet_user)
        self.assertEqual(realtime_guard(user=settlement_user), settlement_user)
        self.assertEqual(ops_summary_guard(user=settlement_user), settlement_user)

        # 2.3 通过完整 ASGI 链路验证 401 / 403 / 200
        status_unauth, _ = asyncio.run(_call_asgi("GET", "/api/stations/faults"))
        self.assertEqual(status_unauth, 401)

        status_403, _ = asyncio.run(_call_asgi("GET", "/api/stations/faults", token=driver_token))
        self.assertEqual(status_403, 403)

        ops_token = create_access_token(ops_user)
        status_200, body_200 = asyncio.run(_call_asgi("GET", "/api/stations/faults", token=ops_token))
        self.assertEqual(status_200, 200)
        self.assertEqual(body_200["total"], 7)

    # ------------------------------------------------------------------------
    # 3. 真实 MySQL 数据库端到端验证 5 个 RBAC 视图（DAO + 路由）
    # ------------------------------------------------------------------------

    def test_03_five_rbac_views_end_to_end(self) -> None:
        """端到端校验 5 个 RBAC 隔离视图的条数、筛选条件与 Decimal 精度保持。"""
        driver_user = _make_user(RoleEnum.DRIVER)
        ops_user = _make_user(RoleEnum.OPS)
        settlement_user = _make_user(RoleEnum.SETTLEMENT)

        # 3.1 v_driver_station_overview（16 家营业场站，Decimal 保留，only_idle_fast 生效）
        dao_overview = station_dao.list_driver_stations(self.db, PageParams(page=1, page_size=50))
        self.assertEqual(dao_overview["total"], 16)
        self.assertIsInstance(dao_overview["items"][0]["longitude"], Decimal)
        self.assertIsInstance(dao_overview["items"][0]["latitude"], Decimal)
        self.assertIsInstance(dao_overview["items"][0]["parking_fee_rate"], Decimal)

        route_overview = station_router.list_driver_stations(
            keyword=None,
            only_idle_fast=False,
            page=PageParams(page=1, page_size=50),
            db=self.db,
            user=driver_user,
        )
        self.assertEqual(route_overview.total, 16)
        self.assertEqual(len(route_overview.items), 16)
        self.assertIs(type(route_overview.items[0].longitude), Decimal)
        self.assertIs(type(route_overview.items[0].latitude), Decimal)
        self.assertIs(type(route_overview.items[0].parking_fee_rate), Decimal)

        # only_idle_fast=True 仅保留存在空闲快充枪的营业场站（11 家）
        fast_overview = station_router.list_driver_stations(
            keyword=None,
            only_idle_fast=True,
            page=PageParams(page=1, page_size=50),
            db=self.db,
            user=driver_user,
        )
        self.assertEqual(fast_overview.total, 11)
        self.assertTrue(all(item.idle_fast_gun_count > 0 for item in fast_overview.items))

        # keyword 筛选
        kw_overview = station_router.list_driver_stations(
            keyword="清华东路",
            only_idle_fast=False,
            page=PageParams(page=1, page_size=10),
            db=self.db,
            user=driver_user,
        )
        self.assertEqual(kw_overview.total, 1)
        self.assertEqual(kw_overview.items[0].station_code, "ST-BJ-001")

        # 3.2 v_driver_gun_availability（32 把营业场站已验收非离线/故障桩下属枪）
        guns_page = station_router.list_driver_guns(
            station_id=None,
            gun_type=None,
            gun_status=None,
            page=PageParams(page=1, page_size=50),
            db=self.db,
            user=driver_user,
        )
        self.assertEqual(guns_page.total, 32)
        self.assertIs(type(guns_page.items[0].max_power), Decimal)
        self.assertIs(type(guns_page.items[0].rated_power), Decimal)

        slow_guns = station_router.list_driver_guns(
            station_id=2,
            gun_type="AC_SLOW",
            gun_status="IDLE",
            page=PageParams(page=1, page_size=20),
            db=self.db,
            user=driver_user,
        )
        self.assertEqual(slow_guns.total, 2)
        self.assertTrue(all(item.gun_type == "AC_SLOW" for item in slow_guns.items))

        # 3.3 v_fault_gun_detail（7 条故障枪/离线桩明细）
        faults_page = station_router.list_fault_guns(
            station_id=None,
            pile_status=None,
            page=PageParams(page=1, page_size=20),
            db=self.db,
            user=ops_user,
        )
        self.assertEqual(faults_page.total, 7)
        self.assertIsNotNone(faults_page.items[0].service_phone_masked)
        self.assertIn("****", faults_page.items[0].service_phone_masked)
        self.assertFalse(hasattr(faults_page.items[0], "service_phone"))
        fault_only = station_router.list_fault_guns(
            station_id=3,
            pile_status="FAULT",
            page=PageParams(page=1, page_size=20),
            db=self.db,
            user=ops_user,
        )
        self.assertEqual(fault_only.total, 2)
        self.assertTrue(all(item.pile_status == "FAULT" for item in fault_only.items))

        # 3.4 v_device_status_history（30 条状态变更审计日志）
        history_page = station_router.list_status_history(
            station_id=None,
            device_type=None,
            todo_status=None,
            page=PageParams(page=1, page_size=50),
            db=self.db,
            user=ops_user,
        )
        self.assertEqual(history_page.total, 30)
        pending_history = station_router.list_status_history(
            station_id=None,
            device_type="PILE",
            todo_status="PENDING",
            page=PageParams(page=1, page_size=50),
            db=self.db,
            user=ops_user,
        )
        self.assertGreaterEqual(pending_history.total, 3)
        self.assertTrue(all(item.todo_status == "PENDING" for item in pending_history.items))

        # 3.5 v_station_realtime_status（20 家全量场站，含总功率 Decimal 聚合校验）
        realtime_page = station_router.list_realtime_stations(
            operator_id=None,
            station_status=None,
            page=PageParams(page=1, page_size=50),
            db=self.db,
            user=settlement_user,
        )
        self.assertEqual(realtime_page.total, 20)
        first_station = realtime_page.items[0]
        self.assertEqual(first_station.station_id, 1)
        self.assertIs(type(first_station.parking_fee_rate), Decimal)
        self.assertIs(type(first_station.total_rated_power), Decimal)
        # 1号站有 180kW + 120kW 两台桩，预聚合总功率应精确为 300.00（未被 4 把枪重复累加）
        self.assertEqual(first_station.total_rated_power, Decimal("300.00"))

        closed_stations = station_router.list_realtime_stations(
            operator_id=None,
            station_status="CLOSED",
            page=PageParams(page=1, page_size=20),
            db=self.db,
            user=settlement_user,
        )
        self.assertEqual(closed_stations.total, 2)

    # ------------------------------------------------------------------------
    # 4. MySQL 异常转译测试（1062 / 3819 / 1452 / 1644）
    # ------------------------------------------------------------------------

    def test_04_mysql_exception_translation(self) -> None:
        """验证真实 DAO 抛出的 MySQL 异常经 main.py 处理器翻译为正确 HTTP 状态码与中文提示。"""
        admin_user = _make_user(RoleEnum.ADMIN)
        ops_user = _make_user(RoleEnum.OPS)
        req = _make_dummy_request()

        # 4.1 1062 唯一键冲突（重复插入 ST-BJ-001）-> HTTP 409
        with self.assertRaises(IntegrityError) as ctx_1062:
            station_router.create_station(
                payload=StationCreateIn(
                    operator_id=1,
                    station_code="ST-BJ-001",
                    station_name="重复编码测试站",
                    address="北京市海淀区测试路1号",
                    longitude=Decimal("116.350000"),
                    latitude=Decimal("40.000000"),
                    total_spaces=10,
                    available_spaces=5,
                ),
                db=self.db,
                user=admin_user,
            )
        resp_1062 = asyncio.run(integrity_error_handler(req, ctx_1062.exception))
        body_1062 = json.loads(resp_1062.body.decode("utf-8"))
        self.assertEqual(resp_1062.status_code, 409)
        self.assertEqual(body_1062["code"], 1062)
        self.assertIn("已存在", body_1062["detail"])

        # 4.2 3819 CHECK 约束冲突（available_spaces > total_spaces）-> HTTP 400
        # 注意：PyMySQL 对 MySQL errno 3819 (SQLSTATE HY000) 抛出 OperationalError 而非 IntegrityError
        with self.assertRaises((IntegrityError, OperationalError)) as ctx_3819_station:
            station_router.create_station(
                payload=StationCreateIn(
                    operator_id=1,
                    station_code="ST-TEST-3819A",
                    station_name="车位越界测试站",
                    address="北京市海淀区测试路2号",
                    longitude=Decimal("116.350000"),
                    latitude=Decimal("40.000000"),
                    total_spaces=10,
                    available_spaces=11,
                ),
                db=self.db,
                user=admin_user,
            )
        exc_3819_a = ctx_3819_station.exception
        handler_a = (
            integrity_error_handler
            if isinstance(exc_3819_a, IntegrityError)
            else operational_error_handler
        )
        resp_3819_a = asyncio.run(handler_a(req, exc_3819_a))
        body_3819_a = json.loads(resp_3819_a.body.decode("utf-8"))
        self.assertEqual(resp_3819_a.status_code, 400)
        self.assertEqual(body_3819_a["code"], 3819)
        self.assertIn("校验约束", body_3819_a["detail"])

        # 4.3 3819 CHECK 约束冲突（未验收桩直接置为 IDLE）-> HTTP 400
        with self.assertRaises((IntegrityError, OperationalError)) as ctx_3819_pile:
            station_router.create_pile(
                payload=PileCreateIn(
                    station_id=1,
                    pile_code="PL-TEST-3819B",
                    device_model="TEST-DC120",
                    rated_power=Decimal("120.00"),
                    gun_count=2,
                    warranty_end_date=date(2032, 12, 31),
                    acceptance_status="PENDING",
                    current_status="IDLE",
                ),
                db=self.db,
                user=admin_user,
            )
        exc_3819_b = ctx_3819_pile.exception
        handler_b = (
            integrity_error_handler
            if isinstance(exc_3819_b, IntegrityError)
            else operational_error_handler
        )
        resp_3819_b = asyncio.run(handler_b(req, exc_3819_b))
        body_3819_b = json.loads(resp_3819_b.body.decode("utf-8"))
        self.assertEqual(resp_3819_b.status_code, 400)
        self.assertEqual(body_3819_b["code"], 3819)
        self.assertIn("校验约束", body_3819_b["detail"])

        # 4.4 1452 外键约束冲突（不存在的 operator_id=999999）-> HTTP 400
        with self.assertRaises(IntegrityError) as ctx_1452:
            station_router.create_station(
                payload=StationCreateIn(
                    operator_id=999999,
                    station_code="ST-TEST-1452",
                    station_name="无效运营商测试站",
                    address="北京市海淀区测试路3号",
                    longitude=Decimal("116.350000"),
                    latitude=Decimal("40.000000"),
                    total_spaces=10,
                    available_spaces=5,
                ),
                db=self.db,
                user=admin_user,
            )
        resp_1452 = asyncio.run(integrity_error_handler(req, ctx_1452.exception))
        body_1452 = json.loads(resp_1452.body.decode("utf-8"))
        self.assertEqual(resp_1452.status_code, 400)
        self.assertEqual(body_1452["code"], 1452)
        self.assertIn("不存在", body_1452["detail"])

        # 4.5 1644 触发器 SIGNAL（单枪 max_power > 所属桩 rated_power）-> HTTP 400
        with self.assertRaises(OperationalError) as ctx_1644_gun:
            station_router.create_gun(
                payload=GunCreateIn(
                    pile_id=1,  # rated_power = 180.00
                    gun_code="GN-TEST-1644",
                    gun_seq=2,
                    gun_type="DC_FAST",
                    connector_standard="GB/T 20234.3",
                    max_power=Decimal("200.00"),
                    current_status="OFFLINE",
                ),
                db=self.db,
                user=admin_user,
            )
        resp_1644_gun = asyncio.run(operational_error_handler(req, ctx_1644_gun.exception))
        body_1644_gun = json.loads(resp_1644_gun.body.decode("utf-8"))
        self.assertEqual(resp_1644_gun.status_code, 400)
        self.assertEqual(body_1644_gun["code"], 1644)
        self.assertIn("额定总功率", body_1644_gun["detail"])

        # 4.6 1644 存储过程 SIGNAL（对已投运的 pile_id=1 重复调用 commission_pile）-> HTTP 400
        with self.assertRaises(OperationalError) as ctx_1644_comm:
            station_router.commission_pile(
                pile_id=1,
                payload=PileCommissionIn(),
                db=self.db,
                user=ops_user,
            )
        resp_1644_comm = asyncio.run(operational_error_handler(req, ctx_1644_comm.exception))
        body_1644_comm = json.loads(resp_1644_comm.body.decode("utf-8"))
        self.assertEqual(resp_1644_comm.status_code, 400)
        self.assertEqual(body_1644_comm["code"], 1644)
        self.assertIn("无需重复验收", body_1644_comm["detail"])

        # 4.7 通过完整 ASGI HTTP 链路验证 1062 -> 409 与 3819 -> 400 全局异常拦截
        admin_token = create_access_token(admin_user)
        asgi_1062_status, asgi_1062_body = asyncio.run(
            _call_asgi(
                "POST",
                "/api/stations",
                token=admin_token,
                payload={
                    "operator_id": 1,
                    "station_code": "ST-BJ-001",
                    "station_name": "ASGI重复站",
                    "address": "北京市海淀区测试路10号",
                    "longitude": "116.350000",
                    "latitude": "40.000000",
                    "total_spaces": 10,
                    "available_spaces": 5,
                },
            )
        )
        self.assertEqual(asgi_1062_status, 409)
        self.assertEqual(asgi_1062_body["code"], 1062)

        asgi_3819_status, asgi_3819_body = asyncio.run(
            _call_asgi(
                "POST",
                "/api/stations",
                token=admin_token,
                payload={
                    "operator_id": 1,
                    "station_code": "ST-ASGI-3819",
                    "station_name": "ASGI车位越界站",
                    "address": "北京市海淀区测试路11号",
                    "longitude": "116.350000",
                    "latitude": "40.000000",
                    "total_spaces": 10,
                    "available_spaces": 12,
                },
            )
        )
        self.assertEqual(asgi_3819_status, 400)
        self.assertEqual(asgi_3819_body["code"], 3819)
        self.assertIn("校验约束", asgi_3819_body["detail"])

    # ------------------------------------------------------------------------
    # 5. 存储过程、待办闭环与触发器会话变量无泄漏测试
    # ------------------------------------------------------------------------

    def test_05_procedures_triggers_and_session_var_cleanup(self) -> None:
        """验证投运、心跳离线巡检、待办办结闭环及状态变更会话变量零泄漏。"""
        # 确保隔离测试库从干净且心跳新鲜的基线出发（支持单独运行此用例）
        self.db.close()
        self._test_engine.dispose()
        _rebuild_isolated_test_db()
        self.db = self._TestSessionLocal()
        ops_user = _make_user(RoleEnum.OPS)

        # 5.1 对 pile_id=21（PENDING / OFFLINE）执行验收投运
        pile_21_out = station_router.commission_pile(
            pile_id=21,
            payload=PileCommissionIn(remark="现场铭牌及协议联调验收通过-自动化测试"),
            db=self.db,
            user=ops_user,
        )
        self.assertEqual(pile_21_out.acceptance_status, "PASSED")
        self.assertEqual(pile_21_out.current_status, "IDLE")
        self.assertIsNotNone(pile_21_out.commissioned_date)
        self.assertIsNotNone(pile_21_out.last_heartbeat_at)

        guns_21 = station_router.list_guns_by_pile(pile_id=21, db=self.db, user=ops_user)
        self.assertEqual(len(guns_21), 1)
        self.assertEqual(guns_21[0].gun_id, 38)
        self.assertEqual(guns_21[0].current_status, "IDLE")

        # 5.2 心跳离线巡检：应精准捕获超时桩 15（2把枪）与 17（1把枪）
        hb_res = station_router.check_heartbeat_offline(
            payload=HeartbeatCheckIn(check_time=None),
            db=self.db,
            user=ops_user,
        )
        self.assertEqual(hb_res.affected_piles, 2)
        self.assertEqual(hb_res.affected_guns, 3)

        pile_15 = station_dao.get_pile(self.db, 15)
        pile_17 = station_dao.get_pile(self.db, 17)
        self.assertIsNotNone(pile_15)
        self.assertIsNotNone(pile_17)
        self.assertEqual(pile_15.current_status, "OFFLINE")
        self.assertEqual(pile_17.current_status, "OFFLINE")

        guns_15 = station_dao.list_guns_by_pile(self.db, 15)
        guns_17 = station_dao.list_guns_by_pile(self.db, 17)
        self.assertTrue(all(g.current_status == "OFFLINE" for g in guns_15 + guns_17))

        # 核查刚刚生成的 HEARTBEAT + PENDING 日志并调用 resolve_status_todo 办结
        pending_hb_logs = self.db.execute(
            text(
                "SELECT log_id FROM device_status_log "
                "WHERE log_id > 30 AND change_source = 'HEARTBEAT' AND todo_status = 'PENDING' "
                "ORDER BY log_id ASC"
            )
        ).scalars().all()
        self.assertEqual(len(pending_hb_logs), 5)

        target_log_id = int(pending_hb_logs[0])
        resolve_res = station_router.resolve_status_todo(
            log_id=target_log_id,
            payload=TodoResolveIn(remark="运维现场排查供电并完成待办"),
            db=self.db,
            user=ops_user,
        )
        self.assertTrue(resolve_res.success)
        self.assertEqual(resolve_res.id, target_log_id)

        resolved_row = self.db.execute(
            text("SELECT todo_status, remark FROM device_status_log WHERE log_id = :lid"),
            {"lid": target_log_id},
        ).mappings().one()
        self.assertEqual(resolved_row["todo_status"], "DONE")
        self.assertEqual(resolved_row["remark"], "运维现场排查供电并完成待办")

        # 负向校验：已 DONE 的待办或 todo_status='NONE' 的日志禁止再次办结（DAO 抛 ValueError -> 路由转 400）
        with self.assertRaises(HTTPException) as ctx_done:
            station_router.resolve_status_todo(
                log_id=target_log_id,
                payload=TodoResolveIn(remark="重复办结测试"),
                db=self.db,
                user=ops_user,
            )
        self.assertEqual(ctx_done.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx_none:
            station_router.resolve_status_todo(
                log_id=1,
                payload=TodoResolveIn(remark="NONE日志办结测试"),
                db=self.db,
                user=ops_user,
            )
        self.assertEqual(ctx_none.exception.status_code, 400)

        # 5.3 验证 update_pile_status / update_gun_status 在变态、同态、异常后会话变量均为 NULL
        def _assert_session_vars_cleared() -> None:
            src, todo, rmk = self.db.execute(
                text(
                    "SELECT @status_change_source, @status_todo_status, @status_change_remark"
                )
            ).one()
            self.assertIsNone(src)
            self.assertIsNone(todo)
            self.assertIsNone(rmk)

        # (a) 桩正常状态变更（1号桩 IDLE -> FAULT）
        pile_1_fault = station_router.update_pile_status(
            pile_id=1,
            payload=DeviceStatusUpdateIn(
                new_status="FAULT",
                change_source="ALARM",
                todo_status="PENDING",
                remark="测试桩故障状态流转",
            ),
            db=self.db,
            user=ops_user,
        )
        self.assertEqual(pile_1_fault.current_status, "FAULT")
        _assert_session_vars_cleared()

        # (b) 桩同状态更新（1号桩 FAULT -> FAULT，触发器不执行清零，靠 DAO finally 清理）
        pile_1_same = station_router.update_pile_status(
            pile_id=1,
            payload=DeviceStatusUpdateIn(
                new_status="FAULT",
                change_source="MANUAL",
                todo_status="DONE",
                remark="同状态重复更新测试",
            ),
            db=self.db,
            user=ops_user,
        )
        self.assertEqual(pile_1_same.current_status, "FAULT")
        _assert_session_vars_cleared()

        # (c) 桩状态变更异常（22号未验收桩尝试置为 IDLE，触发 1644）
        with self.assertRaises(OperationalError):
            station_router.update_pile_status(
                pile_id=22,
                payload=DeviceStatusUpdateIn(
                    new_status="IDLE",
                    change_source="MANUAL",
                    todo_status="NONE",
                    remark="非法状态流转测试",
                ),
                db=self.db,
                user=ops_user,
            )
        _assert_session_vars_cleared()

        # (d) 枪正常状态变更（4号枪 IDLE -> FAULT）
        gun_4_fault = station_router.update_gun_status(
            gun_id=4,
            payload=DeviceStatusUpdateIn(
                new_status="FAULT",
                change_source="ALARM",
                todo_status="PENDING",
                remark="测试枪故障状态流转",
            ),
            db=self.db,
            user=ops_user,
        )
        self.assertEqual(gun_4_fault.current_status, "FAULT")
        _assert_session_vars_cleared()

        # (e) 枪同状态更新（4号枪 FAULT -> FAULT）
        gun_4_same = station_router.update_gun_status(
            gun_id=4,
            payload=DeviceStatusUpdateIn(
                new_status="FAULT",
                change_source="MANUAL",
                todo_status="DONE",
                remark="枪同状态重复更新",
            ),
            db=self.db,
            user=ops_user,
        )
        self.assertEqual(gun_4_same.current_status, "FAULT")
        _assert_session_vars_cleared()

        # (f) 枪状态变更异常（39号枪所属 23号桩处于 OFFLINE，尝试将枪置为 IDLE 触发 1644）
        with self.assertRaises(OperationalError):
            station_router.update_gun_status(
                gun_id=39,
                payload=DeviceStatusUpdateIn(
                    new_status="IDLE",
                    change_source="MANUAL",
                    todo_status="NONE",
                    remark="离线桩下枪非法上线测试",
                ),
                db=self.db,
                user=ops_user,
            )
        _assert_session_vars_cleared()

    # ------------------------------------------------------------------------
    # 6. 资产建档/更新/心跳上报 CRUD 与 404 边界测试
    # ------------------------------------------------------------------------

    def test_06_asset_crud_heartbeat_and_404(self) -> None:
        """验证运营商->场站->桩->枪建档更新、心跳上报与不存在资源 404 响应。"""
        admin_user = _make_user(RoleEnum.ADMIN)
        ops_user = _make_user(RoleEnum.OPS)
        settlement_user = _make_user(RoleEnum.SETTLEMENT)

        # ping 探活（带显式 response_model=StationPingOut）
        self.assertEqual(station_router.ping_station().status, "ready")

        # 运营商脱敏摘要（结算专员）与完整档案（管理员）+ CRUD
        op_summaries = station_router.list_operator_summaries(db=self.db, user=settlement_user)
        self.assertGreaterEqual(len(op_summaries), 20)
        self.assertIn("****", op_summaries[0].service_phone_masked or "")
        self.assertFalse(hasattr(op_summaries[0], "credit_code"))
        self.assertFalse(hasattr(op_summaries[0], "service_phone"))

        operators = station_router.list_operators(db=self.db, user=admin_user)
        self.assertGreaterEqual(len(operators), 20)

        new_op = station_router.create_operator(
            payload=OperatorCreateIn(
                operator_code="OP-TEST-099",
                operator_name="测试新能源运营公司",
                credit_code="91110108MA99TEST99",
                contact_name="测试员",
                service_phone="400-999-0099",
            ),
            db=self.db,
            user=admin_user,
        )
        updated_op = station_router.update_operator(
            operator_id=new_op.operator_id,
            payload=OperatorUpdateIn(contact_name="新联系人", status="SUSPENDED"),
            db=self.db,
            user=admin_user,
        )
        self.assertEqual(updated_op.contact_name, "新联系人")
        self.assertEqual(updated_op.status, "SUSPENDED")

        # 场站 CRUD
        new_st = station_router.create_station(
            payload=StationCreateIn(
                operator_id=new_op.operator_id,
                station_code="ST-TEST-099",
                station_name="测试新建超充站",
                address="北京市海淀区测试大道99号",
                longitude=Decimal("116.361234"),
                latitude=Decimal("40.012345"),
                total_spaces=12,
                available_spaces=10,
                parking_fee_rate=Decimal("5.50"),
            ),
            db=self.db,
            user=admin_user,
        )
        updated_st = station_router.update_station(
            station_id=new_st.station_id,
            payload=StationUpdateIn(available_spaces=8, station_status="MAINTENANCE"),
            db=self.db,
            user=admin_user,
        )
        self.assertEqual(updated_st.available_spaces, 8)
        self.assertEqual(updated_st.station_status, "MAINTENANCE")

        # 桩与枪 CRUD + 明细查询
        new_pile = station_router.create_pile(
            payload=PileCreateIn(
                station_id=new_st.station_id,
                pile_code="PL-TEST-099",
                device_model="TEST-HPC240",
                rated_power=Decimal("240.00"),
                gun_count=2,
                warranty_end_date=date(2032, 12, 31),
                acceptance_status="PENDING",
                current_status="OFFLINE",
            ),
            db=self.db,
            user=admin_user,
        )
        piles = station_router.list_piles_by_station(
            station_id=new_st.station_id, db=self.db, user=ops_user
        )
        self.assertEqual([p.pile_id for p in piles], [new_pile.pile_id])

        new_gun = station_router.create_gun(
            payload=GunCreateIn(
                pile_id=new_pile.pile_id,
                gun_code="GN-TEST-099-A",
                gun_seq=1,
                gun_type="DC_FAST",
                connector_standard="GB/T 20234.3",
                max_power=Decimal("240.00"),
                current_status="OFFLINE",
            ),
            db=self.db,
            user=admin_user,
        )
        guns = station_router.list_guns_by_pile(
            pile_id=new_pile.pile_id, db=self.db, user=ops_user
        )
        self.assertEqual([g.gun_id for g in guns], [new_gun.gun_id])

        # 心跳上报刷新 last_heartbeat_at
        hb_pile = station_router.report_pile_heartbeat(
            pile_id=new_pile.pile_id, db=self.db, user=ops_user
        )
        self.assertIsNotNone(hb_pile.last_heartbeat_at)

        # 不存在主键统一返回 404（其中 commission_pile 由存储过程抛出 1644 -> 400 '指定充电桩不存在'）
        not_found_calls = [
            lambda: station_router.update_operator(
                999999, OperatorUpdateIn(contact_name="x"), db=self.db, user=admin_user
            ),
            lambda: station_router.update_station(
                999999, StationUpdateIn(station_name="不存在站"), db=self.db, user=admin_user
            ),
            lambda: station_router.list_piles_by_station(999999, db=self.db, user=ops_user),
            lambda: station_router.list_guns_by_pile(999999, db=self.db, user=ops_user),
            lambda: station_router.report_pile_heartbeat(999999, db=self.db, user=ops_user),
            lambda: station_router.update_pile_status(
                999999, DeviceStatusUpdateIn(new_status="FAULT"), db=self.db, user=ops_user
            ),
            lambda: station_router.update_gun_status(
                999999, DeviceStatusUpdateIn(new_status="FAULT"), db=self.db, user=ops_user
            ),
            lambda: station_router.resolve_status_todo(
                999999, TodoResolveIn(), db=self.db, user=ops_user
            ),
        ]
        for fn in not_found_calls:
            with self.assertRaises(HTTPException) as ctx_404:
                fn()
            self.assertEqual(ctx_404.exception.status_code, 404)

        with self.assertRaises(OperationalError) as ctx_comm_missing:
            station_router.commission_pile(
                999999, PileCommissionIn(), db=self.db, user=ops_user
            )
        resp_missing = asyncio.run(
            operational_error_handler(_make_dummy_request(), ctx_comm_missing.exception)
        )
        self.assertEqual(resp_missing.status_code, 400)
        self.assertIn("不存在", json.loads(resp_missing.body.decode("utf-8"))["detail"])

    def test_07_static_frontend_routes(self) -> None:
        """验证首页 GET / 与静态资源 /static/、styles.css、app.js 可正常访问。"""
        for static_path, marker in (
            ("/", "新能源汽车充电桩运营服务平台"),
            ("/static/", "充电桩运营服务平台"),
            ("/static/styles.css", "@layer reset, base, layout, components, pages, utilities"),
            ("/static/app.js", "/api/stations/overview"),
        ):
            st, raw = asyncio.run(_call_asgi_raw("GET", static_path))
            self.assertEqual(st, 200, f"静态路径 {static_path} 返回状态码 {st}")
            self.assertIn(marker, raw.decode("utf-8"))

        st_img, raw_img = asyncio.run(_call_asgi_raw("GET", "/static/res/brand-logo.jpg"))
        self.assertEqual(st_img, 200)
        self.assertGreater(len(raw_img), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
