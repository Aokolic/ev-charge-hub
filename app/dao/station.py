"""业务线1：场站与充电设施（成员1/组长） —— 数据访问层（常规 CRUD 用 ORM，视图/存储过程用 text() 参数化）。

约定：
1. 列表与看板查询一律走 5 个 RBAC 隔离视图（调用 app.dao.common.fetch_view_page），禁止将原始表直出前端；
2. 所有 SQL 与排序子句均采用受控白名单字符串拼接与参数绑定，严禁 f-string 拼接用户输入；
3. 存储过程与状态更新在同事务内完成，任何异常均先 db.rollback() 再原样向上抛出，交由 app/main.py 统一翻译；
4. 心跳离线巡检的 check_time 默认值为 None，严禁用 Python datetime.now() 替代数据库 UTC NOW()。
"""

from datetime import datetime, timezone
from typing import Any, TypeVar

from sqlalchemy import TextClause, select, text
from sqlalchemy.orm import Session

from app.dao.common import fetch_view_page
from app.models.station import (
    ChargingGun,
    ChargingPile,
    DeviceStatusLog,
    Operator,
    Station,
)
from app.schemas.common import PageParams
from app.schemas.station import (
    DeviceStatusUpdateIn,
    GunCreateIn,
    OperatorCreateIn,
    OperatorUpdateIn,
    PileCommissionIn,
    PileCreateIn,
    StationCreateIn,
    StationUpdateIn,
    TodoResolveIn,
)

ModelT = TypeVar("ModelT")

# ----------------------------------------------------------------------------
# 受控视图 SQL 基础常量与状态变量操作语句（严禁动态拼接用户输入）
# ----------------------------------------------------------------------------
_SQL_DRIVER_STATIONS = "SELECT * FROM v_driver_station_overview WHERE 1 = 1"
_SQL_DRIVER_GUNS = "SELECT * FROM v_driver_gun_availability WHERE 1 = 1"
_SQL_FAULT_GUNS = "SELECT * FROM v_fault_gun_detail WHERE 1 = 1"
_SQL_STATUS_HISTORY = "SELECT * FROM v_device_status_history WHERE 1 = 1"
_SQL_REALTIME_STATIONS = "SELECT * FROM v_station_realtime_status WHERE 1 = 1"

_ORDER_DRIVER_STATIONS = "idle_fast_gun_count DESC, station_id ASC"
_ORDER_DRIVER_GUNS = "station_id ASC, pile_id ASC, gun_seq ASC"
_ORDER_FAULT_GUNS = "pile_status_updated_at DESC, pile_id ASC, gun_seq ASC"
_ORDER_STATUS_HISTORY = "changed_at DESC, log_id DESC"
_ORDER_REALTIME_STATIONS = "station_id ASC"

_SQL_SET_STATUS_VARS = text(
    "SET @status_change_source = :src, "
    "@status_todo_status = :todo, "
    "@status_change_remark = :rmk"
)
_SQL_CLEAR_STATUS_VARS = text(
    "SET @status_change_source = NULL, "
    "@status_todo_status = NULL, "
    "@status_change_remark = NULL"
)
_SQL_UPDATE_PILE_STATUS = text(
    "UPDATE charging_pile "
    "SET current_status = :st, status_updated_at = NOW() "
    "WHERE pile_id = :target_id"
)
_SQL_UPDATE_GUN_STATUS = text(
    "UPDATE charging_gun "
    "SET current_status = :st, status_updated_at = NOW() "
    "WHERE gun_id = :target_id"
)


def _commit_and_refresh(db: Session, instance: ModelT) -> ModelT:
    """统一提交 ORM 写操作并刷新实例；发生底层约束或触发器异常时先回滚再原样上抛。"""
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(instance)
    return instance


def _execute_device_status_update(
    db: Session,
    update_stmt: TextClause,
    target_id: int,
    payload: DeviceStatusUpdateIn,
) -> None:
    """在单事务内通过 MySQL 会话变量向触发器透传上下文并更新设备状态。

    注意：触发器仅在状态实际发生变化时清空会话变量；若新旧状态相同或中途抛出异常，
    必须通过 try/finally 在当前连接释放前手动将 3 个会话变量重置为 NULL。
    """
    remark = (
        payload.remark.strip()
        if payload.remark is not None and payload.remark.strip()
        else None
    )
    has_error = False
    try:
        try:
            db.execute(
                _SQL_SET_STATUS_VARS,
                {
                    "src": payload.change_source,
                    "todo": payload.todo_status,
                    "rmk": remark,
                },
            )
            db.execute(
                update_stmt,
                {"st": payload.new_status, "target_id": target_id},
            )
        except Exception:
            has_error = True
            raise
        finally:
            try:
                db.execute(_SQL_CLEAR_STATUS_VARS)
            except Exception:
                # 仅在主流程未出错时抛出清理异常，避免覆盖真实的 1644/3819 业务异常
                if not has_error:
                    raise
        db.commit()
    except Exception:
        db.rollback()
        raise


# ============================================================================
# 1. 5 个 RBAC 隔离视图分页查询函数
# ============================================================================


def list_driver_stations(
    db: Session,
    page: PageParams,
    keyword: str | None = None,
    only_idle_fast: bool = False,
) -> dict[str, Any]:
    """分页查询面向车主/车队的营业场站总览视图（v_driver_station_overview）。"""
    sql_parts = [_SQL_DRIVER_STATIONS]
    params: dict[str, Any] = {}

    if keyword and keyword.strip():
        sql_parts.append("AND (station_name LIKE :kw OR address LIKE :kw)")
        params["kw"] = f"%{keyword.strip()}%"
    if only_idle_fast:
        sql_parts.append("AND idle_fast_gun_count > 0")

    return fetch_view_page(
        db,
        " ".join(sql_parts),
        params,
        page,
        order_by=_ORDER_DRIVER_STATIONS,
    )


def list_driver_guns(
    db: Session,
    page: PageParams,
    station_id: int | None = None,
    gun_type: str | None = None,
    gun_status: str | None = None,
) -> dict[str, Any]:
    """分页查询面向车主/车队的充电枪可用性视图（v_driver_gun_availability）。"""
    sql_parts = [_SQL_DRIVER_GUNS]
    params: dict[str, Any] = {}

    if station_id is not None:
        sql_parts.append("AND station_id = :station_id")
        params["station_id"] = station_id
    if gun_type and gun_type.strip():
        sql_parts.append("AND gun_type = :gun_type")
        params["gun_type"] = gun_type.strip()
    if gun_status and gun_status.strip():
        sql_parts.append("AND gun_status = :gun_status")
        params["gun_status"] = gun_status.strip()

    return fetch_view_page(
        db,
        " ".join(sql_parts),
        params,
        page,
        order_by=_ORDER_DRIVER_GUNS,
    )


def list_fault_guns(
    db: Session,
    page: PageParams,
    station_id: int | None = None,
    pile_status: str | None = None,
) -> dict[str, Any]:
    """分页查询面向运维/管理员的故障与离线设备明细视图（v_fault_gun_detail）。"""
    sql_parts = [_SQL_FAULT_GUNS]
    params: dict[str, Any] = {}

    if station_id is not None:
        sql_parts.append("AND station_id = :station_id")
        params["station_id"] = station_id
    if pile_status and pile_status.strip():
        sql_parts.append("AND pile_status = :pile_status")
        params["pile_status"] = pile_status.strip()

    return fetch_view_page(
        db,
        " ".join(sql_parts),
        params,
        page,
        order_by=_ORDER_FAULT_GUNS,
    )


def list_status_history(
    db: Session,
    page: PageParams,
    station_id: int | None = None,
    device_type: str | None = None,
    todo_status: str | None = None,
) -> dict[str, Any]:
    """分页查询设备状态变更审计与运维待办视图（v_device_status_history）。"""
    sql_parts = [_SQL_STATUS_HISTORY]
    params: dict[str, Any] = {}

    if station_id is not None:
        sql_parts.append("AND station_id = :station_id")
        params["station_id"] = station_id
    if device_type and device_type.strip():
        sql_parts.append("AND device_type = :device_type")
        params["device_type"] = device_type.strip()
    if todo_status and todo_status.strip():
        sql_parts.append("AND todo_status = :todo_status")
        params["todo_status"] = todo_status.strip()

    return fetch_view_page(
        db,
        " ".join(sql_parts),
        params,
        page,
        order_by=_ORDER_STATUS_HISTORY,
    )


def list_realtime_stations(
    db: Session,
    page: PageParams,
    operator_id: int | None = None,
    station_status: str | None = None,
) -> dict[str, Any]:
    """分页查询面向管理员/结算专员的全量场站实时状态看板视图（v_station_realtime_status）。"""
    sql_parts = [_SQL_REALTIME_STATIONS]
    params: dict[str, Any] = {}

    if operator_id is not None:
        sql_parts.append("AND operator_id = :operator_id")
        params["operator_id"] = operator_id
    if station_status and station_status.strip():
        sql_parts.append("AND station_status = :station_status")
        params["station_status"] = station_status.strip()

    return fetch_view_page(
        db,
        " ".join(sql_parts),
        params,
        page,
        order_by=_ORDER_REALTIME_STATIONS,
    )


# ============================================================================
# 2. 2 个存储过程调用函数
# ============================================================================


def commission_pile(
    db: Session,
    pile_id: int,
    payload: PileCommissionIn,
) -> ChargingPile | None:
    """调用存储过程 proc_commission_charging_pile 原子完成充电桩验收投运。

    校验失败（如未配枪、已投运、质保过期）由过程 SIGNAL 1644 抛出，回滚后原样上抛。
    """
    try:
        db.execute(
            text("CALL proc_commission_charging_pile(:pile_id, :commissioned_date, :remark)"),
            {
                "pile_id": pile_id,
                "commissioned_date": payload.commissioned_date,
                "remark": payload.remark,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return db.get(ChargingPile, pile_id)


def check_heartbeat_offline(
    db: Session,
    check_time: datetime | None = None,
) -> dict[str, int]:
    """调用存储过程 proc_check_pile_heartbeat_offline 巡检心跳超时桩并联动置为离线。

    时区规范：check_time 默认 None，交由数据库内部 COALESCE(p_check_time, NOW()) 取 UTC 时间；
    若显式传入带时区的 datetime，则先换算为 UTC 无时区 datetime 再入参。
    """
    normalized_time = (
        check_time.astimezone(timezone.utc).replace(tzinfo=None)
        if check_time is not None and check_time.tzinfo is not None
        else check_time
    )
    try:
        db.execute(
            text(
                "CALL proc_check_pile_heartbeat_offline("
                ":check_time, @p_affected_piles, @p_affected_guns)"
            ),
            {"check_time": normalized_time},
        )
        row = (
            db.execute(
                text(
                    "SELECT COALESCE(@p_affected_piles, 0) AS affected_piles, "
                    "COALESCE(@p_affected_guns, 0) AS affected_guns"
                )
            )
            .mappings()
            .one()
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "affected_piles": int(row["affected_piles"]),
        "affected_guns": int(row["affected_guns"]),
    }


# ============================================================================
# 3. ORM CRUD 与状态更新函数
# ============================================================================


def get_operator(db: Session, operator_id: int) -> Operator | None:
    """按主键查询运营商档案，不存在返回 None。"""
    return db.get(Operator, operator_id)


def list_operators(db: Session) -> list[Operator]:
    """按 operator_id 升序返回全部运营商档案列表。"""
    stmt = select(Operator).order_by(Operator.operator_id.asc())
    return list(db.execute(stmt).scalars().all())


def create_operator(db: Session, payload: OperatorCreateIn) -> Operator:
    """新增运营商档案，编码或统一社会信用代码冲突时回滚并抛出 IntegrityError(1062)。"""
    operator = Operator(
        operator_code=payload.operator_code,
        operator_name=payload.operator_name,
        credit_code=payload.credit_code,
        contact_name=payload.contact_name,
        service_phone=payload.service_phone,
        status=payload.status,
    )
    db.add(operator)
    return _commit_and_refresh(db, operator)


def update_operator(
    db: Session,
    operator_id: int,
    payload: OperatorUpdateIn,
) -> Operator | None:
    """更新运营商可变更字段，不存在返回 None。"""
    operator = db.get(Operator, operator_id)
    if operator is None:
        return None
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(operator, field, value)
    return _commit_and_refresh(db, operator)


def get_station(db: Session, station_id: int) -> Station | None:
    """按主键查询充电场站，不存在返回 None。"""
    return db.get(Station, station_id)


def create_station(db: Session, payload: StationCreateIn) -> Station:
    """新增充电场站（可用车位 <= 总车位等约束交由数据库 CHECK 3819 与外键 1452 兜底）。"""
    station = Station(
        operator_id=payload.operator_id,
        station_code=payload.station_code,
        station_name=payload.station_name,
        address=payload.address,
        longitude=payload.longitude,
        latitude=payload.latitude,
        total_spaces=payload.total_spaces,
        available_spaces=payload.available_spaces,
        business_hours=payload.business_hours,
        parking_fee_rate=payload.parking_fee_rate,
        station_status=payload.station_status,
    )
    db.add(station)
    return _commit_and_refresh(db, station)


def update_station(
    db: Session,
    station_id: int,
    payload: StationUpdateIn,
) -> Station | None:
    """更新充电场站信息，不存在返回 None。"""
    station = db.get(Station, station_id)
    if station is None:
        return None
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(station, field, value)
    return _commit_and_refresh(db, station)


def get_pile(db: Session, pile_id: int) -> ChargingPile | None:
    """按主键查询充电桩，不存在返回 None。"""
    return db.get(ChargingPile, pile_id)


def list_piles_by_station(db: Session, station_id: int) -> list[ChargingPile]:
    """按 pile_id 升序查询指定场站下的全部充电桩。"""
    stmt = (
        select(ChargingPile)
        .where(ChargingPile.station_id == station_id)
        .order_by(ChargingPile.pile_id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def create_pile(db: Session, payload: PileCreateIn) -> ChargingPile:
    """新增充电桩（建档初始化状态由数据库触发器 trg_pile_after_insert_status_log 自动留痕）。"""
    pile = ChargingPile(
        station_id=payload.station_id,
        pile_code=payload.pile_code,
        device_model=payload.device_model,
        rated_power=payload.rated_power,
        gun_count=payload.gun_count,
        protocol_type=payload.protocol_type,
        commissioned_date=payload.commissioned_date,
        warranty_end_date=payload.warranty_end_date,
        acceptance_status=payload.acceptance_status,
        current_status=payload.current_status,
        offline_threshold_sec=payload.offline_threshold_sec,
    )
    db.add(pile)
    return _commit_and_refresh(db, pile)


def update_pile_status(
    db: Session,
    pile_id: int,
    payload: DeviceStatusUpdateIn,
) -> ChargingPile | None:
    """更新充电桩当前状态并记录变更来源/待办/备注，不存在返回 None。"""
    if db.get(ChargingPile, pile_id) is None:
        return None
    _execute_device_status_update(db, _SQL_UPDATE_PILE_STATUS, pile_id, payload)
    db.expire_all()
    return db.get(ChargingPile, pile_id)


def report_pile_heartbeat(db: Session, pile_id: int) -> ChargingPile | None:
    """记录充电桩心跳上报（使用数据库 NOW() 刷新 last_heartbeat_at），不存在返回 None。"""
    if db.get(ChargingPile, pile_id) is None:
        return None
    try:
        db.execute(
            text(
                "UPDATE charging_pile "
                "SET last_heartbeat_at = NOW() "
                "WHERE pile_id = :pid"
            ),
            {"pid": pile_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return db.get(ChargingPile, pile_id)


def get_gun(db: Session, gun_id: int) -> ChargingGun | None:
    """按主键查询充电枪，不存在返回 None。"""
    return db.get(ChargingGun, gun_id)


def list_guns_by_pile(db: Session, pile_id: int) -> list[ChargingGun]:
    """按枪序号 gun_seq 升序查询指定充电桩下的全部充电枪。"""
    stmt = (
        select(ChargingGun)
        .where(ChargingGun.pile_id == pile_id)
        .order_by(ChargingGun.gun_seq.asc())
    )
    return list(db.execute(stmt).scalars().all())


def create_gun(db: Session, payload: GunCreateIn) -> ChargingGun:
    """新增充电枪（单枪功率 <= 桩额定功率与枪序号校验由 BEFORE INSERT 触发器拦截）。"""
    gun = ChargingGun(
        pile_id=payload.pile_id,
        gun_code=payload.gun_code,
        gun_seq=payload.gun_seq,
        gun_type=payload.gun_type,
        connector_standard=payload.connector_standard,
        max_power=payload.max_power,
        current_status=payload.current_status,
    )
    db.add(gun)
    return _commit_and_refresh(db, gun)


def update_gun_status(
    db: Session,
    gun_id: int,
    payload: DeviceStatusUpdateIn,
) -> ChargingGun | None:
    """更新充电枪当前状态并记录变更来源/待办/备注，不存在返回 None。"""
    if db.get(ChargingGun, gun_id) is None:
        return None
    _execute_device_status_update(db, _SQL_UPDATE_GUN_STATUS, gun_id, payload)
    db.expire_all()
    return db.get(ChargingGun, gun_id)


def resolve_status_todo(
    db: Session,
    log_id: int,
    payload: TodoResolveIn,
) -> DeviceStatusLog | None:
    """办结设备状态日志中的运维待办（置 todo_status='DONE' 并可选更新处理备注）。"""
    log = db.get(DeviceStatusLog, log_id)
    if log is None:
        return None
    log.todo_status = "DONE"
    if payload.remark is not None and payload.remark.strip():
        log.remark = payload.remark.strip()
    return _commit_and_refresh(db, log)


__all__ = [
    "check_heartbeat_offline",
    "commission_pile",
    "create_gun",
    "create_operator",
    "create_pile",
    "create_station",
    "get_gun",
    "get_operator",
    "get_pile",
    "get_station",
    "list_driver_guns",
    "list_driver_stations",
    "list_fault_guns",
    "list_guns_by_pile",
    "list_operators",
    "list_piles_by_station",
    "list_realtime_stations",
    "list_status_history",
    "report_pile_heartbeat",
    "resolve_status_todo",
    "update_gun_status",
    "update_operator",
    "update_pile_status",
    "update_station",
]
