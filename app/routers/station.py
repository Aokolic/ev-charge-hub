"""业务线1：场站与充电设施（成员1/组长） —— FastAPI 路由层。

职责：
1. 提供 5 个基于 RBAC 隔离视图的分页查询接口（场站概览、可用枪、故障枪/离线桩、状态变更历史、实时资产看板）；
2. 提供存储过程投运/心跳离线巡检、运维待办办结与设备状态更新接口；
3. 提供运营商 -> 场站 -> 充电桩 -> 充电枪三级资产建档、更新与明细查看接口。

约束：
- 静态路径统一定义在 `/{station_id}` 通配参数路由之前，避免路径被误拦截；
- 路由层不直写 SQL、不吞底层数据库异常，统一交由 `app/main.py` 全局异常处理器转译。
"""

from typing import Annotated, TypeVar

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, RoleEnum, require_roles
from app.dao import station as station_dao
from app.db import get_db
from app.schemas.common import ActionResult, PageParams, PageResult
from app.schemas.station import (
    DeviceStatusHistoryOut,
    DeviceStatusUpdateIn,
    DriverGunAvailabilityOut,
    DriverStationOverviewOut,
    FaultGunDetailOut,
    GunCreateIn,
    GunOut,
    HeartbeatCheckIn,
    HeartbeatCheckOut,
    OperatorCreateIn,
    OperatorOut,
    OperatorUpdateIn,
    PileCommissionIn,
    PileCreateIn,
    PileOut,
    StationCreateIn,
    StationOut,
    StationRealtimeStatusOut,
    StationUpdateIn,
    TodoResolveIn,
)

router = APIRouter(prefix="/api/stations", tags=["业务线1-场站与充电设施"])

_ModelT = TypeVar("_ModelT")


def _require_found(obj: _ModelT | None, detail: str) -> _ModelT:
    """校验资源是否存在，不存在时统一抛出 404 异常。"""
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return obj


# ============================================================================
# 0. 模块连通性探活
# ============================================================================


@router.get("/ping", summary="业务线1路由连通性探活")
def ping_station() -> dict[str, str]:
    """业务线 1 路由连通性探活接口。"""
    return {"module": "station", "owner": "成员1（组长）", "status": "ready"}


# ============================================================================
# 1. RBAC 隔离视图分页查询类
# ============================================================================


@router.get(
    "/overview",
    response_model=PageResult[DriverStationOverviewOut],
    summary="车主/车队场站概览分页查询",
)
def list_driver_stations(
    keyword: Annotated[str | None, Query(description="场站名称或地址关键字")] = None,
    only_idle_fast: Annotated[bool, Query(description="是否仅看存在空闲快充枪的场站")] = False,
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(
        require_roles(RoleEnum.DRIVER, RoleEnum.FLEET, RoleEnum.OPS, RoleEnum.ADMIN)
    ),
) -> PageResult[DriverStationOverviewOut]:
    """查询营业中场站与可用枪聚合概览（基于视图 v_driver_station_overview）。"""
    result = station_dao.list_driver_stations(
        db,
        page,
        keyword=keyword,
        only_idle_fast=only_idle_fast,
    )
    return PageResult[DriverStationOverviewOut](**result)


@router.get(
    "/guns/availability",
    response_model=PageResult[DriverGunAvailabilityOut],
    summary="车主/车队可用充电枪列表",
)
def list_driver_guns(
    station_id: Annotated[int | None, Query(gt=0, description="按场站 ID 筛选")] = None,
    gun_type: Annotated[str | None, Query(description="按枪型筛选（DC_FAST / AC_SLOW）")] = None,
    gun_status: Annotated[str | None, Query(description="按枪状态筛选")] = None,
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(
        require_roles(RoleEnum.DRIVER, RoleEnum.FLEET, RoleEnum.OPS, RoleEnum.ADMIN)
    ),
) -> PageResult[DriverGunAvailabilityOut]:
    """查询营业场站内已验收桩名下充电枪实时可用状态（基于视图 v_driver_gun_availability）。"""
    result = station_dao.list_driver_guns(
        db,
        page,
        station_id=station_id,
        gun_type=gun_type,
        gun_status=gun_status,
    )
    return PageResult[DriverGunAvailabilityOut](**result)


@router.get(
    "/faults",
    response_model=PageResult[FaultGunDetailOut],
    summary="运维/管理员故障枪与离线桩清单",
)
def list_fault_guns(
    station_id: Annotated[int | None, Query(gt=0, description="按场站 ID 筛选")] = None,
    pile_status: Annotated[str | None, Query(description="按桩状态筛选（FAULT / OFFLINE 等）")] = None,
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> PageResult[FaultGunDetailOut]:
    """查询已验收但处于故障或离线状态的桩枪排障明细（基于视图 v_fault_gun_detail）。"""
    result = station_dao.list_fault_guns(
        db,
        page,
        station_id=station_id,
        pile_status=pile_status,
    )
    return PageResult[FaultGunDetailOut](**result)


@router.get(
    "/status-logs",
    response_model=PageResult[DeviceStatusHistoryOut],
    summary="运维/管理员设备状态变更历史与待办审计",
)
def list_status_history(
    station_id: Annotated[int | None, Query(gt=0, description="按场站 ID 筛选")] = None,
    device_type: Annotated[str | None, Query(description="按设备类型筛选（PILE / GUN）")] = None,
    todo_status: Annotated[str | None, Query(description="按待办状态筛选（NONE / PENDING / DONE）")] = None,
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> PageResult[DeviceStatusHistoryOut]:
    """查询桩/枪状态变更轨迹与运维待办记录（基于视图 v_device_status_history）。"""
    result = station_dao.list_status_history(
        db,
        page,
        station_id=station_id,
        device_type=device_type,
        todo_status=todo_status,
    )
    return PageResult[DeviceStatusHistoryOut](**result)


@router.get(
    "/realtime-status",
    response_model=PageResult[StationRealtimeStatusOut],
    summary="平台级场站实时资产状态看板",
)
def list_realtime_stations(
    operator_id: Annotated[int | None, Query(gt=0, description="按运营商 ID 筛选")] = None,
    station_status: Annotated[str | None, Query(description="按场站营业状态筛选")] = None,
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(
        require_roles(RoleEnum.ADMIN, RoleEnum.SETTLEMENT, RoleEnum.OPS)
    ),
) -> PageResult[StationRealtimeStatusOut]:
    """查询全量场站、桩/枪实时统计与装机总功率（基于视图 v_station_realtime_status）。"""
    result = station_dao.list_realtime_stations(
        db,
        page,
        operator_id=operator_id,
        station_status=station_status,
    )
    return PageResult[StationRealtimeStatusOut](**result)


# ============================================================================
# 2. 待办闭环与存储过程 / 设备状态维护类
# ============================================================================


@router.post(
    "/status-logs/{log_id}/resolve-todo",
    response_model=ActionResult,
    summary="办结设备状态变更运维待办",
)
def resolve_status_todo(
    log_id: int,
    payload: Annotated[TodoResolveIn | None, Body()] = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> ActionResult:
    """将指定状态变更日志的运维待办状态置为 DONE 并记录处置备注。"""
    resolved_payload = payload if payload is not None else TodoResolveIn()
    _require_found(
        station_dao.resolve_status_todo(db, log_id, resolved_payload),
        "状态变更日志不存在",
    )
    return ActionResult(success=True, message="运维待办已办结", id=log_id)


@router.post(
    "/piles/heartbeat-check",
    response_model=HeartbeatCheckOut,
    summary="批量巡检充电桩心跳超时离线",
)
def check_heartbeat_offline(
    payload: Annotated[HeartbeatCheckIn | None, Body()] = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> HeartbeatCheckOut:
    """调用存储过程识别心跳超时的充电桩，级联置为 OFFLINE 并生成 PENDING 待办。"""
    res = station_dao.check_heartbeat_offline(
        db,
        payload.check_time if payload is not None else None,
    )
    return HeartbeatCheckOut(**res, message="心跳离线巡检完成")


@router.post(
    "/piles/{pile_id}/commission",
    response_model=PileOut,
    summary="充电桩验收投运",
)
def commission_pile(
    pile_id: int,
    payload: Annotated[PileCommissionIn | None, Body()] = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> PileOut:
    """调用存储过程对充电桩执行验收投运，更新验收状态及下属枪状态。"""
    resolved_payload = payload if payload is not None else PileCommissionIn()
    pile = _require_found(
        station_dao.commission_pile(db, pile_id, resolved_payload),
        "充电桩不存在",
    )
    return PileOut.model_validate(pile)


@router.post(
    "/piles/{pile_id}/heartbeat",
    response_model=PileOut,
    summary="上报充电桩心跳",
)
def report_pile_heartbeat(
    pile_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> PileOut:
    """刷新指定充电桩的最新心跳时间戳（last_heartbeat_at = NOW()）。"""
    pile = _require_found(
        station_dao.report_pile_heartbeat(db, pile_id),
        "充电桩不存在",
    )
    return PileOut.model_validate(pile)


@router.patch(
    "/piles/{pile_id}/status",
    response_model=PileOut,
    summary="变更充电桩运行状态",
)
def update_pile_status(
    pile_id: int,
    payload: DeviceStatusUpdateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> PileOut:
    """更新充电桩当前状态并透传变更来源/待办状态到审计触发器。"""
    pile = _require_found(
        station_dao.update_pile_status(db, pile_id, payload),
        "充电桩不存在",
    )
    return PileOut.model_validate(pile)


@router.patch(
    "/guns/{gun_id}/status",
    response_model=GunOut,
    summary="变更充电枪运行状态",
)
def update_gun_status(
    gun_id: int,
    payload: DeviceStatusUpdateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> GunOut:
    """更新充电枪当前状态并透传变更来源/待办状态到审计触发器。"""
    gun = _require_found(
        station_dao.update_gun_status(db, gun_id, payload),
        "充电枪不存在",
    )
    return GunOut.model_validate(gun)


# ============================================================================
# 3. 三级资产建档、更新与明细查询类（静态前缀必须在 /{station_id} 之前）
# ============================================================================


@router.get(
    "/operators",
    response_model=list[OperatorOut],
    summary="获取运营商列表",
)
def list_operators(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN, RoleEnum.SETTLEMENT)),
) -> list[OperatorOut]:
    """查询平台全部合作运营商档案列表（按 operator_id 升序）。"""
    operators = station_dao.list_operators(db)
    return [OperatorOut.model_validate(item) for item in operators]


@router.post(
    "/operators",
    response_model=OperatorOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增运营商档案",
)
def create_operator(
    payload: OperatorCreateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN)),
) -> OperatorOut:
    """创建运营商基础档案，编码或信用代码重复由数据库唯一约束拦截转 409。"""
    operator = station_dao.create_operator(db, payload)
    return OperatorOut.model_validate(operator)


@router.patch(
    "/operators/{operator_id}",
    response_model=OperatorOut,
    summary="更新运营商档案",
)
def update_operator(
    operator_id: int,
    payload: OperatorUpdateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN)),
) -> OperatorOut:
    """按 operator_id 局部更新运营商联系人、客服电话或合作状态。"""
    operator = _require_found(
        station_dao.update_operator(db, operator_id, payload),
        "运营商不存在",
    )
    return OperatorOut.model_validate(operator)


@router.post(
    "",
    response_model=StationOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增充电场站",
)
def create_station(
    payload: StationCreateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN)),
) -> StationOut:
    """创建充电场站档案；车位合理性与编码唯一性交由数据库约束校验。"""
    station = station_dao.create_station(db, payload)
    return StationOut.model_validate(station)


@router.post(
    "/piles",
    response_model=PileOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增充电桩",
)
def create_pile(
    payload: PileCreateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN)),
) -> PileOut:
    """在指定场站下新建充电桩档案；未验收桩不可直接置为空闲/占用由 CHECK 约束保障。"""
    pile = station_dao.create_pile(db, payload)
    return PileOut.model_validate(pile)


@router.get(
    "/piles/{pile_id}/guns",
    response_model=list[GunOut],
    summary="查看指定充电桩下属充电枪列表",
)
def list_guns_by_pile(
    pile_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> list[GunOut]:
    """校验充电桩存在后，按枪序号升序返回名下所有充电枪档案。"""
    _require_found(station_dao.get_pile(db, pile_id), "充电桩不存在")
    guns = station_dao.list_guns_by_pile(db, pile_id)
    return [GunOut.model_validate(item) for item in guns]


@router.post(
    "/guns",
    response_model=GunOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增充电枪",
)
def create_gun(
    payload: GunCreateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN)),
) -> GunOut:
    """为充电桩配置充电枪；枪数量上限与单枪功率上限由数据库触发器实时校验。"""
    gun = station_dao.create_gun(db, payload)
    return GunOut.model_validate(gun)


# ============================================================================
# 4. 带 /{station_id} 通配参数路由（必须放在所有静态前缀路由之后）
# ============================================================================


@router.patch(
    "/{station_id}",
    response_model=StationOut,
    summary="更新充电场站信息",
)
def update_station(
    station_id: int,
    payload: StationUpdateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.ADMIN)),
) -> StationOut:
    """局部更新场站名称、车位数、营业时间、停车费率或营业状态。"""
    station = _require_found(
        station_dao.update_station(db, station_id, payload),
        "充电场站不存在",
    )
    return StationOut.model_validate(station)


@router.get(
    "/{station_id}/piles",
    response_model=list[PileOut],
    summary="查看指定场站下属充电桩列表",
)
def list_piles_by_station(
    station_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> list[PileOut]:
    """校验场站存在后，按 pile_id 升序返回名下全部充电桩档案。"""
    _require_found(station_dao.get_station(db, station_id), "充电场站不存在")
    piles = station_dao.list_piles_by_station(db, station_id)
    return [PileOut.model_validate(item) for item in piles]
