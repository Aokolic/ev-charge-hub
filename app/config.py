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


@dataclass(frozen=True)  # frozen=True 保证配置只读，防止运行期被意外修改
class Settings:
    """应用全局只读配置集合。"""

    # 应用基础信息
    APP_TITLE: str = os.getenv("APP_TITLE", "新能源汽车充电桩运营服务平台")

    # 数据库配置（由 .env 注入，禁止把真实账号密码写死在代码中）
    DATABASE_URL: str = _require("DATABASE_URL")
    SQL_ECHO: bool = _get_bool("SQL_ECHO", False)  # 设为 True 可在控制台打印每条执行 SQL

    # 认证与安全策略配置（供 auth.py 使用）
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = _get_int("ACCESS_TOKEN_EXPIRE_MINUTES", 120)
    LOGIN_MAX_FAILS: int = _get_int("LOGIN_MAX_FAILS", 5)      # 连续登录失败触发锁定的阈值
    LOGIN_LOCK_MINUTES: int = _get_int("LOGIN_LOCK_MINUTES", 15)  # 账号锁定时长（分钟）


# 导出全组公用配置单例，其他模块统一使用 `from app.config import settings`
settings = Settings()
