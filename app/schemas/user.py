"""业务线4：用户、车辆、企业车队与优惠券的 Pydantic v2 入参/出参契约（负责人：成员 4）。

铁律：
1. 敏感字段只在“入参”里出现明文（phone / plate_no / contact_phone），
   出参一律只输出 phone_masked / plate_masked / contact_phone_masked，禁止透出密文或明文；
2. 余额、授信额度、券面额、抵扣金额一律 Decimal，严禁转 float（与库中 DECIMAL 对齐）；
3. 明显非法请求在进入数据库前先拦截：金额符号、电量范围、快充功率一致性、
   活动有效期先后、折扣券面额上限；数据库触发器再兜底业务规则（发券/核销）。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth import RoleEnum

# 角色 / 状态 / 等级等枚举的字面量别名，既做类型校验又能在 OpenAPI 中直接展示可选值
RoleLiteral = Literal["driver", "ops", "settlement", "fleet", "admin"]
UserStatusLiteral = Literal["ACTIVE", "FROZEN", "DISABLED"]
RealnameStatusLiteral = Literal["UNVERIFIED", "PENDING", "VERIFIED", "REJECTED"]
MemberLevelLiteral = Literal["NORMAL", "SILVER", "GOLD", "PLATINUM"]
RegisterChannelLiteral = Literal["APP", "MINI_PROGRAM", "WEB", "OFFLINE", "FLEET_IMPORT"]
PlateColorLiteral = Literal["GREEN", "BLUE", "YELLOW", "WHITE", "BLACK"]
CouponKindLiteral = Literal["CASH", "DISCOUNT", "SERVICE_FEE_FREE"]
CampaignStatusLiteral = Literal["DRAFT", "ACTIVE", "PAUSED", "ENDED"]
IssueChannelLiteral = Literal["NEW_USER_PACK", "OPS_GRANT", "ACTIVITY", "COMPENSATION"]

# 车牌：省级简称 + 发牌机关字母 + 5~6 位字母数字（新能源 8 位，普通 7 位）
_PLATE_PATTERN = r"^[\u4e00-\u9fa5][A-Z][A-Z0-9]{5,6}$"
_PHONE_PATTERN = r"^1[3-9]\d{9}$"


class UserRegisterIn(BaseModel):
    """平台管理员代开账号（普通车主与企业车队走 /api/auth/register 自助注册）。"""

    username: str = Field(
        min_length=2,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_\u4e00-\u9fa5]+$",
        description="登录用户名",
    )
    password: str = Field(min_length=6, max_length=128, description="登录口令（6~128位）")
    phone: str = Field(pattern=_PHONE_PATTERN, description="11位手机号，入库前加密+盲索引+脱敏")
    role: RoleEnum = Field(default=RoleEnum.OPS, description="代开角色：ops / settlement / admin / fleet")
    fleet_id: int | None = Field(default=None, ge=1, description="所属企业车队ID（仅 fleet 角色必填）")
    realname_status: RealnameStatusLiteral = Field(default="VERIFIED", description="代开账号默认已实名")
    member_level: MemberLevelLiteral = Field(default="NORMAL", description="会员等级")
    register_channel: RegisterChannelLiteral = Field(default="OFFLINE", description="注册渠道")

    @model_validator(mode="after")
    def _validate_role_and_fleet(self) -> "UserRegisterIn":
        """禁止代开车主账号；车队角色必须绑定 fleet_id，其余角色强制清空防越权。"""
        if self.role == RoleEnum.DRIVER:
            raise ValueError("普通车主请通过 /api/auth/register 自助注册，不支持后台代开")
        if self.role == RoleEnum.FLEET:
            if self.fleet_id is None:
                raise ValueError("企业车队角色必须绑定所属 fleet_id")
        else:
            self.fleet_id = None
        return self


class UserMaskedOut(BaseModel):
    """车主/员工脱敏档案输出（对应视图 v_user_masked_profile，不含密文与密码哈希）。"""

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    username: str
    role: str
    fleet_id: int | None = None
    fleet_name: str | None = None
    phone_masked: str | None = None
    realname_status: str
    member_level: str
    balance: Decimal
    register_channel: str
    status: str
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    vehicle_cnt: int = 0
    usable_coupon_cnt: int = 0


class UserStatusIn(BaseModel):
    """管理员冻结/解冻/禁用账号（冻结后登录被 403 拦截）。"""

    status: UserStatusLiteral = Field(description="目标状态：ACTIVE-正常, FROZEN-冻结, DISABLED-禁用")


class VehicleBindIn(BaseModel):
    """车主绑定车辆：车牌明文入参，DAO 层负责加密、盲索引与脱敏三列落库。"""

    plate_no: str = Field(pattern=_PLATE_PATTERN, description="车牌号明文（如 京AD12345）")
    plate_color: PlateColorLiteral = Field(default="GREEN", description="号牌颜色")
    vehicle_model: str = Field(min_length=2, max_length=64, description="车型（如 比亚迪 汉 EV）")
    battery_capacity_kwh: Decimal = Field(gt=0, le=Decimal("1000"), description="电池容量（kWh）")
    max_charge_power: Decimal = Field(gt=0, le=Decimal("500"), description="最大充电功率（kW）")
    supports_fast_charge: bool = Field(default=True, description="是否支持直流快充")

    @field_validator("plate_no", mode="before")
    @classmethod
    def _normalize_plate(cls, value: object) -> object:
        """先归一化再走 pattern 校验：去除空白并统一大写。

        必须用 mode="before"，否则小写车牌会在 pattern 校验阶段就被判非法
        （例如前端传 "京ad12345" 时应归一化为 "京AD12345" 而不是直接报错）。
        """
        if isinstance(value, str):
            cleaned = value.strip().upper()
            if not cleaned:
                raise ValueError("车牌号不能为空")
            return cleaned
        return value

    @model_validator(mode="after")
    def _check_fast_charge_power(self) -> "VehicleBindIn":
        """快充一致性：声明支持直流快充的车辆，最大充电功率应不低于 30 kW。"""
        if self.supports_fast_charge and self.max_charge_power < Decimal("30"):
            raise ValueError("支持直流快充的车辆，最大充电功率不得低于 30 kW")
        return self


class VehicleOut(BaseModel):
    """车辆绑定信息输出（对应视图 v_vehicle_binding，车牌只输出脱敏值）。"""

    model_config = ConfigDict(from_attributes=True)

    vehicle_id: int
    user_id: int | None = None
    username: str | None = None
    fleet_id: int | None = None
    fleet_name: str | None = None
    plate_masked: str
    plate_color: str
    vehicle_model: str
    battery_capacity_kwh: Decimal
    max_charge_power: Decimal
    supports_fast_charge: bool
    bind_status: str
    bound_at: datetime | None = None
    created_at: datetime | None = None


class FleetCardIn(BaseModel):
    """企业车队开卡：建档并授予月度授信额度（对接人手机号同样加密+脱敏存储）。"""

    fleet_code: str = Field(
        min_length=4, max_length=32, pattern=r"^[A-Z0-9\-]+$", description="车队编号（如 FL-BJ-006）"
    )
    fleet_name: str = Field(min_length=2, max_length=120, description="企业名称")
    credit_code: str | None = Field(
        default=None, pattern=r"^[0-9A-Z]{18}$", description="统一社会信用代码（18位）"
    )
    contact_name: str = Field(min_length=2, max_length=50, description="企业对接人")
    contact_phone: str = Field(pattern=_PHONE_PATTERN, description="对接人手机号明文")
    credit_limit: Decimal = Field(
        default=Decimal("0.00"), ge=0, le=Decimal("10000000"), description="月度授信额度（元）"
    )


class FleetCreditOut(BaseModel):
    """车队授信与消费概览（对应视图 v_fleet_credit_usage，按 fleet_id 行级隔离）。"""

    model_config = ConfigDict(from_attributes=True)

    fleet_id: int
    fleet_code: str
    fleet_name: str
    contact_name: str
    contact_phone_masked: str | None = None
    status: str
    credit_limit: Decimal
    credit_used: Decimal
    credit_remaining: Decimal
    bound_vehicle_cnt: int = 0
    member_cnt: int = 0
    month_order_cnt: int = 0
    month_order_amount: Decimal = Decimal("0.00")


class CampaignIn(BaseModel):
    """创建优惠活动：面额、门槛、适用场站、有效期与发放规则。"""

    campaign_code: str = Field(
        min_length=4, max_length=32, pattern=r"^[A-Z0-9\-]+$", description="活动编号（如 CP-202612-NEW）"
    )
    campaign_name: str = Field(min_length=2, max_length=120, description="活动名称")
    coupon_kind: CouponKindLiteral = Field(default="CASH", description="券类型")
    face_value: Decimal = Field(gt=0, le=Decimal("500"), description="券面额（元）")
    threshold_amount: Decimal = Field(
        default=Decimal("0.00"), ge=0, le=Decimal("10000"), description="使用门槛（元，0 表示无门槛）"
    )
    applicable_station_id: int | None = Field(default=None, ge=1, description="适用场站ID，空为全场站")
    valid_from: date = Field(description="生效日期")
    valid_to: date = Field(description="失效日期")
    issue_total: int = Field(gt=0, le=1000000, description="计划发放总量")
    per_user_limit: int = Field(default=1, ge=1, le=20, description="每人限领张数")
    redeem_rule: str = Field(
        default="单笔订单限用一张，不可与其他优惠叠加",
        max_length=255,
        description="核销规则说明",
    )
    status: CampaignStatusLiteral = Field(default="DRAFT", description="活动状态")

    @model_validator(mode="after")
    def _check_window(self) -> "CampaignIn":
        """有效期必须先后有序；折扣/减免券面额不得高于使用门槛（否则等于白送）。"""
        if self.valid_to < self.valid_from:
            raise ValueError("活动失效日期不得早于生效日期")
        if self.coupon_kind in ("DISCOUNT", "SERVICE_FEE_FREE") and self.threshold_amount > 0:
            if self.face_value > self.threshold_amount:
                raise ValueError("折扣/减免类券面额不得高于使用门槛")
        return self


class CampaignOut(BaseModel):
    """活动投放与核销看板输出（对应视图 v_coupon_campaign_stat）。"""

    model_config = ConfigDict(from_attributes=True)

    campaign_id: int
    campaign_code: str
    campaign_name: str
    coupon_kind: str
    face_value: Decimal
    threshold_amount: Decimal
    applicable_station_id: int | None = None
    station_name: str | None = None
    valid_from: date
    valid_to: date
    issue_total: int
    issued_count: int
    per_user_limit: int
    status: str
    redeem_rule: str
    redeemed_cnt: int = 0
    redeemed_amount: Decimal = Decimal("0.00")
    redeem_rate: Decimal | None = None


class CouponIssueIn(BaseModel):
    """运营定向发券：指定持券人与张数，逐张过发券触发器校验。"""

    user_id: int = Field(gt=0, description="持券人ID")
    count: int = Field(default=1, ge=1, le=20, description="发放张数（受每人限领约束）")
    channel: IssueChannelLiteral = Field(default="OPS_GRANT", description="发放渠道")


class CouponIssueOut(BaseModel):
    """发券结果回执。"""

    campaign_id: int
    user_id: int
    issued_count: int = Field(description="本次实际发放张数")
    coupon_nos: list[str] = Field(default_factory=list, description="本次生成的券码")


class CouponOut(BaseModel):
    """车主券包输出（对应视图 v_coupon_user_wallet）。"""

    model_config = ConfigDict(from_attributes=True)

    coupon_id: int
    coupon_no: str
    user_id: int
    campaign_id: int
    campaign_name: str
    coupon_kind: str
    applicable_station_id: int | None = None
    face_value: Decimal
    threshold_amount: Decimal
    issue_channel: str
    issued_at: datetime | None = None
    expire_at: date
    status: str
    redeem_rule: str
    usable_flag: int = 0
    days_to_expire: int = 0


class CouponRedeemIn(BaseModel):
    """核销优惠券：券码 + 订单号（一单一券、一券一销由唯一索引与触发器共同保证）。"""

    coupon_no: str = Field(min_length=4, max_length=32, description="券码")
    order_id: int = Field(gt=0, description="核销对应的充电订单ID")

    @field_validator("coupon_no", mode="before")
    @classmethod
    def _normalize_coupon_no(cls, value: object) -> object:
        """券码统一去空白并大写，避免大小写不一致导致查不到券。"""
        if isinstance(value, str):
            cleaned = value.strip().upper()
            if not cleaned:
                raise ValueError("券码不能为空")
            return cleaned
        return value


class CouponRedeemOut(BaseModel):
    """核销结果回执：抵扣金额与核销后的订单应付金额。"""

    coupon_no: str
    order_id: int
    face_value: Decimal = Field(description="实际抵扣金额（元）")
    order_amount: Decimal = Field(description="核销前订单金额（元）")
    payable_amount: Decimal = Field(description="核销后订单应付金额（元）")


__all__ = [
    "CampaignIn",
    "CampaignOut",
    "CouponIssueIn",
    "CouponIssueOut",
    "CouponOut",
    "CouponRedeemIn",
    "CouponRedeemOut",
    "FleetCardIn",
    "FleetCreditOut",
    "UserMaskedOut",
    "UserRegisterIn",
    "UserStatusIn",
    "VehicleBindIn",
    "VehicleOut",
]
