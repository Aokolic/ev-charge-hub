"""业务线1：场站与充电设施（成员1/组长） —— Pydantic v2 请求与响应模型（金额、功率、经纬度保留 Decimal 精度）。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# 1. 入参校验模型（In）
# ============================================================================


class OperatorCreateIn(BaseModel):
    """新增合作运营商入参。"""

    operator_code: str = Field(min_length=3, max_length=32, description="运营商业务编码（3~32位）")
    operator_name: str = Field(min_length=2, max_length=100, description="运营商全称（2~100位）")
    credit_code: str = Field(min_length=18, max_length=18, description="统一社会信用代码（固定18位）")
    contact_name: str = Field(min_length=1, max_length=50, description="业务对接联系人")
    service_phone: str = Field(min_length=5, max_length=20, description="企业客服热线")
    status: Literal["ACTIVE", "SUSPENDED"] = Field(default="ACTIVE", description="合作状态")


class OperatorUpdateIn(BaseModel):
    """更新运营商档案入参（仅更新非空字段）。"""

    operator_name: str | None = Field(default=None, min_length=2, max_length=100, description="运营商全称")
    contact_name: str | None = Field(default=None, min_length=1, max_length=50, description="业务对接联系人")
    service_phone: str | None = Field(default=None, min_length=5, max_length=20, description="企业客服热线")
    status: Literal["ACTIVE", "SUSPENDED"] | None = Field(default=None, description="合作状态")


class StationCreateIn(BaseModel):
    """新增充电场站入参。

    注意：故意不在 Schema 层拦截 available_spaces > total_spaces，
    以便由数据库 CHECK 约束 chk_station_avail_spaces (3819) 统一兜底校验。
    """

    operator_id: int = Field(gt=0, description="所属运营商 ID")
    station_code: str = Field(min_length=3, max_length=32, description="场站唯一编号（3~32位）")
    station_name: str = Field(min_length=2, max_length=120, description="场站名称（2~120位）")
    address: str = Field(min_length=2, max_length=255, description="场站详细地址")
    longitude: Decimal = Field(
        ge=Decimal("-180"), le=Decimal("180"), description="经度（-180.000000 ~ 180.000000）"
    )
    latitude: Decimal = Field(
        ge=Decimal("-90"), le=Decimal("90"), description="纬度（-90.000000 ~ 90.000000）"
    )
    total_spaces: int = Field(gt=0, description="车位总数（须大于 0）")
    available_spaces: int = Field(ge=0, description="当前空闲车位数")
    business_hours: str = Field(
        default="00:00-24:00", min_length=1, max_length=64, description="营业时间描述"
    )
    parking_fee_rate: Decimal = Field(
        default=Decimal("0.00"), ge=Decimal("0.00"), description="停车费标准（元/小时）"
    )
    station_status: Literal["OPEN", "MAINTENANCE", "CLOSED"] = Field(
        default="OPEN", description="场站状态"
    )


class StationUpdateIn(BaseModel):
    """更新充电场站信息入参（仅更新非空字段）。"""

    station_name: str | None = Field(default=None, min_length=2, max_length=120, description="场站名称")
    address: str | None = Field(default=None, min_length=2, max_length=255, description="场站详细地址")
    total_spaces: int | None = Field(default=None, gt=0, description="车位总数")
    available_spaces: int | None = Field(default=None, ge=0, description="当前空闲车位数")
    business_hours: str | None = Field(
        default=None, min_length=1, max_length=64, description="营业时间描述"
    )
    parking_fee_rate: Decimal | None = Field(
        default=None, ge=Decimal("0.00"), description="停车费标准（元/小时）"
    )
    station_status: Literal["OPEN", "MAINTENANCE", "CLOSED"] | None = Field(
        default=None, description="场站状态"
    )


class PileCreateIn(BaseModel):
    """新增充电桩入参。"""

    station_id: int = Field(gt=0, description="所属场站 ID")
    pile_code: str = Field(min_length=3, max_length=32, description="充电桩唯一编号（3~32位）")
    device_model: str = Field(min_length=1, max_length=64, description="设备型号（现场铭牌核对项）")
    rated_power: Decimal = Field(gt=Decimal("0"), description="额定总功率（kW）")
    gun_count: int = Field(ge=1, le=8, description="设计配备充电枪数量（1~8）")
    protocol_type: str = Field(
        default="OCPP1.6", min_length=1, max_length=32, description="通信协议（如 OCPP1.6）"
    )
    commissioned_date: date | None = Field(default=None, description="投运日期（验收投运前可为空）")
    warranty_end_date: date = Field(description="质保截止日期")
    acceptance_status: Literal["PENDING", "PASSED", "REJECTED"] = Field(
        default="PENDING", description="协议联调与验收状态"
    )
    current_status: Literal["IDLE", "OCCUPIED", "FAULT", "OFFLINE"] = Field(
        default="OFFLINE", description="当前状态"
    )
    offline_threshold_sec: int = Field(
        default=180, ge=30, description="心跳离线判定阈值（秒，最低 30 秒）"
    )


class GunCreateIn(BaseModel):
    """新增充电枪入参（单枪功率 <= 所属桩额定功率由数据库触发器校验）。"""

    pile_id: int = Field(gt=0, description="所属充电桩 ID")
    gun_code: str = Field(min_length=3, max_length=32, description="充电枪唯一编号（3~32位）")
    gun_seq: int = Field(ge=1, description="桩内枪序号（从 1 开始）")
    gun_type: Literal["DC_FAST", "AC_SLOW"] = Field(description="枪型（直流快充/交流慢充）")
    connector_standard: str = Field(
        min_length=1, max_length=32, description="接口标准（如 GB/T 20234.3）"
    )
    max_power: Decimal = Field(gt=Decimal("0"), description="单枪最大输出功率（kW）")
    current_status: Literal["IDLE", "OCCUPIED", "FAULT", "OFFLINE"] = Field(
        default="OFFLINE", description="当前状态"
    )


class DeviceStatusUpdateIn(BaseModel):
    """充电桩/充电枪状态流转入参（通过数据库触发器自动记录 device_status_log）。"""

    new_status: Literal["IDLE", "OCCUPIED", "FAULT", "OFFLINE"] = Field(description="变更后状态")
    change_source: Literal["INIT", "ACCEPTANCE", "ORDER", "HEARTBEAT", "MANUAL", "ALARM"] = Field(
        default="MANUAL", description="变更触发来源"
    )
    todo_status: Literal["NONE", "PENDING", "DONE"] | None = Field(
        default=None, description="运维待办状态（为空时由触发器按故障/离线规则自动设定）"
    )
    remark: str | None = Field(default=None, max_length=255, description="变更说明")


class PileCommissionIn(BaseModel):
    """充电桩协议联调验收与投运入参。"""

    commissioned_date: date | None = Field(default=None, description="投运日期（留空默认当天）")
    remark: str | None = Field(
        default="现场铭牌及协议联调验收通过", max_length=255, description="验收备注"
    )


class HeartbeatCheckIn(BaseModel):
    """心跳离线超时巡检入参（留空由存储过程取数据库当前 UTC 时间）。"""

    check_time: datetime | None = Field(default=None, description="巡检基准时间")


class TodoResolveIn(BaseModel):
    """运维待办办结入参。"""

    remark: str | None = Field(
        default="运维已排查恢复并办结待办", max_length=255, description="待办办结说明"
    )


# ============================================================================
# 2. 实体与过程输出模型（Out）
# ============================================================================


class OperatorOut(BaseModel):
    """运营商档案响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    operator_id: int
    operator_code: str
    operator_name: str
    credit_code: str
    contact_name: str
    service_phone: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StationOut(BaseModel):
    """充电场站响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    station_id: int
    operator_id: int
    station_code: str
    station_name: str
    address: str
    longitude: Decimal
    latitude: Decimal
    total_spaces: int
    available_spaces: int
    business_hours: str
    parking_fee_rate: Decimal
    station_status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PileOut(BaseModel):
    """充电桩响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    pile_id: int
    station_id: int
    pile_code: str
    device_model: str
    rated_power: Decimal
    gun_count: int
    protocol_type: str
    commissioned_date: date | None = None
    warranty_end_date: date
    acceptance_status: str
    current_status: str
    offline_threshold_sec: int
    last_heartbeat_at: datetime | None = None
    status_updated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GunOut(BaseModel):
    """充电枪响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    gun_id: int
    pile_id: int
    gun_code: str
    gun_seq: int
    gun_type: str
    connector_standard: str
    max_power: Decimal
    current_status: str
    status_updated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DeviceStatusLogOut(BaseModel):
    """设备状态变更日志响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    log_id: int
    device_type: str
    pile_id: int | None = None
    gun_id: int | None = None
    prev_status: str | None = None
    new_status: str
    change_source: str
    todo_status: str
    remark: str | None = None
    changed_at: datetime | None = None


class HeartbeatCheckOut(BaseModel):
    """心跳离线巡检存储过程回执模型。"""

    model_config = ConfigDict(from_attributes=True)

    affected_piles: int = Field(ge=0, description="本次判定离线的充电桩数量")
    affected_guns: int = Field(ge=0, description="本次联动置为离线的充电枪数量")
    message: str = Field(description="巡检执行结果说明")


# ============================================================================
# 3. RBAC 隔离视图输出模型（与 sql/02_views.sql 五个视图列名严格对齐）
# ============================================================================


class DriverStationOverviewOut(BaseModel):
    """视图 1.1 v_driver_station_overview（面向 driver / fleet）响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    station_id: int
    station_code: str
    station_name: str
    operator_name: str
    address: str
    longitude: Decimal
    latitude: Decimal
    business_hours: str
    parking_fee_rate: Decimal
    total_spaces: int
    available_spaces: int
    active_pile_count: int
    total_gun_count: int
    idle_gun_count: int
    idle_fast_gun_count: int
    idle_slow_gun_count: int


class DriverGunAvailabilityOut(BaseModel):
    """视图 1.2 v_driver_gun_availability（面向 driver / fleet）响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    gun_id: int
    gun_code: str
    gun_seq: int
    gun_type: str
    connector_standard: str
    max_power: Decimal
    gun_status: str
    pile_id: int
    pile_code: str
    rated_power: Decimal
    pile_status: str
    station_id: int
    station_code: str
    station_name: str
    business_hours: str


class FaultGunDetailOut(BaseModel):
    """视图 1.3 v_fault_gun_detail（面向 ops / admin）响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    operator_id: int
    operator_name: str
    service_phone: str
    station_id: int
    station_code: str
    station_name: str
    address: str
    pile_id: int
    pile_code: str
    device_model: str
    protocol_type: str
    pile_status: str
    offline_threshold_sec: int
    last_heartbeat_at: datetime | None = None
    heartbeat_lag_sec: int | None = None
    pile_status_updated_at: datetime
    gun_id: int | None = None
    gun_code: str | None = None
    gun_seq: int | None = None
    gun_type: str | None = None
    connector_standard: str | None = None
    max_power: Decimal | None = None
    gun_status: str | None = None
    gun_status_updated_at: datetime | None = None


class DeviceStatusHistoryOut(BaseModel):
    """视图 1.4 v_device_status_history（面向 ops / admin）响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    log_id: int
    station_id: int
    station_code: str
    station_name: str
    device_type: str
    pile_id: int
    pile_code: str
    gun_id: int | None = None
    gun_code: str | None = None
    gun_seq: int | None = None
    prev_status: str | None = None
    new_status: str
    change_source: str
    todo_status: str
    remark: str | None = None
    changed_at: datetime


class StationRealtimeStatusOut(BaseModel):
    """视图 1.5 v_station_realtime_status（面向 admin / settlement）响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    station_id: int
    station_code: str
    station_name: str
    station_status: str
    operator_id: int
    operator_code: str
    operator_name: str
    operator_status: str
    address: str
    business_hours: str
    parking_fee_rate: Decimal
    total_spaces: int
    available_spaces: int
    total_piles: int
    pending_acceptance_piles: int
    idle_piles: int
    occupied_piles: int
    fault_piles: int
    offline_piles: int
    total_rated_power: Decimal
    total_guns: int
    idle_guns: int
    occupied_guns: int
    fault_guns: int
    offline_guns: int
    updated_at: datetime


__all__ = [
    "OperatorCreateIn",
    "OperatorUpdateIn",
    "StationCreateIn",
    "StationUpdateIn",
    "PileCreateIn",
    "GunCreateIn",
    "DeviceStatusUpdateIn",
    "PileCommissionIn",
    "HeartbeatCheckIn",
    "TodoResolveIn",
    "OperatorOut",
    "StationOut",
    "PileOut",
    "GunOut",
    "DeviceStatusLogOut",
    "HeartbeatCheckOut",
    "DriverStationOverviewOut",
    "DriverGunAvailabilityOut",
    "FaultGunDetailOut",
    "DeviceStatusHistoryOut",
    "StationRealtimeStatusOut",
]
