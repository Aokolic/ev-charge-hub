"""业务线5：结算对账的三张表映射（结构见 sql/01_ddl.sql）。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Enum, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

BILL_STATUS_ENUM = Enum(
    "DRAFT", "CONFIRMED", "INVOICED", "CLOSED", name="bill_status_enum"
)
DIFF_TYPE_ENUM = Enum(
    "MISSING_ORDER", "AMOUNT_MISMATCH", "DUPLICATE_PAY", "UNPAID", name="diff_type_enum"
)
INVOICE_TYPE_ENUM = Enum("NORMAL", "SPECIAL", name="invoice_type_enum")
INVOICE_STATUS_ENUM = Enum("ISSUED", "RED_FLUSHED", name="invoice_status_enum")


class SettlementBill(Base):
    __tablename__ = "settlement_bill"

    bill_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    operator_id: Mapped[int] = mapped_column(BigInteger)
    station_id: Mapped[int | None] = mapped_column(BigInteger)
    bill_month: Mapped[str] = mapped_column(String(7))
    order_count: Mapped[int] = mapped_column(Integer, default=0)
    total_kwh: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0.000"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    payable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(BILL_STATUS_ENUM, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, server_default=text("CURRENT_TIMESTAMP"))


class ReconcileDiff(Base):
    __tablename__ = "reconcile_diff"

    diff_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(BigInteger)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    diff_type: Mapped[str] = mapped_column(DIFF_TYPE_ENUM)
    expect_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    diff_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    resolved: Mapped[int] = mapped_column(Integer, default=0)
    remark: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=text("CURRENT_TIMESTAMP"))


class InvoiceRecord(Base):
    __tablename__ = "invoice_record"

    invoice_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(BigInteger)
    invoice_no: Mapped[str] = mapped_column(String(32), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    invoice_type: Mapped[str] = mapped_column(INVOICE_TYPE_ENUM, default="NORMAL")
    status: Mapped[str] = mapped_column(INVOICE_STATUS_ENUM, default="ISSUED")
    red_flush_of: Mapped[int | None] = mapped_column(BigInteger)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime)
