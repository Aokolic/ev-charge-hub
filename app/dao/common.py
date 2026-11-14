"""全组公共持久层工具与账号认证 DAO 模块。

职责：
1. 提供手机号与车牌号的统一脱敏展示函数（mask_phone / mask_plate）；
2. 提供敏感字段确定性检索哈希（HMAC-SHA256）与对称可逆加解密（Fernet）；
3. 提供基于原生 SQL（text() 参数绑定）的视图通用分页查询封装（fetch_view_page）；
4. 提供认证模块所需的 user_account 账号查询、登录失败锁定、登录成功重置与账号注册写入。
"""

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.schemas.common import PageParams


def mask_phone(phone: str | None) -> str | None:
    """对手机号进行脱敏展示（如 138****5678），空值或纯空白返回 None。"""
    if phone is None:
        return None
    cleaned = phone.strip()
    if not cleaned:
        return None
    if len(cleaned) >= 7:
        return f"{cleaned[:3]}****{cleaned[-4:]}"
    return "****"


def mask_plate(plate: str | None) -> str | None:
    """对车牌号进行脱敏展示（如 京A***45 或 京A****45），空值或纯空白返回 None。"""
    if plate is None:
        return None
    cleaned = plate.strip()
    if not cleaned:
        return None
    if len(cleaned) >= 5:
        stars = "*" * max(3, len(cleaned) - 4)
        return f"{cleaned[:2]}{stars}{cleaned[-2:]}"
    return "***"


def hash_sensitive_lookup(plain: str) -> str:
    """使用服务端密钥计算敏感明文（如手机号/车牌）的 HMAC-SHA256 确定性检索摘要。"""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        plain.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _get_fernet() -> Fernet:
    """基于全局 SECRET_KEY 的 SHA-256 摘要派生 32 字节 URL 安全 Fernet 密钥实例。"""
    key = base64.urlsafe_b64encode(
        hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    )
    return Fernet(key)


def encrypt_sensitive(plain: str) -> str:
    """使用 Fernet 对去除首尾空白后的敏感明文进行对称加密，返回 ASCII 密文串。"""
    return _get_fernet().encrypt(plain.strip().encode("utf-8")).decode("ascii")


def decrypt_sensitive(cipher_text: str) -> str:
    """将 Fernet ASCII 密文串解密还原为 UTF-8 明文字符串。"""
    return _get_fernet().decrypt(cipher_text.encode("ascii")).decode("utf-8")


def fetch_view_page(
    db: Session,
    base_sql: str,
    params: dict[str, Any] | None,
    page_params: PageParams,
    order_by: str | None = None,
) -> dict[str, Any]:
    """针对视图或只读 SELECT 语句执行两段式参数化分页查询。

    注意：base_sql 与 order_by 必须来自 DAO 层受控白名单常量，严禁拼接未校验的用户输入。
    """
    clean_sql = base_sql.strip().rstrip(";").strip()
    if not clean_sql:
        raise ValueError("base_sql 不能为空")

    bound_params: dict[str, Any] = dict(params or {})

    # 第一步：包裹子查询统计符合条件的总行数；总数为 0 时直接短路，省去第二次分页查询
    count_sql = f"SELECT COUNT(1) AS cnt FROM ({clean_sql}) AS _v_count"
    total = int(db.execute(text(count_sql), bound_params).scalar_one() or 0)
    if total == 0:
        return {
            "total": 0,
            "page": page_params.page,
            "page_size": page_params.page_size,
            "items": [],
        }

    # 第二步：追加受控排序子句与参数化 LIMIT/OFFSET 拉取当前页字典列表
    order_clause = f" ORDER BY {order_by.strip()}" if order_by and order_by.strip() else ""
    page_sql = f"{clean_sql}{order_clause} LIMIT :_page_limit OFFSET :_page_offset"
    bound_params["_page_limit"] = page_params.limit
    bound_params["_page_offset"] = page_params.offset

    rows = db.execute(text(page_sql), bound_params).mappings().all()
    return {
        "total": total,
        "page": page_params.page,
        "page_size": page_params.page_size,
        "items": [dict(row) for row in rows],
    }


@dataclass(frozen=True)
class AccountRecord:
    """登录与注册账号记录（持久层只读数据载体，与 user_account 核心字段对齐）。"""

    user_id: int
    username: str
    password_hash: str
    role: str
    fleet_id: int | None = None
    phone_masked: str | None = None
    status: str = "ACTIVE"
    failed_login_count: int = 0
    locked_until: datetime | None = None


def _parse_datetime(value: Any) -> datetime | None:
    """兼容 MySQL datetime 对象与 SQLite 原生 text() 查询返回的 ISO 时间字符串。"""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        return datetime.fromisoformat(cleaned) if cleaned else None
    return value


def get_account_by_username(db: Session, username: str) -> AccountRecord | None:
    """按用户名精确查询登录账号信息，未找到返回 None。"""
    sql = text(
        "SELECT user_id, username, password_hash, role, fleet_id, "
        "phone_masked, status, failed_login_count, locked_until "
        "FROM user_account "
        "WHERE username = :username "
        "LIMIT 1"
    )
    row = db.execute(sql, {"username": username}).mappings().first()
    if row is None:
        return None

    return AccountRecord(
        user_id=int(row["user_id"]),
        username=str(row["username"]),
        password_hash=str(row["password_hash"]),
        role=str(row["role"]),
        fleet_id=int(row["fleet_id"]) if row["fleet_id"] is not None else None,
        phone_masked=row["phone_masked"],
        status=str(row["status"]),
        failed_login_count=int(row["failed_login_count"] or 0),
        locked_until=_parse_datetime(row["locked_until"]),
    )


def _execute_write_and_commit(
    db: Session,
    sql_text: str,
    params: dict[str, Any],
) -> int:
    """统一执行写操作 SQL 并显式提交；异常时先回滚会话再原样向上抛出约束错误。"""
    try:
        result = db.execute(text(sql_text), params)
        lastrowid = int(result.lastrowid or 0)
        db.commit()
        return lastrowid
    except Exception:
        db.rollback()
        raise


def record_login_failure(
    db: Session,
    user_id: int,
    failed_count: int,
    locked_until: datetime | None,
) -> None:
    """累计账号连续登录失败次数，并在达到阈值时写入锁定截止时间。"""
    _execute_write_and_commit(
        db,
        "UPDATE user_account "
        "SET failed_login_count = :failed_count, "
        "locked_until = :locked_until, "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE user_id = :user_id",
        {
            "failed_count": failed_count,
            "locked_until": locked_until,
            "user_id": user_id,
        },
    )


def reset_login_success(db: Session, user_id: int, login_time: datetime) -> None:
    """登录校验通过后清零失败计数、解除锁定状态并记录最近登录时间。"""
    _execute_write_and_commit(
        db,
        "UPDATE user_account "
        "SET failed_login_count = 0, "
        "locked_until = NULL, "
        "last_login_at = :login_time, "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE user_id = :user_id",
        {
            "login_time": login_time,
            "user_id": user_id,
        },
    )


def create_account(
    db: Session,
    username: str,
    password_hash: str,
    role: str,
    phone_plain: str,
    fleet_id: int | None = None,
) -> AccountRecord:
    """创建平台账号，自动完成手机号加密、检索摘要计算与脱敏留存。

    唯一键冲突等数据库 IntegrityError/OperationalError 会回滚事务后向上抛出。
    """
    phone_cipher = encrypt_sensitive(phone_plain)
    phone_hash = hash_sensitive_lookup(phone_plain)
    phone_masked = mask_phone(phone_plain)

    user_id = _execute_write_and_commit(
        db,
        "INSERT INTO user_account ("
        "username, password_hash, role, fleet_id, "
        "phone_cipher, phone_hash, phone_masked, status, failed_login_count"
        ") VALUES ("
        ":username, :password_hash, :role, :fleet_id, "
        ":phone_cipher, :phone_hash, :phone_masked, 'ACTIVE', 0"
        ")",
        {
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "fleet_id": fleet_id,
            "phone_cipher": phone_cipher,
            "phone_hash": phone_hash,
            "phone_masked": phone_masked,
        },
    )

    return AccountRecord(
        user_id=user_id,
        username=username,
        password_hash=password_hash,
        role=role,
        fleet_id=fleet_id,
        phone_masked=phone_masked,
        status="ACTIVE",
        failed_login_count=0,
        locked_until=None,
    )


__all__ = [
    "AccountRecord",
    "create_account",
    "decrypt_sensitive",
    "encrypt_sensitive",
    "fetch_view_page",
    "get_account_by_username",
    "hash_sensitive_lookup",
    "mask_phone",
    "mask_plate",
    "record_login_failure",
    "reset_login_success",
]
