"""业务线2：订单计费的入参/出参契约。"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OrderStartIn(BaseModel):
    """开单入参：扫枪码充电，车没在平台登记时 vehicle_id 可以不填。"""

    gun_id: int = Field(gt=0, description="充电枪 ID")
    station_id: int = Field(gt=0, description="场站 ID")
    vehicle_id: int | None = Field(default=None, gt=0)
    start_soc: Decimal | None = Field(default=None, ge=0, le=100, description="开始 SOC")


class OrderFinishIn(BaseModel):
    """结束充电：真实项目里 kwh 由电表上报，这里让调用方传进来。"""

    kwh: Decimal = Field(gt=0, le=200, description="本次充电度数")
    end_soc: Decimal | None = Field(default=None, ge=0, le=100)


class PaymentIn(BaseModel):
    """支付入参。amount 为负表示退款；txn_no 是渠道流水号，用来防重复入账。"""

    amount: Decimal = Field(description="支付金额，负数表示退款")
    method: str = Field(pattern="^(WECHAT|ALIPAY|BALANCE|CASH)$")
    txn_no: str = Field(min_length=6, max_length=64)

    @model_validator(mode="after")
    def _check_amount(self) -> "PaymentIn":
        if self.amount == 0:
            raise ValueError("支付金额不能为 0")
        return self


class FeeDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    detail_id: int
    period: str
    begin_time: datetime
    end_time: datetime
    kwh: Decimal
    energy_price: Decimal
    service_price: Decimal
    energy_fee: Decimal
    service_fee: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: int
    order_no: str
    user_id: int
    gun_id: int
    station_id: int
    start_time: datetime
    end_time: datetime | None
    kwh: Decimal
    energy_fee: Decimal
    service_fee: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    paid_amount: Decimal
    status: str


class OrderDetailOut(OrderOut):
    """订单详情：多带一份分时明细，前端用来展示峰平谷各花了多少。"""

    details: list[FeeDetailOut] = Field(default_factory=list)


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: int
    order_id: int
    amount: Decimal
    method: str
    txn_no: str
    status: str
    paid_at: datetime | None


class PeriodStatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: str
    stat_date: str
    order_cnt: int
    total_kwh: Decimal
    energy_fee: Decimal
    service_fee: Decimal
    avg_price: Decimal
