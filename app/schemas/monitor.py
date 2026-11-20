"""业务线3：设备监控与工单（成员3/代润森） —— Pydantic v2 请求与响应模型。

规范要点：
1. 遥测电压/电流/功率/温度在入参层完成合理区间校验，功率全程保留 Decimal；
2. 派单/接单/到场/修复/复核各时间节点必须严格递增；
3. 告警等级、告警状态、工单状态统一使用 Literal 白名单，禁止自由字符串入参。
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AlarmLevel = Literal["L1", "L2", "L3", "L4"]
AlarmStatus = Literal["ACTIVE", "PROCESSING", "RECOVERED", "CLOSED"]
WorkOrderStatus = Literal[
    "DISPATCHED", "ACCEPTED", "ARRIVED", "REPAIRED", "VERIFIED", "CLOSED"
]
DeviceStatus = Literal["IDLE", "OCCUPIED", "FAULT", "OFFLINE"]


class TelemetryIn(BaseModel):
    """分钟级遥测上报入参。"""

    pile_id: int = Field(ge=1, description="充电桩ID")
    collected_at: datetime = Field(description="采集时间（毫秒精度）")
    output_voltage: Decimal | None = Field(default=None, ge=0, le=1000, description="输出电压(V)")
    output_current: Decimal | None = Field(default=None, ge=0, le=1000, description="输出电流(A)")
    power: Decimal = Field(default=Decimal("0"), ge=0, le=1000, description="当前功率(kW)")
    module_temp: Decimal | None = Field(default=None, ge=-40, le=150, description="模块温度(℃)")
    offline_duration_sec: int = Field(
        default=0,
        ge=0,
        le=31_536_000,
        description="离线时长(秒)，上限一年",
    )
    fault_code: str | None = Field(default=None, max_length=32, description="故障码，无故障为空")

    @field_validator("fault_code", mode="before")
    @classmethod
    def _normalize_fault_code(cls, value: object) -> object:
        """空白故障码统一转 None，避免空字符串绕过“无故障”语义。"""
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value

    @model_validator(mode="after")
    def _check_telemetry_consistency(self) -> Self:
        """在线运行时不应携带离线时长；携带故障码时必须给出故障等级判定条件。"""
        if self.offline_duration_sec > 0 and self.fault_code is None:
            raise ValueError("离线时长大于 0 时必须上报 fault_code")
        return self


class RealtimeTelemetryOut(BaseModel):
    """最新遥测响应模型（金额/功率保持 Decimal）。"""

    model_config = ConfigDict(from_attributes=True)

    pile_id: int
    station_id: int
    station_code: str
    station_name: str
    pile_code: str
    pile_status: DeviceStatus
    collected_at: datetime
    output_voltage: Decimal | None
    output_current: Decimal | None
    power: Decimal
    module_temp: Decimal | None
    offline_duration_sec: int
    fault_code: str | None
    heartbeat_lag_sec: int | None


class AlarmOut(BaseModel):
    """告警详情响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    alarm_id: int
    pile_id: int
    gun_id: int | None
    alarm_code: str
    alarm_level: AlarmLevel
    original_level: AlarmLevel
    response_limit_min: int
    occurred_at: datetime
    recovered_at: datetime | None
    status: AlarmStatus
    escalated_count: int
    escalated_at: datetime | None
    latest_maintainer_id: int | None
    overdue_flag: bool


class RepairDispatchIn(BaseModel):
    """告警派发维修工单入参。"""

    maintainer_id: int = Field(ge=1, description="维修人员 user_id")
    dispatched_at: datetime | None = Field(default=None, description="派单时间，缺省取数据库当前时间")


class RepairAcceptIn(BaseModel):
    """运维接单入参。"""

    accepted_at: datetime | None = Field(default=None, description="接单时间")


class RepairArriveIn(BaseModel):
    """运维到场入参。"""

    arrived_at: datetime | None = Field(default=None, description="到场时间")


class RepairCompleteIn(BaseModel):
    """运维完成维修入参。"""

    repaired_at: datetime | None = Field(default=None, description="修复时间")
    replaced_parts: str | None = Field(default=None, max_length=255, description="更换备件")
    result: str = Field(min_length=2, max_length=255, description="处理结果说明")


class RepairCloseIn(BaseModel):
    """工单复核关闭入参。"""

    verified_at: datetime | None = Field(default=None, description="复核时间")


class EscalateIn(BaseModel):
    """告警 SLA 超时升级入参。"""

    check_time: datetime | None = Field(default=None, description="检查时间，缺省取当前时间")


class RepairOrderOut(BaseModel):
    """维修工单响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    work_order_id: int
    alarm_id: int
    maintainer_id: int
    status: WorkOrderStatus
    dispatched_at: datetime
    accepted_at: datetime | None
    arrived_at: datetime | None
    repaired_at: datetime | None
    verified_at: datetime | None
    closed_at: datetime | None
    replaced_parts: str | None
    result: str | None
    response_minutes: int
    overdue_flag: bool


__all__ = [
    "AlarmLevel",
    "AlarmStatus",
    "AlarmOut",
    "DeviceStatus",
    "EscalateIn",
    "RealtimeTelemetryOut",
    "RepairAcceptIn",
    "RepairArriveIn",
    "RepairCloseIn",
    "RepairCompleteIn",
    "RepairDispatchIn",
    "RepairOrderOut",
    "TelemetryIn",
    "WorkOrderStatus",
]
