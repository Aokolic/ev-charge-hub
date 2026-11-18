"""FastAPI 应用总入口与公共基础设施底座（全组公用）。

职责：
1. 初始化 FastAPI 实例并配置 CORS 跨域中间件；
2. 注册全局数据库异常处理器（将 MySQL 约束/触发器错误统一翻译为标准 HTTP 响应）；
3. 提供数据库连通性探活接口 GET /health/db；
4. 挂载 static/ 静态前端目录，并作为各业务线 Router 的统一装配点。
"""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db

# 1. 创建 FastAPI 应用实例
app = FastAPI(title=settings.APP_TITLE)

# 开发期跨域配置：前端采用 Bearer Token 鉴权（不依赖 Cookie），allow_credentials=False 配合 "*" 最稳妥
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 2. 全局数据库异常翻译器（统一兜底 MySQL 底层错误，避免各业务路由重复捕获）
def _extract_mysql_error(exc: Exception) -> tuple[int | None, str]:
    """从 SQLAlchemy 异常中提取 PyMySQL 原始错误码 (errno) 与错误描述。"""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ())
    if isinstance(args, tuple) and len(args) >= 2 and isinstance(args[0], int):
        return args[0], str(args[1])
    return None, str(exc)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """拦截完整性约束异常（唯一索引冲突、外键失效、CHECK 约束拦截等）。"""
    errno, msg = _extract_mysql_error(exc)

    if errno == 1062:
        # 唯一键冲突：如并发抢枪下单（active_gun_id 唯一锁）、优惠券重复核销、编码重复
        return JSONResponse(
            status_code=409,
            content={"detail": "操作冲突或数据已存在", "code": 1062},
        )

    if errno in (1451, 1452):
        # 1451: 子表存在关联记录禁止删除/更新；1452: 引用的父表外键 ID 不存在
        return JSONResponse(
            status_code=400,
            content={"detail": "关联数据不存在或仍被引用", "code": errno},
        )

    if errno == 3819:
        # MySQL 8 CHECK 约束失败（如金额/电量非负校验、状态枚举校验）
        return JSONResponse(
            status_code=400,
            content={"detail": "提交数据不满足数据库校验约束", "code": 3819},
        )

    return JSONResponse(
        status_code=400 if errno in (1048, 1406) else 500,
        content={"detail": "数据库约束校验异常", "code": errno or 500},
    )


@app.exception_handler(OperationalError)
async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    """拦截数据库操作异常（重点捕获触发器 SIGNAL SQLSTATE '45000' 主动抛错）。"""
    errno, msg = _extract_mysql_error(exc)

    if errno == 1644:
        # MySQL 自定义异常（触发器/存储过程 SIGNAL 抛出），直接将业务提示透传给前端
        return JSONResponse(
            status_code=400,
            content={"detail": msg, "code": 1644},
        )

    return JSONResponse(
        status_code=500,
        content={"detail": "数据库服务异常，请稍后重试", "code": errno or 500},
    )


# 3. 基础设施健康检查接口
@app.get("/health/db", tags=["系统监控"])
def health_check_db(db: Session = Depends(get_db)) -> dict[str, str | None]:
    """校验 FastAPI -> SQLAlchemy -> MySQL 8 链路与当前连接库名。"""
    version, db_name = db.execute(text("SELECT VERSION(), DATABASE()")).one()
    return {
        "status": "ok",
        "mysql_version": version,
        "database": db_name,
    }


# 4. 静态前端挂载（使用基于 __file__ 的绝对路径，避免工作目录切换导致找不到 static/）
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# 5. 各业务线路由挂载区（后续由组长在此统一引入 auth 及 5 条业务线路由）
# 示例：
# from app.routers import auth, station, order, user, monitor, settle
# app.include_router(auth.router, prefix="/api/auth", tags=["认证鉴权"])

# 业务线 2 / 5（孙墨涵）：前缀已写在 router 内部，这里只做挂载
from app.routers import order as order_routes  # noqa: E402
from app.routers import settle as settle_routes  # noqa: E402

app.include_router(order_routes.router)
app.include_router(settle_routes.router)
