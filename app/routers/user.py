"""业务线4：用户与会员运营路由（负责人：成员 4）。

前缀 /api/users。RBAC 隔离口径（对应任务书 2.2 节与 routers/README 权限矩阵）：
1. 平台运营管理员 admin：用户档案全量、代开账号、冻结/禁用、创建活动与定向发券、车队开卡；
2. 客服与结算专员 settlement：用户档案与车队授信只读、核销台账对账，不可发券/开卡；
3. 普通车主 driver：仅本人档案/券包/车辆，扫码充电后核销本人券；
4. 企业客户 fleet：本企业车队授信与车辆（严格按 JWT 内的 fleet_id 行级隔离）；
5. 场站运维 ops：不开放本业务线接口（工单相关在 /api/monitor）。
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, RoleEnum, get_current_user, hash_password, require_roles
from app.dao import user as user_dao
from app.db import get_db
from app.schemas.common import ActionResult, PageParams, PageResult
from app.schemas.user import (
    CampaignIn,
    CampaignOut,
    CampaignPublicOut,
    CampaignStatusIn,
    CouponIssueIn,
    CouponIssueOut,
    CouponOut,
    CouponRedeemIn,
    CouponRedeemOut,
    CouponRedeemRecordOut,
    FleetCardIn,
    FleetCreditOut,
    UserMaskedOut,
    UserRegisterIn,
    UserStatusIn,
    VehicleBindIn,
    VehicleOut,
)

router = APIRouter(prefix="/api/users", tags=["业务线4-用户与会员"])

# 角色守卫：写操作按矩阵收敛到最窄角色集合
_ONLY_ADMIN = require_roles(RoleEnum.ADMIN)
_ADMIN_OR_SETTLE = require_roles(RoleEnum.ADMIN, RoleEnum.SETTLEMENT)
_DRIVER_OR_FLEET = require_roles(RoleEnum.DRIVER, RoleEnum.FLEET)

# 查询参数用 Literal 收敛取值范围（非法值直接 422，而不是悄悄查不到数据）
BIND_STATUS_LITERAL = Literal["BOUND", "UNBOUND", "DISABLED"]
CAMPAIGN_STATUS_LITERAL = Literal["DRAFT", "ACTIVE", "PAUSED", "ENDED"]
COUPON_STATUS_LITERAL = Literal["ISSUED", "REDEEMED", "EXPIRED", "VOID"]
USER_ROLE_LITERAL = Literal["driver", "ops", "settlement", "fleet", "admin"]
USER_STATUS_LITERAL = Literal["ACTIVE", "FROZEN", "DISABLED"]
FLEET_STATUS_LITERAL = Literal["ACTIVE", "SUSPENDED", "CLOSED"]


# ---------------------------------------------------------------------------
# 一、用户档案与账号
# ---------------------------------------------------------------------------
@router.get("", response_model=PageResult[UserMaskedOut], summary="分页查询脱敏用户档案")
def list_users(
    page: PageParams = Depends(),
    role: USER_ROLE_LITERAL | None = Query(default=None, description="按角色过滤：driver/ops/settlement/fleet/admin"),
    user_status: USER_STATUS_LITERAL | None = Query(
        default=None, alias="status", description="按账号状态过滤：ACTIVE/FROZEN/DISABLED"
    ),
    keyword: str | None = Query(default=None, max_length=50, description="用户名或脱敏手机号模糊匹配"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ADMIN_OR_SETTLE),
) -> PageResult[UserMaskedOut]:
    """管理员与结算专员可查全量脱敏档案；列表中不含手机号明文与口令哈希。"""
    result = user_dao.list_users(db, page, role=role, status=user_status, keyword=keyword)
    return PageResult[UserMaskedOut](**result)


@router.get("/me/profile", response_model=UserMaskedOut, summary="查看本人脱敏档案")
def my_profile(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> UserMaskedOut:
    """任何已登录角色读取本人档案（仅脱敏手机号，含绑车数与可用券数）。"""
    row = user_dao.get_user_masked(db, user.user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账号档案不存在")
    return UserMaskedOut.model_validate(row)


@router.post(
    "/accounts",
    response_model=UserMaskedOut,
    status_code=status.HTTP_201_CREATED,
    summary="管理员代开运维/结算/车队账号",
)
def create_account(
    payload: UserRegisterIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_ADMIN),
) -> UserMaskedOut:
    """车主与车队走 /api/auth/register 自助注册；此处仅供管理员代开内部角色账号。"""
    account = user_dao.create_staff_account(db, payload, hash_password(payload.password))
    row = user_dao.get_user_masked(db, account.user_id)
    if row is None:  # pragma: no cover - 刚写入必然可查
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "账号已创建但档案读取失败")
    return UserMaskedOut.model_validate(row)


# ---------------------------------------------------------------------------
# 二、车辆绑定
# ---------------------------------------------------------------------------
@router.get("/vehicles", response_model=PageResult[VehicleOut], summary="分页查询车辆绑定")
def list_vehicles(
    page: PageParams = Depends(),
    user_id: int | None = Query(default=None, gt=0, description="仅管理员可指定他人ID"),
    bind_status: BIND_STATUS_LITERAL | None = Query(default=None, description="BOUND/UNBOUND/DISABLED"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PageResult[VehicleOut]:
    """driver 只看本人车；fleet 看**本企业**全部车辆（同车队多账号可协同管理）；admin 可查全量。"""
    if user.role == RoleEnum.DRIVER:
        result = user_dao.list_vehicles(db, page, user_id=user.user_id, bind_status=bind_status)
        return PageResult[VehicleOut](**result)

    if user.role == RoleEnum.FLEET:
        if user.fleet_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "当前车队账号未绑定企业档案")
        result = user_dao.list_vehicles(db, page, fleet_id=user.fleet_id, bind_status=bind_status)
        return PageResult[VehicleOut](**result)

    if user.role != RoleEnum.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前角色无权查看车辆档案")

    result = user_dao.list_vehicles(db, page, user_id=user_id, bind_status=bind_status)
    return PageResult[VehicleOut](**result)


@router.post(
    "/vehicles",
    response_model=VehicleOut,
    status_code=status.HTTP_201_CREATED,
    summary="绑定车辆（车牌加密落库，一车一主）",
)
def bind_vehicle(
    payload: VehicleBindIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_DRIVER_OR_FLEET),
) -> VehicleOut:
    """车牌明文只在此处出现；该车牌正被他人绑定时由唯一索引拦截并返回 409。

    解绑后的车牌可被重新绑定（一车一主只约束在绑状态，不再永久锁死车牌）。
    """
    fleet_id: int | None = None
    if user.role == RoleEnum.FLEET:
        if user.fleet_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "当前车队账号未绑定企业档案，无法绑车")
        fleet_id = user.fleet_id

    vehicle = user_dao.bind_vehicle(db, user.user_id, payload, fleet_id=fleet_id)
    row = user_dao.get_vehicle_masked(db, vehicle.vehicle_id)
    if row is None:  # pragma: no cover - 刚写入必然可查
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "车辆已绑定但档案读取失败")
    return VehicleOut.model_validate(row)


@router.delete("/vehicles/{vehicle_id}", response_model=ActionResult, summary="解绑本人/本企业车辆")
def unbind_vehicle(
    vehicle_id: int = Path(ge=1, description="车辆ID"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_DRIVER_OR_FLEET),
) -> ActionResult:
    """driver 只能解绑本人车，fleet 只能解绑本企业车；不匹配按不存在处理，避免探测他人车辆ID。"""
    if user.role == RoleEnum.FLEET:
        if user.fleet_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "当前车队账号未绑定企业档案")
        vehicle = user_dao.unbind_vehicle(db, vehicle_id, fleet_id=user.fleet_id)
    else:
        vehicle = user_dao.unbind_vehicle(db, vehicle_id, user_id=user.user_id)

    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "车辆不存在或不属于当前用户")
    return ActionResult(message=f"车辆 {vehicle_id} 已解绑，车牌可重新绑定", id=vehicle_id)


# ---------------------------------------------------------------------------
# 三、企业车队与授信额度
# ---------------------------------------------------------------------------
@router.post(
    "/fleets",
    response_model=ActionResult,
    status_code=status.HTTP_201_CREATED,
    summary="企业车队开卡（建档 + 授信额度）",
)
def create_fleet(
    payload: FleetCardIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_ADMIN),
) -> ActionResult:
    """开卡后即可为该车队代开 fleet 角色账号并批量绑车。"""
    fleet = user_dao.create_fleet(db, payload)
    return ActionResult(
        message=f"车队 {fleet.fleet_code} 开卡成功，授信额度 {fleet.credit_limit} 元",
        id=fleet.fleet_id,
    )


@router.get("/fleets/credit", response_model=PageResult[FleetCreditOut], summary="车队授信与月度消费")
def list_fleet_credit(
    page: PageParams = Depends(),
    fleet_id: int | None = Query(default=None, gt=0, description="仅管理员/结算可指定"),
    fleet_status: FLEET_STATUS_LITERAL | None = Query(
        default=None, alias="status", description="ACTIVE/SUSPENDED/CLOSED"
    ),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PageResult[FleetCreditOut]:
    """fleet 角色强制按 JWT 中的 fleet_id 隔离，查不到别家车队。"""
    if user.role == RoleEnum.FLEET:
        if user.fleet_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "当前车队账号未绑定企业档案")
        result = user_dao.list_fleet_credit(db, page, fleet_id=user.fleet_id, status=fleet_status)
        return PageResult[FleetCreditOut](**result)

    if user.role not in (RoleEnum.ADMIN, RoleEnum.SETTLEMENT):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前角色无权查看车队授信")

    result = user_dao.list_fleet_credit(db, page, fleet_id=fleet_id, status=fleet_status)
    return PageResult[FleetCreditOut](**result)


# ---------------------------------------------------------------------------
# 四、优惠活动与发券
# ---------------------------------------------------------------------------
@router.get(
    "/campaigns",
    response_model=PageResult[CampaignOut],
    summary="活动投放与核销看板（仅管理员/结算专员）",
)
def list_campaigns(
    page: PageParams = Depends(),
    campaign_status: CAMPAIGN_STATUS_LITERAL | None = Query(
        default=None, alias="status", description="DRAFT/ACTIVE/PAUSED/ENDED"
    ),
    applicable_station_id: int | None = Query(default=None, gt=0, description="按适用场站过滤"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ADMIN_OR_SETTLE),
) -> PageResult[CampaignOut]:
    """含预算、已发量、核销量与核销率等**运营内部指标**，仅 admin / settlement 可见。

    车主与车队请改用 GET /api/users/campaigns/available（脱敏公开视图）。
    """
    result = user_dao.list_campaigns(
        db, page, status=campaign_status, applicable_station_id=applicable_station_id
    )
    return PageResult[CampaignOut](**result)


@router.get(
    "/campaigns/available",
    response_model=PageResult[CampaignPublicOut],
    summary="车主/车队可领的活动（脱敏，仅投放中且在有效期内）",
)
def list_available_campaigns(
    page: PageParams = Depends(),
    applicable_station_id: int | None = Query(default=None, gt=0, description="按适用场站过滤"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_DRIVER_OR_FLEET),
) -> PageResult[CampaignPublicOut]:
    """只返回活动名称、券面额、门槛、有效期与适用场站，不含预算与核销统计。"""
    result = user_dao.list_public_campaigns(
        db, page, applicable_station_id=applicable_station_id
    )
    return PageResult[CampaignPublicOut](**result)


@router.post(
    "/campaigns",
    response_model=ActionResult,
    status_code=status.HTTP_201_CREATED,
    summary="创建优惠活动",
)
def create_campaign(
    payload: CampaignIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_ADMIN),
) -> ActionResult:
    """活动创建后处于 payload.status（默认 DRAFT），置为 ACTIVE 才可发券与核销。"""
    campaign = user_dao.create_campaign(db, payload, created_by=user.user_id)
    return ActionResult(
        message=f"活动 {campaign.campaign_code} 创建成功，当前状态 {campaign.status}",
        id=campaign.campaign_id,
    )


@router.patch(
    "/campaigns/{campaign_id}/status",
    response_model=ActionResult,
    summary="活动状态流转（上线/暂停/结束）",
)
def change_campaign_status(
    payload: CampaignStatusIn,
    campaign_id: int = Path(ge=1, description="活动ID"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_ADMIN),
) -> ActionResult:
    """ACTIVE 上线后才能发券与核销；PAUSED/ENDED 后触发器会拒绝继续发券与核销。"""
    campaign = user_dao.update_campaign_status(db, campaign_id, payload.status)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "优惠活动不存在")
    return ActionResult(
        message=f"活动 {campaign.campaign_code} 状态已更新为 {payload.status}",
        id=campaign_id,
    )


@router.post(
    "/campaigns/{campaign_id}/issue",
    response_model=CouponIssueOut,
    status_code=status.HTTP_201_CREATED,
    summary="运营定向发券",
)
def issue_coupons(
    payload: CouponIssueIn,
    campaign_id: int = Path(ge=1, description="活动ID"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_ADMIN),
) -> CouponIssueOut:
    """逐张发券；活动未投放、券量发完、超出每人限领都会被触发器拒绝（400）。

    发券人（当前管理员）写入 coupon_record.issued_by，便于审计。
    """
    if user_dao.get_campaign(db, campaign_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "优惠活动不存在")
    result = user_dao.issue_coupons(db, campaign_id, payload, issued_by=user.user_id)
    return CouponIssueOut(**result)


# ---------------------------------------------------------------------------
# 五、券包与核销（车主/车队视角）
# ---------------------------------------------------------------------------
@router.get("/coupons/mine", response_model=PageResult[CouponOut], summary="本人券包（含可用标记）")
def list_my_coupons(
    page: PageParams = Depends(),
    usable_only: bool = Query(default=False, description="仅返回未核销且未过期的券"),
    coupon_status: COUPON_STATUS_LITERAL | None = Query(
        default=None, alias="status", description="ISSUED/REDEEMED/EXPIRED/VOID"
    ),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_DRIVER_OR_FLEET),
) -> PageResult[CouponOut]:
    """按“可用优先、将到期在前”排序，方便车主先用在快到期的券上。"""
    result = user_dao.list_my_coupons(
        db, user.user_id, page, usable_only=usable_only, status=coupon_status
    )
    return PageResult[CouponOut](**result)


@router.post("/coupons/redeem", response_model=CouponRedeemOut, summary="核销优惠券抵扣订单")
def redeem_coupon(
    payload: CouponRedeemIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_DRIVER_OR_FLEET),
) -> CouponRedeemOut:
    """核销与订单 1:1；重复核销或一单多券会分别撞唯一索引/触发器，返回 409 或 400。"""
    if user_dao.get_order_owner(db, payload.order_id) != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能核销本人订单上的优惠券")
    return user_dao.redeem_coupon(db, user.user_id, payload)


@router.get("/redeems", response_model=PageResult[CouponRedeemRecordOut], summary="核销台账（对账用）")
def list_redeem_records(
    page: PageParams = Depends(),
    user_id: int | None = Query(default=None, gt=0, description="按持券人过滤"),
    station_id: int | None = Query(default=None, gt=0, description="按核销场站过滤"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ADMIN_OR_SETTLE),
) -> PageResult[CouponRedeemRecordOut]:
    """管理员与结算专员按券码/订单/场站核对核销与抵扣金额（走 v_coupon_redeem_ledger）。"""
    result = user_dao.list_redeem_records(db, page, user_id=user_id, station_id=station_id)
    return PageResult[CouponRedeemRecordOut](**result)


# ---------------------------------------------------------------------------
# 六、按主键查档案（必须放在最后声明）
# 说明：/api/users/{user_id} 这种路径参数路由会把 "vehicles"、"campaigns" 等静态段
#       一并吃掉（FastAPI 按声明顺序匹配），因此统一收在本文件末尾，避免遮蔽业务路由。
# ---------------------------------------------------------------------------
@router.get("/{user_id}", response_model=UserMaskedOut, summary="按用户ID查询脱敏档案")
def get_user_detail(
    user_id: int = Path(ge=1, description="用户ID"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ADMIN_OR_SETTLE),
) -> UserMaskedOut:
    """管理员/结算专员按 ID 查看单条脱敏档案。"""
    row = user_dao.get_user_masked(db, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    return UserMaskedOut.model_validate(row)


@router.patch("/{user_id}/status", response_model=ActionResult, summary="冻结/解冻/禁用账号")
def change_user_status(
    payload: UserStatusIn,
    user_id: int = Path(ge=1, description="用户ID"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_ADMIN),
) -> ActionResult:
    """冻结或禁用后，登录接口会直接返回 403，会话令牌到期后自然失效。"""
    if user_id == user.user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能修改自己的账号状态")
    entity = user_dao.set_user_status(db, user_id, payload.status)
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    return ActionResult(message=f"用户 {user_id} 状态已更新为 {payload.status}", id=user_id)
