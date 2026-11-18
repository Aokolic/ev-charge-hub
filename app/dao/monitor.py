"""业务线3：设备监控与工单（成员3/代润森） —— 数据访问层。

双轨实现：
1. 遥测批量写入、工单状态流转等常规写操作使用 SQLAlchemy 2.0 ORM，并在同一
   Session 内显式 commit，跨表操作只提交一次；
2. 实时状态、告警看板、工单闭环等列表查询统一通过 text() 参数化查询 RBAC 隔离
   视图，原始表不直接暴露给前端；
3. 告警 SLA 超时升级调用存储过程 proc_escalate_overdue_alarms。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth import RoleEnum
from app.dao.common import fetch_view_page
from app.models.monitor import AlarmRecord, PileTelemetry, RepairOrder
from app.schemas.common import PageParams
from app.schemas.monitor import RepairDispatchIn, TelemetryIn


class MonitorError(Exception):
    """业务线3 领域异常：携带 HTTP 状态码，由路由层统一转换为 HTTPException。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


_ALARM_BASE_SQL = (
    "SELECT alarm_id, pile_id, gun_id, alarm_code, alarm_level, original_level, "
    "response_limit_min, occurred_at, recovered_at, status, escalated_count, "
    "escalated_at, latest_maintainer_id, overdue_flag "
    "FROM v_monitor_alarm_overview"
)

_WORKORDER_BASE_SQL = (
    "SELECT work_order_id, alarm_id, maintainer_id, status, dispatched_at, "
    "accepted_at, arrived_at, repaired_at, verified_at, closed_at, "
    "replaced_parts, result, response_minutes, overdue_flag "
    "FROM v_monitor_workorder_closure"
)

_REALTIME_SQL = (
    "SELECT pile_id, station_id, pile_code, station_name, collected_at, "
    "output_voltage, output_current, power, module_temp, offline_duration_sec, "
    "fault_code, heartbeat_lag_sec, pile_status "
    "FROM v_monitor_realtime_status"
)

_ORDER_BY_ALARM = "occurred_at DESC, alarm_id DESC"
_ORDER_BY_WORKORDER = "dispatched_at DESC, work_order_id DESC"


def _now() -> datetime:
    """统一取应用侧当前时间，保证同一事务内各时间字段一致。"""
    return datetime.now()


def _commit_or_rollback(db: Session) -> None:
    """显式提交；若底层约束/触发器报错，先回滚再原样上抛交给 main.py 全局翻译。"""
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def create_telemetry_batch(db: Session, items: list[TelemetryIn]) -> int:
    """批量写入分钟级遥测（事实表 append-only），返回成功写入条数。"""
    rows = [
        PileTelemetry(
            pile_id=item.pile_id,
            collected_at=item.collected_at,
            output_voltage=item.output_voltage,
            output_current=item.output_current,
            power=item.power,
            module_temp=item.module_temp,
            offline_duration_sec=item.offline_duration_sec,
            fault_code=item.fault_code,
        )
        for item in items
    ]
    db.add_all(rows)
    _commit_or_rollback(db)
    return len(rows)


def get_latest_telemetry(db: Session, pile_id: int) -> dict[str, Any]:
    """按桩查询实时状态视图中的最新一条遥测。"""
    row = db.execute(
        text(f"{_REALTIME_SQL} WHERE pile_id = :pile_id"),
        {"pile_id": pile_id},
    ).mappings().first()
    if row is None:
        raise MonitorError("未查询到该充电桩的最新遥测数据", status_code=404)
    return dict(row)


def list_alarms(
    db: Session,
    page: PageParams,
    role: RoleEnum,
    maintainer_id: int,
    overdue_only: bool = False,
) -> dict[str, Any]:
    """分页查询告警看板：ops 仅能查看已派发给本人的告警，admin 可查看全部。"""
    conditions = ["latest_maintainer_id = :maintainer_id"] if role == RoleEnum.OPS else []
    if overdue_only:
        conditions.append("overdue_flag = 1")
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return fetch_view_page(
        db,
        _ALARM_BASE_SQL + where_clause,
        {"maintainer_id": maintainer_id} if role == RoleEnum.OPS else None,
        page,
        order_by=_ORDER_BY_ALARM,
    )


def list_workorders(
    db: Session,
    page: PageParams,
    role: RoleEnum,
    maintainer_id: int,
) -> dict[str, Any]:
    """分页查询维修工单：ops 仅能查看派发给本人的工单，admin 可查看全部。"""
    if role == RoleEnum.OPS:
        base_sql = _WORKORDER_BASE_SQL + " WHERE maintainer_id = :maintainer_id"
        params = {"maintainer_id": maintainer_id}
    else:
        base_sql = _WORKORDER_BASE_SQL
        params = None
    return fetch_view_page(db, base_sql, params, page, order_by=_ORDER_BY_WORKORDER)


def get_workorder(
    db: Session,
    work_order_id: int,
    role: RoleEnum,
    maintainer_id: int,
) -> dict[str, Any]:
    """查询单个工单，并对 ops 角色强制校验归属隔离。"""
    row = db.execute(
        text(f"{_WORKORDER_BASE_SQL} WHERE work_order_id = :work_order_id"),
        {"work_order_id": work_order_id},
    ).mappings().first()
    if row is None:
        raise MonitorError("维修工单不存在", status_code=404)
    if role == RoleEnum.OPS and int(row["maintainer_id"]) != maintainer_id:
        raise MonitorError("无权查看他人的维修工单", status_code=403)
    return dict(row)


def dispatch_repair_order(
    db: Session,
    payload: RepairDispatchIn,
) -> int:
    """为指定告警派发维修工单，并在同一事务内将告警从 ACTIVE 推进到 PROCESSING。"""
    now = _now()
    alarm = db.get(AlarmRecord, payload.alarm_id)
    if alarm is None:
        raise MonitorError("告警不存在", status_code=404)
    if alarm.status == "CLOSED":
        raise MonitorError("已关闭告警不能再次派发维修工单", status_code=409)

    order = RepairOrder(
        alarm_id=payload.alarm_id,
        maintainer_id=payload.maintainer_id,
        status="DISPATCHED",
        dispatched_at=payload.dispatched_at or now,
        created_at=now,
        updated_at=now,
    )
    db.add(order)
    db.flush()

    if alarm.status == "ACTIVE":
        alarm.status = "PROCESSING"
        alarm.updated_at = now

    _commit_or_rollback(db)
    return order.work_order_id


def _require_owned_order(
    db: Session,
    work_order_id: int,
    maintainer_id: int,
) -> RepairOrder:
    order = db.get(RepairOrder, work_order_id)
    if order is None:
        raise MonitorError("维修工单不存在", status_code=404)
    if order.maintainer_id != maintainer_id:
        raise MonitorError("无权操作他人的维修工单", status_code=403)
    return order


def _transition(
    db: Session,
    work_order_id: int,
    maintainer_id: int,
    expected_status: str,
    target_status: str,
    **fields: Any,
) -> None:
    """工单状态机通用推进：校验当前状态后更新并一次提交。"""
    order = _require_owned_order(db, work_order_id, maintainer_id)
    if order.status != expected_status:
        raise MonitorError(
            f"当前工单状态为 {order.status}，不能直接变更为 {target_status}",
            status_code=409,
        )
    for name, value in fields.items():
        setattr(order, name, value)
    order.status = target_status
    order.updated_at = _now()
    _commit_or_rollback(db)


def accept_repair_order(
    db: Session,
    work_order_id: int,
    maintainer_id: int,
    accepted_at: datetime | None,
) -> None:
    _transition(
        db,
        work_order_id,
        maintainer_id,
        "DISPATCHED",
        "ACCEPTED",
        accepted_at=accepted_at or _now(),
    )


def arrive_repair_order(
    db: Session,
    work_order_id: int,
    maintainer_id: int,
    arrived_at: datetime | None,
) -> None:
    _transition(
        db,
        work_order_id,
        maintainer_id,
        "ACCEPTED",
        "ARRIVED",
        arrived_at=arrived_at or _now(),
    )


def complete_repair_order(
    db: Session,
    work_order_id: int,
    maintainer_id: int,
    repaired_at: datetime | None,
    replaced_parts: str | None,
    result: str,
) -> None:
    _transition(
        db,
        work_order_id,
        maintainer_id,
        "ARRIVED",
        "REPAIRED",
        repaired_at=repaired_at or _now(),
        replaced_parts=replaced_parts,
        result=result,
    )


def close_repair_order(
    db: Session,
    work_order_id: int,
    maintainer_id: int,
    verified_at: datetime | None,
) -> str:
    """复核关闭工单，并在同告警全部工单闭环且设备最近遥测正常时解除告警。"""
    now = _now()
    order = _require_owned_order(db, work_order_id, maintainer_id)
    if order.status != "REPAIRED":
        raise MonitorError(
            f"当前工单状态为 {order.status}，必须先完成维修后才能复核关闭",
            status_code=409,
        )

    order.status = "CLOSED"
    order.verified_at = verified_at or now
    order.closed_at = now
    order.updated_at = now
    # autoflush=False，显式 flush 后再统计同告警未闭环工单，避免把当前工单误算进去
    db.flush()

    open_count = db.scalar(
        select(func.count())
        .select_from(RepairOrder)
        .where(
            RepairOrder.alarm_id == order.alarm_id,
            RepairOrder.status.notin_(["CLOSED"]),
        )
    ) or 0

    message = "维修工单已复核关闭"
    if open_count == 0:
        latest_fault_code = db.execute(
            text(
                "SELECT fault_code FROM pile_telemetry "
                "WHERE pile_id = (SELECT pile_id FROM alarm_record WHERE alarm_id = :alarm_id) "
                "ORDER BY collected_at DESC LIMIT 1"
            ),
            {"alarm_id": order.alarm_id},
        ).scalar()
        if latest_fault_code is None:
            alarm = db.get(AlarmRecord, order.alarm_id)
            if alarm is not None and alarm.status in ("ACTIVE", "PROCESSING"):
                alarm.status = "RECOVERED"
                alarm.recovered_at = now
                alarm.updated_at = now
            message = "维修工单已复核关闭，告警已解除"
        else:
            message = "维修工单已复核关闭，但设备最近遥测仍存在故障，告警保持处理中"

    _commit_or_rollback(db)
    return message


def escalate_overdue_alarms(db: Session, check_time: datetime | None = None) -> int:
    """调用存储过程升级超时未响应告警，返回本次升级条数。"""
    db.execute(
        text("CALL proc_escalate_overdue_alarms(:p_check_time, @p_escalated_count)"),
        {"p_check_time": check_time or _now()},
    )
    db.commit()
    return int(db.execute(text("SELECT @p_escalated_count")).scalar() or 0)


__all__ = [
    "MonitorError",
    "accept_repair_order",
    "arrive_repair_order",
    "close_repair_order",
    "complete_repair_order",
    "create_telemetry_batch",
    "dispatch_repair_order",
    "escalate_overdue_alarms",
    "get_latest_telemetry",
    "get_workorder",
    "list_alarms",
    "list_workorders",
]
