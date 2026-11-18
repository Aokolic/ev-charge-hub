"""业务线1：场站与充电设施（成员1/组长） —— SQLAlchemy 2.0 ORM 表映射（仅映射已有表，严禁 create_all）。

表结构严格以 sql/01_ddl.sql 为唯一真源，涵盖 5 张核心物理表：
1. operator: 运营商档案表
2. station: 充电场站表
3. charging_pile: 充电桩表
4. charging_gun: 充电枪表（独立实体，计费最小粒度）
5. device_status_log: 设备状态变更日志表（pile_id 与 gun_id 互斥留痕）
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ============================================================================
# 1. 枚举类型定义（与 sql/01_ddl.sql ENUM 取值严格一致）
# ============================================================================

OPERATOR_STATUS_ENUM = Enum("ACTIVE", "SUSPENDED", name="operator_status_enum")
STATION_STATUS_ENUM = Enum("OPEN", "MAINTENANCE", "CLOSED", name="station_status_enum")
ACCEPTANCE_STATUS_ENUM = Enum("PENDING", "PASSED", "REJECTED", name="acceptance_status_enum")
DEVICE_STATUS_ENUM = Enum("IDLE", "OCCUPIED", "FAULT", "OFFLINE", name="device_status_enum")
GUN_TYPE_ENUM = Enum("DC_FAST", "AC_SLOW", name="gun_type_enum")
DEVICE_TYPE_ENUM = Enum("PILE", "GUN", name="device_type_enum")
CHANGE_SOURCE_ENUM = Enum(
    "INIT", "ACCEPTANCE", "ORDER", "HEARTBEAT", "MANUAL", "ALARM", name="change_source_enum"
)
TODO_STATUS_ENUM = Enum("NONE", "PENDING", "DONE", name="todo_status_enum")

# 兼容 MySQL BIGINT UNSIGNED 主键与 SQLite 单元测试自增主键
_PK_BIGINT = BigInteger().with_variant(Integer, "sqlite")


# ============================================================================
# 2. 五张物理表 ORM 映射模型
# ============================================================================


class Operator(Base):
    """运营商档案表（operator）。"""

    __tablename__ = "operator"

    operator_id: Mapped[int] = mapped_column(_PK_BIGINT, primary_key=True, autoincrement=True)
    operator_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    operator_name: Mapped[str] = mapped_column(String(100), nullable=False)
    credit_code: Mapped[str] = mapped_column(String(18), unique=True, nullable=False)
    contact_name: Mapped[str] = mapped_column(String(50), nullable=False)
    service_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(OPERATOR_STATUS_ENUM, default="ACTIVE", nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Station(Base):
    """充电场站表（station）。"""

    __tablename__ = "station"

    station_id: Mapped[int] = mapped_column(_PK_BIGINT, primary_key=True, autoincrement=True)
    operator_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("operator.operator_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    )
    station_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    station_name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    latitude: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    total_spaces: Mapped[int] = mapped_column(Integer, nullable=False)
    available_spaces: Mapped[int] = mapped_column(Integer, nullable=False)
    business_hours: Mapped[str] = mapped_column(String(64), default="00:00-24:00", nullable=False)
    parking_fee_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("0.00"), nullable=False
    )
    station_status: Mapped[str] = mapped_column(STATION_STATUS_ENUM, default="OPEN", nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ChargingPile(Base):
    """充电桩表（charging_pile）。"""

    __tablename__ = "charging_pile"

    pile_id: Mapped[int] = mapped_column(_PK_BIGINT, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("station.station_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    )
    pile_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    device_model: Mapped[str] = mapped_column(String(64), nullable=False)
    rated_power: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    gun_count: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol_type: Mapped[str] = mapped_column(String(32), default="OCPP1.6", nullable=False)
    commissioned_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    warranty_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    acceptance_status: Mapped[str] = mapped_column(
        ACCEPTANCE_STATUS_ENUM, default="PENDING", nullable=False
    )
    current_status: Mapped[str] = mapped_column(
        DEVICE_STATUS_ENUM, default="OFFLINE", nullable=False
    )
    offline_threshold_sec: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ChargingGun(Base):
    """充电枪表（charging_gun，订单与计费最小粒度；单枪功率上限由数据库触发器校验）。"""

    __tablename__ = "charging_gun"
    __table_args__ = (UniqueConstraint("pile_id", "gun_seq", name="uk_gun_pile_seq"),)

    gun_id: Mapped[int] = mapped_column(_PK_BIGINT, primary_key=True, autoincrement=True)
    pile_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("charging_pile.pile_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=False,
    )
    gun_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    gun_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    gun_type: Mapped[str] = mapped_column(GUN_TYPE_ENUM, nullable=False)
    connector_standard: Mapped[str] = mapped_column(String(32), nullable=False)
    max_power: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    current_status: Mapped[str] = mapped_column(
        DEVICE_STATUS_ENUM, default="OFFLINE", nullable=False
    )
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class DeviceStatusLog(Base):
    """设备状态变更日志表（device_status_log，pile_id 与 gun_id 互斥二选一）。"""

    __tablename__ = "device_status_log"

    log_id: Mapped[int] = mapped_column(_PK_BIGINT, primary_key=True, autoincrement=True)
    device_type: Mapped[str] = mapped_column(DEVICE_TYPE_ENUM, nullable=False)
    pile_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("charging_pile.pile_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    gun_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("charging_gun.gun_id", ondelete="RESTRICT", onupdate="RESTRICT"),
        nullable=True,
    )
    prev_status: Mapped[str | None] = mapped_column(DEVICE_STATUS_ENUM, nullable=True)
    new_status: Mapped[str] = mapped_column(DEVICE_STATUS_ENUM, nullable=False)
    change_source: Mapped[str] = mapped_column(
        CHANGE_SOURCE_ENUM, default="MANUAL", nullable=False
    )
    todo_status: Mapped[str] = mapped_column(TODO_STATUS_ENUM, default="NONE", nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


__all__ = [
    "OPERATOR_STATUS_ENUM",
    "STATION_STATUS_ENUM",
    "ACCEPTANCE_STATUS_ENUM",
    "DEVICE_STATUS_ENUM",
    "GUN_TYPE_ENUM",
    "DEVICE_TYPE_ENUM",
    "CHANGE_SOURCE_ENUM",
    "TODO_STATUS_ENUM",
    "Operator",
    "Station",
    "ChargingPile",
    "ChargingGun",
    "DeviceStatusLog",
]
