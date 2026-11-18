"""业务线2：订单计费的四张表映射。

表结构以 sql/01_ddl.sql 为准，这里只做映射，不建表（main 里没有 create_all）。
active_gun_id 是数据库生成列，不能出现在 INSERT/UPDATE 里，所以不映射进来。
"""

from datetime import date, datetime, time
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Enum, Integer, Numeric, String, Time, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

PERIOD_ENUM = Enum("PEAK", "FLAT", "VALLEY", name="period_enum")
ORDER_STATUS_ENUM = Enum(
    "CHARGING", "FINISHED", "PAID", "CANCELLED", "REFUNDED", name="order_status_enum"
)
PAY_METHOD_ENUM = Enum("WECHAT", "ALIPAY", "BALANCE", "CASH", name="pay_method_enum")
PAY_STATUS_ENUM = Enum("SUCCESS", "FAILED", "REFUNDED", name="pay_status_enum")


class PricingRule(Base):
    __tablename__ = "pricing_rule"

    rule_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(BigInteger)
    period: Mapped[str] = mapped_column(PERIOD_ENUM)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    energy_price: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    service_price: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    free_minutes: Mapped[int] = mapped_column(Integer, default=0)
    min_charge: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0.00"))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[int] = mapped_column(Integer, default=1)


class ChargeOrder(Base):
    __tablename__ = "charge_order"

    order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    vehicle_id: Mapped[int | None] = mapped_column(BigInteger)
    gun_id: Mapped[int] = mapped_column(BigInteger)
    station_id: Mapped[int] = mapped_column(BigInteger)
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime | None] = mapped_column(DateTime)
    start_soc: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    end_soc: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    kwh: Mapped[Decimal] = mapped_column(Numeric(10, 3), default=Decimal("0.000"))
    energy_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    service_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(ORDER_STATUS_ENUM, default="CHARGING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, server_default=text("CURRENT_TIMESTAMP"))


class OrderFeeDetail(Base):
    __tablename__ = "order_fee_detail"

    detail_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger)
    period: Mapped[str] = mapped_column(PERIOD_ENUM)
    rule_id: Mapped[int | None] = mapped_column(BigInteger)
    begin_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    kwh: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    energy_price: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    service_price: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    energy_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    service_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class PaymentRecord(Base):
    __tablename__ = "payment_record"

    payment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    method: Mapped[str] = mapped_column(PAY_METHOD_ENUM)
    txn_no: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(PAY_STATUS_ENUM, default="SUCCESS")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    remark: Mapped[str | None] = mapped_column(String(200))
