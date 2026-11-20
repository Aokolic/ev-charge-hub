"""全局配置管理模块（全组公共底座）。

职责：
1. 集中调用一次 load_dotenv() 读取项目根目录的 .env 文件；
2. 对环境变量进行强类型转换（bool / int）与必填校验，实现启动期 Fail-Fast；
3. 按 APP_ENV 区分本地开发与生产环境安全策略（非本地环境强制强 SECRET_KEY 并默认关闭 dev-token）；
4. 导出只读配置单例 settings，供 db.py / auth.py / main.py 等模块统一导入。
"""

import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# 全项目唯一一次加载 .env 文件（若环境变量已由系统注入，默认不覆盖）
load_dotenv()

logger = logging.getLogger(__name__)

_LOCAL_ENVS = frozenset({"dev", "development", "local", "test"})
_DEFAULT_DEV_SECRET = "dev-secret-change-in-production"
_PLACEHOLDER_SECRETS = frozenset(
    {
        _DEFAULT_DEV_SECRET,
        "replace-with-a-random-secret-key",
    }
)
_MIN_PROD_SECRET_LEN = 16


def _require(name: str) -> str:
    """读取必填环境变量，未配置或仅为空白字符时立即抛出异常。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"必填环境变量 {name} 未设置或为空，请检查根目录 .env 文件。")
    return value.strip()


def _get_bool(name: str, default: bool = False) -> bool:
    """将环境变量安全解析为布尔值（规避 bool('false') == True 的陷阱）。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    """将环境变量解析为整数，格式非法时给出明确的变量名提示。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"环境变量 {name}={raw!r} 不是合法整数。") from exc


def _get_positive_int(name: str, default: int) -> int:
    """将环境变量解析为正整数（> 0），防止超时或阈值配置为 0/负数引发逻辑异常。"""
    value = _get_int(name, default)
    if value <= 0:
        raise ValueError(f"环境变量 {name}={value} 必须为大于 0 的正整数。")
    return value


def _get_app_env() -> str:
    """读取运行环境标识 APP_ENV，未配置或为空白时默认为 development。"""
    raw = os.getenv("APP_ENV")
    if raw is None or not raw.strip():
        return "development"
    return raw.strip().lower()


def _is_local_env(app_env: str) -> bool:
    """判断给定环境名是否属于本地开发/测试环境。"""
    return app_env.strip().lower() in _LOCAL_ENVS


def _validate_non_local_secret(secret_key: str, app_env: str) -> None:
    """校验非本地环境密钥：拒绝空串、默认占位串及长度不足 16 字符的弱密钥。"""
    cleaned = secret_key.strip()
    if not cleaned:
        raise ValueError(f"非本地环境 APP_ENV={app_env!r} 必须显式配置非空 SECRET_KEY。")
    if cleaned in _PLACEHOLDER_SECRETS:
        raise ValueError(f"非本地环境 APP_ENV={app_env!r} 禁止使用默认或占位 SECRET_KEY。")
    if len(cleaned) < _MIN_PROD_SECRET_LEN:
        raise ValueError(
            f"非本地环境 APP_ENV={app_env!r} 要求 SECRET_KEY 长度不少于 {_MIN_PROD_SECRET_LEN} 个字符。"
        )


def _get_secret_key(app_env: str | None = None) -> str:
    """读取签名密钥：本地环境未配置时告警并回退开发默认值；非本地环境强制显式配置强密钥。"""
    env = _get_app_env() if app_env is None else app_env.strip().lower()
    raw = os.getenv("SECRET_KEY")
    if _is_local_env(env):
        if raw is None:
            logger.warning(
                "SECRET_KEY 未配置，当前 %s 环境回退本地开发默认密钥（严禁用于生产环境）。",
                env,
            )
            return _DEFAULT_DEV_SECRET
        cleaned = raw.strip()
        if not cleaned:
            raise ValueError("环境变量 SECRET_KEY 已显式设置但为空白，请填写有效密钥或移除该配置项。")
        return cleaned

    if raw is None or not raw.strip():
        raise ValueError(f"非本地环境 APP_ENV={env!r} 必须显式配置 SECRET_KEY。")
    cleaned = raw.strip()
    _validate_non_local_secret(cleaned, env)
    return cleaned


@dataclass(frozen=True)  # frozen=True 保证配置只读，防止运行期被意外修改
class Settings:
    """应用全局只读配置集合。"""

    # 应用基础信息与运行环境
    APP_TITLE: str = field(
        default_factory=lambda: os.getenv("APP_TITLE", "新能源汽车充电桩运营服务平台")
    )
    APP_ENV: str = field(default_factory=_get_app_env)

    # 数据库配置（由 .env 注入，禁止把真实账号密码写死在代码中）
    DATABASE_URL: str = field(default_factory=lambda: _require("DATABASE_URL"))
    SQL_ECHO: bool = field(default_factory=lambda: _get_bool("SQL_ECHO", False))  # 设为 True 可在控制台打印每条执行 SQL

    # 认证与安全策略配置（供 auth.py 使用）
    SECRET_KEY: str = field(default_factory=lambda: _get_secret_key(_get_app_env()))
    ENABLE_DEV_TOKEN: bool = field(
        default_factory=lambda: _get_bool("ENABLE_DEV_TOKEN", _is_local_env(_get_app_env()))
    )  # 本地默认开启便于联调答辩；非本地环境默认关闭
    ACCESS_TOKEN_EXPIRE_MINUTES: int = field(
        default_factory=lambda: _get_positive_int("ACCESS_TOKEN_EXPIRE_MINUTES", 120)
    )
    LOGIN_MAX_FAILS: int = field(
        default_factory=lambda: _get_positive_int("LOGIN_MAX_FAILS", 5)
    )  # 连续登录失败触发锁定的阈值
    LOGIN_LOCK_MINUTES: int = field(
        default_factory=lambda: _get_positive_int("LOGIN_LOCK_MINUTES", 15)
    )  # 账号锁定时长（分钟）

    def __post_init__(self) -> None:
        """兜底校验直接构造 Settings 实例时的密钥、环境安全策略与正整数约束。"""
        if not self.APP_ENV or not self.APP_ENV.strip():
            raise ValueError("APP_ENV 不能为空。")
        if not self.SECRET_KEY or not self.SECRET_KEY.strip():
            raise ValueError("SECRET_KEY 不能为空。")
        if not _is_local_env(self.APP_ENV):
            _validate_non_local_secret(self.SECRET_KEY, self.APP_ENV)
        for field_name in (
            "ACCESS_TOKEN_EXPIRE_MINUTES",
            "LOGIN_MAX_FAILS",
            "LOGIN_LOCK_MINUTES",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} 必须大于 0。")


# 导出全组公用配置单例，其他模块统一使用 `from app.config import settings`
settings = Settings()
