"""业务线2：订单计费的入参/出参契约。"""

from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MONEY = dict(max_digits=12, decimal_places=2, description="金额（元，两位小数）")


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
    """支付入参。txn_no 是渠道流水号，唯一索引用来防重复入账。"""

    amount: Decimal = Field(gt=0, **MONEY)
    method: str = Field(pattern="^(WECHAT|ALIPAY|BALANCE|CASH)$")
    txn_no: str = Field(min_length=6, max_length=64)


class RefundIn(BaseModel):
    """退款入参：金额必须为负，只允许结算专员/管理员发起。"""

    amount: Decimal = Field(lt=0, **MONEY)
    method: str = Field(pattern="^(WECHAT|ALIPAY|BALANCE|CASH)$")
    txn_no: str = Field(min_length=6, max_length=64)


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
    """峰平谷统计：stat_date 是 date 不是 str，视图原样返回 date 类型。"""

    model_config = ConfigDict(from_attributes=True)

    period: str
    stat_date: date
    order_cnt: int
    total_kwh: Decimal | None = None
    energy_fee: Decimal | None = None
    service_fee: Decimal | None = None
    avg_price: Decimal | None = None


class PriceRuleIn(BaseModel):
    """新增一条分时电价规则（同一站同一时段不要重叠）。"""

    station_id: int = Field(gt=0)
    period: str = Field(pattern="^(PEAK|FLAT|VALLEY)$", description="峰/平/谷")
    start_time: str = Field(pattern="^\\d{2}:\\d{2}$", description="HH:MM，可跨零点")
    end_time: str = Field(pattern="^\\d{2}:\\d{2}$")
    energy_price: Decimal = Field(gt=0, max_digits=8, decimal_places=4)
    service_price: Decimal = Field(ge=0, max_digits=8, decimal_places=4)
    free_minutes: int = Field(default=0, ge=0, le=120)
    min_charge: Decimal = Field(default=Decimal("0"), ge=0, **{k: v for k, v in MONEY.items() if k != "description"})
    effective_from: date
    effective_to: date | None = None


class PriceRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rule_id: int
    station_id: int
    period: str
    start_time: time
    end_time: time
    energy_price: Decimal
    service_price: Decimal
    free_minutes: int
    min_charge: Decimal
    is_active: bool


class PriceRuleActiveIn(BaseModel):
    is_active: bool
