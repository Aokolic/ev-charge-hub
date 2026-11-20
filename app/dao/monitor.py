"""业务线3：设备监控与工单（成员3/代润森） —— 数据访问层。

双轨实现：
1. 遥测批量写入、工单状态流转等常规写操作使用 SQLAlchemy 2.0 ORM，并在同一
   Session 内显式 commit，跨表操作只提交一次；
2. 实时状态、告警看板、工单闭环等列表查询统一通过 text() 参数化查询 RBAC 隔离
   视图，原始表不直接暴露给前端；
3. 告警 SLA 超时升级调用存储过程 proc_escalate_overdue_alarms。
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, func, select, text
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
    "SELECT pile_id, station_id, station_code, pile_code, station_name, collected_at, "
    "output_voltage, output_current, power, module_temp, offline_duration_sec, "
    "fault_code, heartbeat_lag_sec, pile_status "
    "FROM v_monitor_realtime_status"
)

_ORDER_BY_ALARM = "occurred_at DESC, alarm_id DESC"
_ORDER_BY_WORKORDER = "dispatched_at DESC, work_order_id DESC"

_SQL_SET_ALARM_STATUS_VARS = text(
    "SET @status_change_source = 'ALARM', "
    "@status_todo_status = 'DONE', "
    "@status_change_remark = :remark"
)
_SQL_CLEAR_STATUS_VARS = text(
    "SET @status_change_source = NULL, "
    "@status_todo_status = NULL, "
    "@status_change_remark = NULL"
)
_PILE_RECOVERY_REMARK = "维修工单闭环且连续遥测正常，自动恢复空闲"
_GUN_RECOVERY_REMARK = "维修工单闭环且连续遥测正常，充电枪自动恢复空闲"


def _now() -> datetime:
    """统一取应用侧当前时间，保证同一事务内各时间字段一致。"""
    return datetime.now()


def _to_naive_local(value: datetime | None) -> datetime | None:
    """把带时区时间转换为本地无时区时间，兼容 MySQL DATETIME 列。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _checked_time(
    value: datetime | None,
    *,
    now: datetime,
    previous: datetime | None = None,
    label: str,
) -> datetime:
    """校验业务时间：不允许明显未来时间，也不允许早于前置节点。"""
    actual = _to_naive_local(value) or now
    if actual > now + timedelta(seconds=60):
        raise MonitorError(f"{label}不能晚于当前时间", status_code=400)
    if previous is not None and actual < previous:
        raise MonitorError(f"{label}不能早于前置时间节点", status_code=400)
    return actual


def _ensure_active_ops_user(db: Session, user_id: int) -> None:
    """派单前校验维修人员是存在且启用的 ops 账号。"""
    row = db.execute(
        text("SELECT role, status FROM user_account WHERE user_id = :user_id"),
        {"user_id": user_id},
    ).mappings().first()
    if row is None:
        raise MonitorError("维修人员账号不存在", status_code=404)
    if row["role"] != "ops" or row["status"] != "ACTIVE":
        raise MonitorError("维修人员必须是启用状态的 ops 账号", status_code=400)


def _commit_or_rollback(db: Session) -> None:
    """显式提交；若底层约束/触发器报错，先回滚再原样上抛交给 main.py 全局翻译。"""
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def create_telemetry_batch(db: Session, items: list[TelemetryIn]) -> int:
    """批量写入分钟级遥测（事实表 append-only），返回成功写入条数。"""
    if not 1 <= len(items) <= 1000:
        raise MonitorError("单次遥测批量必须在 1~1000 条之间", status_code=400)

    pile_ids = sorted({item.pile_id for item in items})
    pile_rows = db.execute(
        text(
            "SELECT pile_id, rated_power FROM charging_pile "
            "WHERE pile_id IN :pile_ids"
        ).bindparams(bindparam("pile_ids", expanding=True)),
        {"pile_ids": pile_ids},
    ).mappings().all()
    pile_power = {int(row["pile_id"]): row["rated_power"] for row in pile_rows}
    missing = set(pile_ids) - set(pile_power)
    if missing:
        raise MonitorError(f"充电桩不存在：{sorted(missing)}", status_code=404)
    for item in items:
        if item.power > pile_power[item.pile_id]:
            raise MonitorError(
                f"遥测功率超过充电桩额定功率：pile_id={item.pile_id}",
                status_code=400,
            )

    rows = [
        PileTelemetry(
            pile_id=item.pile_id,
            collected_at=_to_naive_local(item.collected_at) or item.collected_at,
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
    alarm_id: int,
    payload: RepairDispatchIn,
) -> int:
    """锁定告警后派发维修工单，避免并发重复派单或派给无效维修人员。"""
    now = _now()
    alarm = db.execute(
        select(AlarmRecord).where(AlarmRecord.alarm_id == alarm_id).with_for_update()
    ).scalar_one_or_none()
    if alarm is None:
        raise MonitorError("告警不存在", status_code=404)
    if alarm.status not in ("ACTIVE", "PROCESSING"):
        raise MonitorError("已恢复或已关闭告警不能再次派发维修工单", status_code=409)

    _ensure_active_ops_user(db, payload.maintainer_id)
    existing = db.scalar(
        select(RepairOrder.work_order_id)
        .where(
            RepairOrder.alarm_id == alarm_id,
            RepairOrder.status != "CLOSED",
        )
        .limit(1)
    )
    if existing is not None:
        raise MonitorError("同一告警已存在未闭环维修工单，禁止重复派单", status_code=409)

    dispatched_at = _checked_time(
        payload.dispatched_at,
        now=now,
        previous=alarm.occurred_at,
        label="派单时间",
    )

    order = RepairOrder(
        alarm_id=alarm_id,
        maintainer_id=payload.maintainer_id,
        status="DISPATCHED",
        dispatched_at=dispatched_at,
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
    actor_id: int,
    role: RoleEnum = RoleEnum.OPS,
) -> RepairOrder:
    """锁定并返回指定维修工单；ops 角色仅允许操作本人名下工单。"""
    order = db.execute(
        select(RepairOrder)
        .where(RepairOrder.work_order_id == work_order_id)
        .with_for_update()
    ).scalar_one_or_none()
    if order is None:
        raise MonitorError("维修工单不存在", status_code=404)
    if role == RoleEnum.OPS and order.maintainer_id != actor_id:
        raise MonitorError("无权操作他人的维修工单", status_code=403)
    return order


def _transition(
    db: Session,
    work_order_id: int,
    maintainer_id: int,
    expected_status: str,
    target_status: str,
    time_field: str | None = None,
    previous_field: str | None = None,
    time_label: str = "操作时间",
    **fields: Any,
) -> None:
    """工单状态机通用推进：校验当前状态后更新并一次提交。"""
    order = _require_owned_order(db, work_order_id, maintainer_id)
    if order.status != expected_status:
        raise MonitorError(
            f"当前工单状态为 {order.status}，不能直接变更为 {target_status}",
            status_code=409,
        )
    now = _now()
    if time_field:
        previous = getattr(order, previous_field, None) if previous_field else None
        fields[time_field] = _checked_time(
            fields.get(time_field),
            now=now,
            previous=previous,
            label=time_label,
        )
    for name, value in fields.items():
        setattr(order, name, value)
    order.status = target_status
    order.updated_at = now
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
        time_field="accepted_at",
        previous_field="dispatched_at",
        time_label="接单时间",
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
        time_field="arrived_at",
        previous_field="accepted_at",
        time_label="到场时间",
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
        time_field="repaired_at",
        previous_field="arrived_at",
        time_label="修复时间",
        replaced_parts=replaced_parts,
        result=result,
    )


def _count_open_pile_alarms(db: Session, pile_id: int) -> int:
    """统计指定充电桩上仍处于 ACTIVE/PROCESSING 状态的未解除告警数。"""
    return int(
        db.scalar(
            select(func.count())
            .select_from(AlarmRecord)
            .where(
                AlarmRecord.pile_id == pile_id,
                AlarmRecord.status.in_(("ACTIVE", "PROCESSING")),
            )
        )
        or 0
    )


def _count_open_gun_alarms(db: Session, gun_id: int) -> int:
    """统计指定充电枪上仍处于 ACTIVE/PROCESSING 状态的枪级未解除告警数。"""
    return int(
        db.scalar(
            select(func.count())
            .select_from(AlarmRecord)
            .where(
                AlarmRecord.gun_id == gun_id,
                AlarmRecord.status.in_(("ACTIVE", "PROCESSING")),
            )
        )
        or 0
    )


def _recover_single_gun(db: Session, gun_id: int) -> None:
    """设置触发器上下文变量并将单把故障充电枪恢复为空闲（逐枪执行保证每条日志来源均为 ALARM）。"""
    db.execute(_SQL_SET_ALARM_STATUS_VARS, {"remark": _GUN_RECOVERY_REMARK})
    db.execute(
        text(
            "UPDATE charging_gun "
            "SET current_status = 'IDLE', status_updated_at = NOW() "
            "WHERE gun_id = :gid"
        ),
        {"gid": gun_id},
    )


def _restore_devices_on_alarm_recovery(db: Session, alarm: AlarmRecord) -> None:
    """告警恢复后在单事务内联动恢复无剩余未决告警的故障桩与故障枪，并清零会话变量。"""
    remaining_pile_alarms = _count_open_pile_alarms(db, alarm.pile_id)
    if remaining_pile_alarms > 0 and alarm.gun_id is None:
        return

    pile_row = db.execute(
        text(
            "SELECT current_status, acceptance_status "
            "FROM charging_pile WHERE pile_id = :pid FOR UPDATE"
        ),
        {"pid": alarm.pile_id},
    ).mappings().first()
    if pile_row is None:
        return

    pile_status = str(pile_row["current_status"])
    acceptance_status = str(pile_row["acceptance_status"])

    has_error = False
    try:
        if remaining_pile_alarms == 0 and acceptance_status == "PASSED" and pile_status == "FAULT":
            db.execute(_SQL_SET_ALARM_STATUS_VARS, {"remark": _PILE_RECOVERY_REMARK})
            db.execute(
                text(
                    "UPDATE charging_pile "
                    "SET current_status = 'IDLE', status_updated_at = NOW() "
                    "WHERE pile_id = :pid"
                ),
                {"pid": alarm.pile_id},
            )
            pile_status = "IDLE"

        if acceptance_status == "PASSED" and pile_status not in ("FAULT", "OFFLINE"):
            if alarm.gun_id is not None:
                if _count_open_gun_alarms(db, alarm.gun_id) == 0:
                    gun_row = db.execute(
                        text(
                            "SELECT gun_id, current_status "
                            "FROM charging_gun WHERE gun_id = :gid FOR UPDATE"
                        ),
                        {"gid": alarm.gun_id},
                    ).mappings().first()
                    if gun_row is not None and gun_row["current_status"] == "FAULT":
                        _recover_single_gun(db, int(gun_row["gun_id"]))
            elif remaining_pile_alarms == 0:
                fault_gun_ids = db.execute(
                    text(
                        "SELECT gun_id FROM charging_gun "
                        "WHERE pile_id = :pid AND current_status = 'FAULT' "
                        "ORDER BY gun_id ASC FOR UPDATE"
                    ),
                    {"pid": alarm.pile_id},
                ).scalars().all()
                for raw_gun_id in fault_gun_ids:
                    gun_id = int(raw_gun_id)
                    if _count_open_gun_alarms(db, gun_id) == 0:
                        _recover_single_gun(db, gun_id)
    except Exception:
        has_error = True
        raise
    finally:
        try:
            db.execute(_SQL_CLEAR_STATUS_VARS)
        except Exception:
            if not has_error:
                raise


def close_repair_order(
    db: Session,
    work_order_id: int,
    actor_id: int,
    role: RoleEnum,
    verified_at: datetime | None,
) -> str:
    """复核关闭工单；仅在修复后连续两条遥测均正常时解除告警并联动恢复设备状态。"""
    now = _now()
    order = _require_owned_order(db, work_order_id, actor_id, role)
    if order.status not in ("REPAIRED", "VERIFIED"):
        raise MonitorError(
            f"当前工单状态为 {order.status}，必须先完成维修或复核后才能关闭",
            status_code=409,
        )

    verification_time = _checked_time(
        verified_at or order.verified_at,
        now=now,
        previous=order.repaired_at,
        label="复核时间",
    )

    try:
        order.status = "CLOSED"
        order.verified_at = verification_time
        order.closed_at = max(now, verification_time)
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

        message = "维修工单已关闭"
        if open_count == 0:
            alarm = db.execute(
                select(AlarmRecord)
                .where(AlarmRecord.alarm_id == order.alarm_id)
                .with_for_update()
            ).scalar_one_or_none()
            if alarm is None:
                raise MonitorError("工单关联告警不存在", status_code=404)

            telemetry_rows = db.execute(
                text(
                    "SELECT fault_code, offline_duration_sec "
                    "FROM pile_telemetry "
                    "WHERE pile_id = :pile_id "
                    "  AND collected_at >= :repaired_at "
                    "ORDER BY collected_at DESC "
                    "LIMIT 2"
                ),
                {"pile_id": alarm.pile_id, "repaired_at": order.repaired_at},
            ).mappings().all()

            healthy = (
                len(telemetry_rows) >= 2
                and all(
                    row["fault_code"] is None
                    and int(row["offline_duration_sec"] or 0) == 0
                    for row in telemetry_rows
                )
            )
            if healthy:
                if alarm.status in ("ACTIVE", "PROCESSING"):
                    alarm.status = "RECOVERED"
                    alarm.recovered_at = now
                    alarm.updated_at = now
                    db.flush()
                    _restore_devices_on_alarm_recovery(db, alarm)
                message = "维修工单已关闭，修复后连续遥测正常，告警已解除"
            elif not telemetry_rows:
                message = "维修工单已关闭，但修复后没有遥测数据，告警保持处理中"
            elif len(telemetry_rows) < 2:
                message = "维修工单已关闭，但修复后连续正常遥测不足 2 条，告警保持处理中"
            else:
                message = "维修工单已关闭，但修复后遥测仍有故障或离线，告警保持处理中"

        db.commit()
        return message
    except Exception:
        db.rollback()
        raise


def verify_repair_order(
    db: Session,
    work_order_id: int,
    actor_id: int,
    role: RoleEnum,
    verified_at: datetime | None,
) -> None:
    """将已修复工单推进到 VERIFIED，允许 ops 本人或 admin 复核。"""
    now = _now()
    order = _require_owned_order(db, work_order_id, actor_id, role)
    if order.status == "VERIFIED":
        return
    if order.status != "REPAIRED":
        raise MonitorError(
            f"当前工单状态为 {order.status}，不能直接复核",
            status_code=409,
        )
    order.verified_at = _checked_time(
        verified_at,
        now=now,
        previous=order.repaired_at,
        label="复核时间",
    )
    order.status = "VERIFIED"
    order.updated_at = now
    _commit_or_rollback(db)


def escalate_overdue_alarms(db: Session, check_time: datetime | None = None) -> int:
    """调用存储过程升级超时未响应告警，返回本次升级条数。"""
    actual_check_time = _to_naive_local(check_time) or _now()
    try:
        db.execute(
            text("CALL proc_escalate_overdue_alarms(:p_check_time, @p_escalated_count)"),
            {"p_check_time": actual_check_time},
        )
        # 必须在同一连接、同一事务内读取 OUT 变量，commit 后再读可能换连接而丢失会话变量
        count = int(db.execute(text("SELECT @p_escalated_count")).scalar() or 0)
        db.commit()
        return count
    except Exception:
        db.rollback()
        raise


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
    "verify_repair_order",
]
