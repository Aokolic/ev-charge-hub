"""业务线3：设备监控与工单（成员3/代润森） —— FastAPI 路由层。

只负责：参数接收（Schema）→ RBAC 权限守卫 → 注入 get_db → 调用 DAO → 返回脱敏
响应模型；禁止在本层直接写 SQL，也不吞掉数据库约束异常（由 main.py 全局翻译）。
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, RoleEnum, require_roles
from app.dao import monitor as monitor_dao
from app.dao.monitor import MonitorError
from app.db import get_db
from app.schemas.common import ActionResult, PageParams, PageResult
from app.schemas.monitor import (
    AlarmOut,
    EscalateIn,
    RealtimeTelemetryOut,
    RepairAcceptIn,
    RepairArriveIn,
    RepairCloseIn,
    RepairCompleteIn,
    RepairDispatchIn,
    RepairOrderOut,
    TelemetryIn,
)

router = APIRouter(prefix="/api/monitor", tags=["业务线3-设备监控与工单"])


def _monitor_error_to_http(exc: MonitorError) -> HTTPException:
    """将 DAO 层业务异常转换为标准 HTTPException，保持中文提示透传。"""
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post(
    "/telemetry/batch",
    response_model=ActionResult,
    status_code=status.HTTP_201_CREATED,
    summary="批量上报分钟级遥测",
)
def upload_telemetry_batch(
    items: list[TelemetryIn] = Body(..., min_length=1, max_length=1000),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN)),
) -> ActionResult:
    try:
        count = monitor_dao.create_telemetry_batch(db, items)
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return ActionResult(message=f"已写入 {count} 条遥测数据")


@router.get(
    "/telemetry/latest/{pile_id}",
    response_model=RealtimeTelemetryOut,
    summary="查询充电桩最新遥测",
)
def get_latest_telemetry(
    pile_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> RealtimeTelemetryOut:
    try:
        return RealtimeTelemetryOut.model_validate(
            monitor_dao.get_latest_telemetry(db, pile_id)
        )
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc


@router.get("/alarms", response_model=PageResult[AlarmOut], summary="分页查询告警看板")
def list_alarms(
    page: PageParams = Depends(),
    overdue_only: bool = Query(default=False, description="仅查询已超时未响应告警"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> PageResult[AlarmOut]:
    try:
        data = monitor_dao.list_alarms(db, page, user.role, user.user_id, overdue_only)
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return PageResult[AlarmOut](
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
        items=[AlarmOut.model_validate(row) for row in data["items"]],
    )


@router.post("/alarms/escalate", response_model=ActionResult, summary="执行告警 SLA 超时升级")
def escalate_overdue_alarms(
    payload: EscalateIn = Body(default=EscalateIn()),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN)),
) -> ActionResult:
    try:
        count = monitor_dao.escalate_overdue_alarms(db, payload.check_time)
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return ActionResult(message=f"本次共升级 {count} 条超时未响应告警")


@router.post(
    "/alarms/{alarm_id}/repair-orders",
    response_model=ActionResult,
    status_code=status.HTTP_201_CREATED,
    summary="为告警派发维修工单",
)
def dispatch_repair_order(
    payload: RepairDispatchIn,
    alarm_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN)),
) -> ActionResult:
    try:
        work_order_id = monitor_dao.dispatch_repair_order(db, alarm_id, payload)
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return ActionResult(message="维修工单已派发", id=work_order_id)


@router.get(
    "/repair-orders",
    response_model=PageResult[RepairOrderOut],
    summary="分页查询维修工单",
)
def list_repair_orders(
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> PageResult[RepairOrderOut]:
    try:
        data = monitor_dao.list_workorders(db, page, user.role, user.user_id)
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return PageResult[RepairOrderOut](
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
        items=[RepairOrderOut.model_validate(row) for row in data["items"]],
    )


@router.get("/repair-orders/mine", response_model=PageResult[RepairOrderOut], summary="查询本人维修工单")
def list_my_repair_orders(
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS)),
) -> PageResult[RepairOrderOut]:
    try:
        data = monitor_dao.list_workorders(db, page, user.role, user.user_id)
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return PageResult[RepairOrderOut](
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
        items=[RepairOrderOut.model_validate(row) for row in data["items"]],
    )


@router.get("/repair-orders/{work_order_id}", response_model=RepairOrderOut, summary="查询维修工单详情")
def get_repair_order(
    work_order_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> RepairOrderOut:
    try:
        row = monitor_dao.get_workorder(db, work_order_id, user.role, user.user_id)
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return RepairOrderOut.model_validate(row)


@router.post("/repair-orders/{work_order_id}/accept", response_model=ActionResult, summary="运维接单")
def accept_repair_order(
    payload: RepairAcceptIn,
    work_order_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS)),
) -> ActionResult:
    try:
        monitor_dao.accept_repair_order(db, work_order_id, user.user_id, payload.accepted_at)
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return ActionResult(message="维修工单已接单", id=work_order_id)


@router.post("/repair-orders/{work_order_id}/arrive", response_model=ActionResult, summary="运维到场")
def arrive_repair_order(
    payload: RepairArriveIn,
    work_order_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS)),
) -> ActionResult:
    try:
        monitor_dao.arrive_repair_order(db, work_order_id, user.user_id, payload.arrived_at)
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return ActionResult(message="维修工单已记录到场", id=work_order_id)


@router.post("/repair-orders/{work_order_id}/complete", response_model=ActionResult, summary="维修完成")
def complete_repair_order(
    payload: RepairCompleteIn,
    work_order_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS)),
) -> ActionResult:
    try:
        monitor_dao.complete_repair_order(
            db,
            work_order_id,
            user.user_id,
            payload.repaired_at,
            payload.replaced_parts,
            payload.result,
        )
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return ActionResult(message="维修工单已完成维修", id=work_order_id)


@router.post(
    "/repair-orders/{work_order_id}/verify",
    response_model=ActionResult,
    summary="复核维修工单",
)
def verify_repair_order(
    work_order_id: int = Path(ge=1),
    payload: RepairCloseIn = Body(default=RepairCloseIn()),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> ActionResult:
    try:
        monitor_dao.verify_repair_order(
            db,
            work_order_id,
            user.user_id,
            user.role,
            payload.verified_at,
        )
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return ActionResult(message="维修工单已复核", id=work_order_id)


@router.post("/repair-orders/{work_order_id}/close", response_model=ActionResult, summary="关闭工单")
def close_repair_order(
    work_order_id: int = Path(ge=1),
    payload: RepairCloseIn = Body(default=RepairCloseIn()),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> ActionResult:
    try:
        message = monitor_dao.close_repair_order(
            db,
            work_order_id,
            user.user_id,
            user.role,
            payload.verified_at,
        )
    except MonitorError as exc:
        raise _monitor_error_to_http(exc) from exc
    return ActionResult(message=message, id=work_order_id)
