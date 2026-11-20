"""业务线5：结算对账的入参/出参契约。"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 金额统一两位小数、最多 12 位，避免前端传 1e9 之类的脏数据
MONEY = dict(max_digits=12, decimal_places=2)


class BillGenerateIn(BaseModel):
    """生成月度结算单：按运营商 + 月份汇总，抽成比例默认 5%。"""

    operator_id: int = Field(gt=0)
    bill_month: str = Field(
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM，月份必须是 01-12"
    )
    platform_rate: Decimal = Field(
        default=Decimal("0.0500"), ge=0, le=1, max_digits=5, decimal_places=4
    )


class BillGenerateOut(BaseModel):
    """生成结果：本轮影响了几张账单。"""

    success: bool
    message: str
    affected_bills: int


class BillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bill_id: int
    operator_id: int
    station_id: int | None
    bill_month: str
    order_count: int
    total_kwh: Decimal
    total_amount: Decimal
    platform_fee: Decimal
    payable_amount: Decimal
    status: str


class BillDetailOut(BillOut):
    """账单总览：多带开票进度和未处理差异数，对应视图 v_settle_bill_detail。"""

    invoiced_amount: Decimal
    uninvoiced_amount: Decimal
    open_diff_cnt: int


class DiffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    diff_id: int
    bill_id: int
    order_id: int | None
    diff_type: str
    expect_amount: Decimal
    actual_amount: Decimal
    diff_amount: Decimal
    resolved: int
    remark: str | None
    created_at: datetime | None


class DiffResolveIn(BaseModel):
    """把一条差异标记为已处理。"""

    remark: str | None = Field(default=None, max_length=200)


class InvoiceIn(BaseModel):
    """开票：金额必须大于 0，超不超过账单应付由触发器判断。"""

    amount: Decimal = Field(gt=0, **MONEY, description="开票金额")
    invoice_no: str = Field(min_length=4, max_length=32)
    invoice_type: str = Field(default="NORMAL", pattern="^(NORMAL|SPECIAL)$")


class RedFlushIn(BaseModel):
    """红冲：对指定发票开一张负数票，金额默认等于原票。"""

    invoice_no: str = Field(min_length=4, max_length=32)
    amount: Decimal | None = Field(default=None, description="不填就按原票金额冲")

    @model_validator(mode="after")
    def _check(self) -> "RedFlushIn":
        if self.amount is not None and self.amount >= 0:
            raise ValueError("红冲金额必须为负数或留空")
        return self


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invoice_id: int
    bill_id: int
    invoice_no: str
    amount: Decimal
    invoice_type: str
    status: str
    red_flush_of: int | None
    issued_at: datetime | None
