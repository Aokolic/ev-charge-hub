"""业务线4：用户、车辆、企业车队与优惠券的六张表映射（负责人：成员 4，结构见 sql/01_ddl.sql）。

设计要点：
1. 只做映射不建表：所有列、CHECK 约束、UNIQUE 索引与触发器均以 sql/01_ddl.sql 为唯一真源；
2. user_account 的账号列（username / password_hash / role / fleet_id / phone_cipher /
   phone_hash / phone_masked / status / failed_login_count / locked_until / last_login_at）
   与 app/dao/common.py 的认证契约严格对齐，登录链路仍走原生 SQL，本层只供业务查询使用；
3. 手机号与车牌一律“三列分离”落库：*_cipher 存 Fernet 密文、*_hash 存 HMAC 盲索引、
   *_masked 存脱敏展示值，明文永不进数据库，也不出自视图；
4. 券的 ISSUED -> REDEEMED 状态由核销触发器 trg_coupon_redeem_after 自动回写，
   本层只读 status，不在 Python 侧手工改，避免与核销记录脱节；
5. coupon_redeem_record.order_id 指向业务线2 的 charge_order，属跨线逻辑外键，故不声明 ForeignKey。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Enum,
    FetchedValue,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 与 app/auth.py 的 RoleEnum 及视图/接口的角色隔离口径保持完全一致
ROLE_ENUM = Enum("driver", "ops", "settlement", "fleet", "admin", name="user_role_enum")
REALNAME_STATUS_ENUM = Enum(
    "UNVERIFIED", "PENDING", "VERIFIED", "REJECTED", name="realname_status_enum"
)
REGISTER_CHANNEL_ENUM = Enum(
    "APP", "MINI_PROGRAM", "WEB", "OFFLINE", "FLEET_IMPORT", name="register_channel_enum"
)
MEMBER_LEVEL_ENUM = Enum("NORMAL", "SILVER", "GOLD", "PLATINUM", name="member_level_enum")
USER_STATUS_ENUM = Enum("ACTIVE", "FROZEN", "DISABLED", name="user_status_enum")
FLEET_STATUS_ENUM = Enum("ACTIVE", "SUSPENDED", "CLOSED", name="fleet_status_enum")
PLATE_COLOR_ENUM = Enum("GREEN", "BLUE", "YELLOW", "WHITE", "BLACK", name="plate_color_enum")
BIND_STATUS_ENUM = Enum("BOUND", "UNBOUND", "DISABLED", name="bind_status_enum")
COUPON_KIND_ENUM = Enum("CASH", "DISCOUNT", "SERVICE_FEE_FREE", name="coupon_kind_enum")
CAMPAIGN_STATUS_ENUM = Enum("DRAFT", "ACTIVE", "PAUSED", "ENDED", name="campaign_status_enum")
ISSUE_CHANNEL_ENUM = Enum(
    "NEW_USER_PACK", "OPS_GRANT", "ACTIVITY", "COMPENSATION", name="coupon_issue_channel_enum"
)
COUPON_STATUS_ENUM = Enum("ISSUED", "REDEEMED", "EXPIRED", "VOID", name="coupon_status_enum")


class FleetAccount(Base):
    """企业车队（企业客户）档案：以“授信额度 - 已用额度”控制车队批量开卡与用车。"""

    __tablename__ = "fleet_account"

    fleet_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fleet_code: Mapped[str] = mapped_column(String(32), unique=True)
    fleet_name: Mapped[str] = mapped_column(String(120))
    credit_code: Mapped[str | None] = mapped_column(CHAR(18))
    contact_name: Mapped[str] = mapped_column(String(50))
    contact_phone_cipher: Mapped[str] = mapped_column(String(255))
    contact_phone_hash: Mapped[str] = mapped_column(CHAR(64))
    contact_phone_masked: Mapped[str | None] = mapped_column(String(20))
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    credit_used: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(FLEET_STATUS_ENUM, default="ACTIVE")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())


class UserAccount(Base):
    """平台统一账号（5 类角色）：一个账号最多归属一个企业车队，fleet 角色的 fleet_id 必填。"""

    __tablename__ = "user_account"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(ROLE_ENUM, default="driver")
    fleet_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("fleet_account.fleet_id"))
    # 敏感信息三列分离：密文 + 盲索引 + 脱敏值
    phone_cipher: Mapped[str] = mapped_column(String(255))
    phone_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    phone_masked: Mapped[str | None] = mapped_column(String(20))
    realname_status: Mapped[str] = mapped_column(REALNAME_STATUS_ENUM, default="UNVERIFIED")
    realname_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    register_channel: Mapped[str] = mapped_column(REGISTER_CHANNEL_ENUM, default="APP")
    member_level: Mapped[str] = mapped_column(MEMBER_LEVEL_ENUM, default="NORMAL")
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(USER_STATUS_ENUM, default="ACTIVE")
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())


class Vehicle(Base):
    """车辆档案：一个用户可绑定多辆车；active_plate_hash 全局唯一 → 同一车牌同刻只归一个车主。"""

    __tablename__ = "vehicle"

    vehicle_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user_account.user_id"))
    fleet_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("fleet_account.fleet_id"))
    plate_cipher: Mapped[str] = mapped_column(String(255))
    plate_hash: Mapped[str] = mapped_column(CHAR(64), index=True)
    plate_masked: Mapped[str] = mapped_column(String(20))
    plate_color: Mapped[str] = mapped_column(PLATE_COLOR_ENUM, default="GREEN")
    vehicle_model: Mapped[str] = mapped_column(String(64))
    battery_capacity_kwh: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    max_charge_power: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    supports_fast_charge: Mapped[bool] = mapped_column(Boolean, default=True)
    bind_status: Mapped[str] = mapped_column(BIND_STATUS_ENUM, default="BOUND")
    # 生成列：仅在 BOUND 时暴露车牌盲索引，解绑后为 NULL，使车牌可被重新绑定
    # （写入时不要赋值，由数据库按 bind_status 自动计算）
    active_plate_hash: Mapped[str | None] = mapped_column(
        CHAR(64),
        Computed("CASE WHEN bind_status = 'BOUND' THEN plate_hash END", persisted=True),
        unique=True,
    )
    bound_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())


class CouponCampaign(Base):
    """优惠活动：面额、门槛、适用场站、发放总量与每人限领；发放量由触发器累加。"""

    __tablename__ = "coupon_campaign"

    campaign_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    campaign_code: Mapped[str] = mapped_column(String(32), unique=True)
    campaign_name: Mapped[str] = mapped_column(String(120))
    coupon_kind: Mapped[str] = mapped_column(COUPON_KIND_ENUM, default="CASH")
    face_value: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    threshold_amount: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0.00"))
    # 跨线逻辑外键：指向业务线1 的 station.station_id。物理外键已在 sql/01_ddl.sql 建立，
    # ORM 侧不声明 ForeignKey，避免仅导入本模块时 station 模型缺位导致整站映射失败
    # （与 app/models/monitor.py 中 pile_id / maintainer_id 的处理口径一致）。
    applicable_station_id: Mapped[int | None] = mapped_column(BigInteger)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    issue_total: Mapped[int] = mapped_column(Integer)
    issued_count: Mapped[int] = mapped_column(Integer, default=0)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1)
    redeem_rule: Mapped[str] = mapped_column(String(255), default="单笔订单限用一张")
    status: Mapped[str] = mapped_column(CAMPAIGN_STATUS_ENUM, default="DRAFT")
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user_account.user_id"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())


class CouponRecord(Base):
    """优惠券发放记录（一张券一行）：面额与门槛为发放时快照，status 由核销触发器回写。"""

    __tablename__ = "coupon_record"

    coupon_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    coupon_no: Mapped[str] = mapped_column(String(32), unique=True)
    campaign_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("coupon_campaign.campaign_id")
    )
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_account.user_id"))
    face_value: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    threshold_amount: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0.00"))
    issue_channel: Mapped[str] = mapped_column(ISSUE_CHANNEL_ENUM, default="OPS_GRANT")
    issued_by: Mapped[int | None] = mapped_column(BigInteger)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())
    expire_at: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(COUPON_STATUS_ENUM, default="ISSUED")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())


class CouponRedeemRecord(Base):
    """优惠券核销记录：uk_redeem_order / uk_redeem_coupon 双唯一索引保证“一单一券、一券一销”。

    order_id 为业务线2 charge_order 的跨线逻辑外键（与 repair_order.maintainer_id 同一口径），
    因此本模型不声明 ForeignKey，仅按列语义映射。
    """

    __tablename__ = "coupon_redeem_record"

    redeem_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    coupon_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("coupon_record.coupon_id"), unique=True
    )
    order_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_account.user_id"))
    # 同上：station_id 为业务线1 的跨线逻辑外键，物理约束在 DDL，ORM 侧按列映射
    station_id: Mapped[int] = mapped_column(BigInteger)
    face_value: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    order_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=FetchedValue())


__all__ = [
    "BIND_STATUS_ENUM",
    "CAMPAIGN_STATUS_ENUM",
    "COUPON_KIND_ENUM",
    "COUPON_STATUS_ENUM",
    "CouponCampaign",
    "CouponRecord",
    "CouponRedeemRecord",
    "FleetAccount",
    "ISSUE_CHANNEL_ENUM",
    "MEMBER_LEVEL_ENUM",
    "PLATE_COLOR_ENUM",
    "REALNAME_STATUS_ENUM",
    "REGISTER_CHANNEL_ENUM",
    "ROLE_ENUM",
    "USER_STATUS_ENUM",
    "UserAccount",
    "Vehicle",
]
