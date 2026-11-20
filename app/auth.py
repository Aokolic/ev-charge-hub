"""认证与 RBAC 权限守卫模块（全组公共底座）。

职责：
1. 定义平台 5 类 RBAC 角色枚举（RoleEnum）与当前登录用户上下文（CurrentUser）；
2. 提供基于标准库 PBKDF2-HMAC-SHA256 的密码加盐哈希与恒定时间校验；
3. 提供无状态 HS256 类 JWT 令牌的签发与验签（零第三方加密库依赖，兼容 Python 3.14）；
4. 提供 FastAPI 依赖注入守卫（get_current_user / require_roles），严格区分 401 未认证与 403 越权。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from enum import Enum

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db


class RoleEnum(str, Enum):
    """系统 5 类 RBAC 角色枚举（与任务书 2.2 节及路由隔离矩阵严格对齐）。"""

    DRIVER = "driver"
    OPS = "ops"
    SETTLEMENT = "settlement"
    FLEET = "fleet"
    ADMIN = "admin"

    @property
    def label(self) -> str:
        """返回角色对应的中文显示名称，用于日志或错误提示。"""
        labels = {
            "driver": "普通车主",
            "ops": "场站运维工程师",
            "settlement": "客服与结算专员",
            "fleet": "企业客户/车队",
            "admin": "平台运营管理员",
        }
        return labels[self.value]

    # 使用示例：
    # RoleEnum.DRIVER == "driver"  -> True（可直接与数据库字段比对）
    # RoleEnum.DRIVER.value        -> "driver"（JSON 序列化输出）
    # RoleEnum.DRIVER.label        -> "普通车主"（获取中文说明）


class CurrentUser(BaseModel):
    """当前已认证用户的只读上下文，供业务路由与 DAO 层做数据归属隔离。"""

    model_config = ConfigDict(frozen=True)  # 只读模型，防止在单次请求处理中被意外篡改

    user_id: int
    username: str
    role: RoleEnum
    fleet_id: int | None = None  # 企业车队角色专属隔离键，非车队用户为 None


def hash_password(plain: str) -> str:
    """使用 PBKDF2-SHA256 生成带随机盐的密码哈希串。

    存储格式：pbkdf2_sha256$<迭代次数>$<16进制盐>$<16进制摘要>
    """
    iterations = 100_000
    salt = secrets.token_bytes(16)  # 每次生成 128 位独立随机盐，防止彩虹表攻击
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations=iterations)

    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与数据库哈希串是否匹配。

    采用 hmac.compare_digest 做恒定时间比对以防御时序攻击；
    当哈希串格式损坏或算法不匹配时安全返回 False，不向外抛异常。
    """
    try:
        algorithm, iterations_str, salt_hex, dk_hex = hashed.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected_dk = bytes.fromhex(dk_hex)
        actual_dk = hashlib.pbkdf2_hmac(
            "sha256",
            plain.encode("utf-8"),
            salt,
            iterations=int(iterations_str),
        )

        return hmac.compare_digest(expected_dk, actual_dk)
    except (ValueError, TypeError):
        return False


def _b64url_encode(raw: bytes) -> str:
    """将字节串编码为无填充（去掉末尾 '='）的 URL 安全 Base64 字符串。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """将无填充的 URL 安全 Base64 字符串补齐 '=' 后还原为原始字节串。"""
    # Base64 编码长度须为 4 的倍数，用 (-len % 4) 精确计算缺失的 '=' 数量
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def _unauthorized(detail: str) -> HTTPException:
    """构造带标准 Bearer 质询头的 401 未认证异常。"""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def create_access_token(user: CurrentUser, expires_minutes: int | None = None) -> str:
    """为已认证用户签发无状态 HS256 访问令牌（Header.Payload.Signature）。"""
    now = int(time.time())  # 统一采用 UTC 秒级整数时间戳
    minutes = expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user.user_id),  # JWT 标准 Subject 字段约定存字符串 ID
        "username": user.username,
        "role": user.role.value,   # 显式序列化为小写角色标识串
        "fleet_id": user.fleet_id,
        "iat": now,
        "exp": now + minutes * 60,  # 分钟转换为秒
    }

    # separators=(",", ":") 去除冗余空格；ensure_ascii=False 保留中文直出以缩减字节长度
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    header_b64 = _b64url_encode(header_bytes)
    payload_b64 = _b64url_encode(payload_bytes)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    # 使用服务端密钥对前两段做 HMAC-SHA256 签名防篡改
    signature = hmac.new(settings.SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_access_token(token: str) -> CurrentUser:
    """校验并解析访问令牌，还原为 CurrentUser；任何格式错误、篡改或过期均抛出 401。"""
    parts = token.split(".")
    if len(parts) != 3:
        raise _unauthorized("无效的 Token 格式")

    header_b64, payload_b64, sig_b64 = parts

    # 安全原则：先验签再解码 JSON，防止在未验证来源前解析不可信载荷
    expected_sig = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_sig_b64 = _b64url_encode(expected_sig)

    # 转为字节再比对：hmac.compare_digest 对 str 仅支持纯 ASCII，直接传含中文的脏串会抛 TypeError
    if not hmac.compare_digest(expected_sig_b64.encode("ascii"), sig_b64.encode("utf-8")):
        raise _unauthorized("鉴权失败")

    # HTTPException 不在下列异常元组中，因此显式抛出的具体 401 提示不会被吞掉
    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))

        if header["alg"] != "HS256":
            raise _unauthorized("不支持的 Token 算法")

        if int(payload["exp"]) <= int(time.time()):
            raise _unauthorized("Token 已过期")

        return CurrentUser(
            user_id=int(payload["sub"]),
            username=payload["username"],
            role=RoleEnum(payload["role"]),  # 按值反查枚举（注意用圆括号而非方括号）
            fleet_id=payload.get("fleet_id"),
        )
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise _unauthorized("无效的 Token 内容")


# auto_error=False 便于统一由 get_current_user 返回中文 401 提示与标准质询头
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session | None = Depends(get_db),
) -> CurrentUser:
    """FastAPI 基础认证依赖：提取 Authorization: Bearer <token> 解析为 CurrentUser 并校验账号状态。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("请先提供有效的 Bearer Token")
    user = decode_access_token(credentials.credentials)
    # 兼容单测直接传入凭证而不经 FastAPI DI 注入 db 的调用方式
    if isinstance(db, Session):
        account_status = db.execute(
            text("SELECT status FROM user_account WHERE user_id = :uid LIMIT 1"),
            {"uid": user.user_id},
        ).scalar_one_or_none()
        if account_status is not None and account_status != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="账号已被冻结或禁用，请联系平台管理员",
            )
    return user


def require_roles(*allowed_roles: RoleEnum):
    """FastAPI 角色权限守卫工厂：未登录返回 401，已登录但角色不在白名单内返回 403。"""

    def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"当前角色 {user.role.label} 无权执行此操作",
            )
        return user

    return _guard
