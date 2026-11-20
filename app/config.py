"""全局配置管理模块（全组公共底座）。

职责：
1. 集中调用一次 load_dotenv() 读取项目根目录的 .env 文件；
2. 对环境变量进行强类型转换（bool / int）与必填校验，实现启动期 Fail-Fast；
3. 导出只读配置单例 settings，供 db.py / auth.py / main.py 等模块统一导入。
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# 全项目唯一一次加载 .env 文件（若环境变量已由系统注入，默认不覆盖）
load_dotenv()


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


def _get_secret_key() -> str:
    """读取签名密钥：未配置时回退本地开发默认值；显式配置为空白串时拒绝启动。"""
    raw = os.getenv("SECRET_KEY")
    if raw is None:
        return "dev-secret-change-in-production"
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("环境变量 SECRET_KEY 已显式设置但为空白，请填写有效密钥或移除该配置项。")
    return cleaned


@dataclass(frozen=True)  # frozen=True 保证配置只读，防止运行期被意外修改
class Settings:
    """应用全局只读配置集合。"""

    # 应用基础信息
    APP_TITLE: str = os.getenv("APP_TITLE", "新能源汽车充电桩运营服务平台")

    # 数据库配置（由 .env 注入，禁止把真实账号密码写死在代码中）
    DATABASE_URL: str = _require("DATABASE_URL")
    SQL_ECHO: bool = _get_bool("SQL_ECHO", False)  # 设为 True 可在控制台打印每条执行 SQL

    # 认证与安全策略配置（供 auth.py 使用）
    SECRET_KEY: str = _get_secret_key()
    ENABLE_DEV_TOKEN: bool = _get_bool("ENABLE_DEV_TOKEN", True)  # 生产环境须设为 false 关闭快捷签发
    ACCESS_TOKEN_EXPIRE_MINUTES: int = _get_positive_int("ACCESS_TOKEN_EXPIRE_MINUTES", 120)
    LOGIN_MAX_FAILS: int = _get_positive_int("LOGIN_MAX_FAILS", 5)      # 连续登录失败触发锁定的阈值
    LOGIN_LOCK_MINUTES: int = _get_positive_int("LOGIN_LOCK_MINUTES", 15)  # 账号锁定时长（分钟）

    def __post_init__(self) -> None:
        """兜底校验直接构造 Settings 实例时的密钥与正整数约束。"""
        if not self.SECRET_KEY or not self.SECRET_KEY.strip():
            raise ValueError("SECRET_KEY 不能为空。")
        for field_name in (
            "ACCESS_TOKEN_EXPIRE_MINUTES",
            "LOGIN_MAX_FAILS",
            "LOGIN_LOCK_MINUTES",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} 必须大于 0。")


# 导出全组公用配置单例，其他模块统一使用 `from app.config import settings`
settings = Settings()
