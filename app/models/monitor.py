"""业务线3：设备监控与工单（成员3/代润森） —— SQLAlchemy 2.0 ORM 表映射。

铁律：本模块只做 SQLAlchemy 2.0 映射，严禁调用 Base.metadata.create_all()。
表结构以 sql/01_ddl.sql 为唯一真源；跨线外键（charging_pile / charging_gun /
user_account）在 DDL 中维护，本层按列语义映射为 BIGINT 字段，保持查询层与
数据库约束解耦。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PileTelemetry(Base):
    """分钟级设备运行遥测（分区事实表，无外键）。

    主键为 (telemetry_id, collected_at)，其中 collected_at 必须位于主键中，
    以满足 MySQL 分区表对主键/唯一键必须包含分区键的限制。
    """

    __tablename__ = "pile_telemetry"

    telemetry_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    output_voltage: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    output_current: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    power: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    module_temp: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    offline_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fault_code: Mapped[str | None] = mapped_column(String(32))


class AlarmRecord(Base):
    """设备告警记录：与维修工单构成 1:N 关系。"""

    __tablename__ = "alarm_record"

    alarm_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gun_id: Mapped[int | None] = mapped_column(BigInteger)
    alarm_code: Mapped[str] = mapped_column(String(32), nullable=False)
    alarm_level: Mapped[str] = mapped_column(String(8), nullable=False)
    original_level: Mapped[str] = mapped_column(String(8), nullable=False)
    response_limit_min: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    escalated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RepairOrder(Base):
    """维修工单：同一告警可多次派单，但同一时刻仅允许一张处理中工单。"""

    __tablename__ = "repair_order"

    work_order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alarm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    maintainer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DISPATCHED")
    dispatched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime)
    repaired_at: Mapped[datetime | None] = mapped_column(DateTime)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    replaced_parts: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = ["AlarmRecord", "PileTelemetry", "RepairOrder"]
