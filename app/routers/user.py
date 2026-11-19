"""业务线4：用户与会员运营路由（负责人：成员 4）。

前缀 /api/users。RBAC 隔离口径（对应任务书 2.2 节与 routers/README 权限矩阵）：
1. 平台运营管理员 admin：用户档案全量、代开账号、冻结/禁用、创建活动与定向发券、车队开卡；
2. 客服与结算专员 settlement：用户档案与车队授信只读、核销台账对账，不可发券/开卡；
3. 普通车主 driver：仅本人档案/券包/车辆，扫码充电后核销本人券；
4. 企业客户 fleet：本企业车队授信与车辆（严格按 JWT 内的 fleet_id 行级隔离）；
5. 场站运维 ops：不开放本业务线接口（工单相关在 /api/monitor）。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, RoleEnum, get_current_user, hash_password, require_roles
from app.dao import user as user_dao
from app.db import get_db
from app.schemas.common import ActionResult, PageParams, PageResult
from app.schemas.user import (
    CampaignIn,
    CampaignOut,
    CouponIssueIn,
    CouponIssueOut,
    CouponOut,
    CouponRedeemIn,
    CouponRedeemOut,
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


# ---------------------------------------------------------------------------
# 一、用户档案与账号
# ---------------------------------------------------------------------------
@router.get("", response_model=PageResult[UserMaskedOut], summary="分页查询脱敏用户档案")
def list_users(
    page: PageParams = Depends(),
    role: str | None = Query(default=None, description="按角色过滤：driver/ops/settlement/fleet/admin"),
    user_status: str | None = Query(
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
    bind_status: str | None = Query(default=None, description="BOUND/UNBOUND/DISABLED"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PageResult[VehicleOut]:
    """车主/车队固定只看自己或本企业的车；管理员可查全量并按车主过滤。"""
    if user.role in (RoleEnum.DRIVER, RoleEnum.FLEET):
        target_user_id = user.user_id
        result = user_dao.list_vehicles(db, page, user_id=target_user_id, bind_status=bind_status)
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
    """车牌明文只在此处出现；同一车牌重复绑定由唯一索引拦截并返回 409。"""
    fleet_id = user.fleet_id if user.role == RoleEnum.FLEET else None
    vehicle = user_dao.bind_vehicle(db, user.user_id, payload, fleet_id=fleet_id)
    row = user_dao.get_vehicle_masked(db, vehicle.vehicle_id)
    if row is None:  # pragma: no cover - 刚写入必然可查
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "车辆已绑定但档案读取失败")
    return VehicleOut.model_validate(row)


@router.delete("/vehicles/{vehicle_id}", response_model=ActionResult, summary="解绑本人车辆")
def unbind_vehicle(
    vehicle_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_DRIVER_OR_FLEET),
) -> ActionResult:
    """只能解绑本人（或本企业）车辆；非本人车辆按不存在处理，避免探测他人车辆ID。"""
    vehicle = user_dao.unbind_vehicle(db, vehicle_id, user_id=user.user_id)
    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "车辆不存在或不属于当前用户")
    return ActionResult(message=f"车辆 {vehicle_id} 已解绑", id=vehicle_id)


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
    fleet_status: str | None = Query(default=None, alias="status", description="ACTIVE/SUSPENDED/CLOSED"),
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
@router.get("/campaigns", response_model=PageResult[CampaignOut], summary="分页查询优惠活动与核销率")
def list_campaigns(
    page: PageParams = Depends(),
    campaign_status: str | None = Query(default=None, alias="status", description="DRAFT/ACTIVE/PAUSED/ENDED"),
    applicable_station_id: int | None = Query(default=None, gt=0, description="按适用场站过滤"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> PageResult[CampaignOut]:
    """车主/车队只能看到投放中的活动；管理员与结算专员可看全量（含核销率）。"""
    if user.role in (RoleEnum.DRIVER, RoleEnum.FLEET):
        result = user_dao.list_campaigns(
            db, page, status="ACTIVE", applicable_station_id=applicable_station_id
        )
        return PageResult[CampaignOut](**result)

    if user.role not in (RoleEnum.ADMIN, RoleEnum.SETTLEMENT):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前角色无权查看活动投放数据")

    result = user_dao.list_campaigns(
        db, page, status=campaign_status, applicable_station_id=applicable_station_id
    )
    return PageResult[CampaignOut](**result)


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


@router.post(
    "/campaigns/{campaign_id}/issue",
    response_model=CouponIssueOut,
    status_code=status.HTTP_201_CREATED,
    summary="运营定向发券",
)
def issue_coupons(
    campaign_id: int,
    payload: CouponIssueIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_ADMIN),
) -> CouponIssueOut:
    """逐张发券；活动未投放、券量发完、超出每人限领都会被触发器拒绝（400）。"""
    if user_dao.get_campaign(db, campaign_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "优惠活动不存在")
    result = user_dao.issue_coupons(db, campaign_id, payload)
    return CouponIssueOut(**result)


# ---------------------------------------------------------------------------
# 五、券包与核销（车主/车队视角）
# ---------------------------------------------------------------------------
@router.get("/coupons/mine", response_model=PageResult[CouponOut], summary="本人券包（含可用标记）")
def list_my_coupons(
    page: PageParams = Depends(),
    usable_only: bool = Query(default=False, description="仅返回未核销且未过期的券"),
    coupon_status: str | None = Query(default=None, alias="status", description="ISSUED/REDEEMED/EXPIRED/VOID"),
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


@router.get("/redeems", response_model=PageResult[dict], summary="核销台账（对账用）")
def list_redeem_records(
    page: PageParams = Depends(),
    user_id: int | None = Query(default=None, gt=0, description="按持券人过滤"),
    station_id: int | None = Query(default=None, gt=0, description="按核销场站过滤"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ADMIN_OR_SETTLE),
) -> PageResult[dict]:
    """管理员与结算专员按券码/订单/场站核对核销与抵扣金额。"""
    result = user_dao.list_redeem_records(db, page, user_id=user_id, station_id=station_id)
    return PageResult[dict](**result)


# ---------------------------------------------------------------------------
# 六、按主键查档案（必须放在最后声明）
# 说明：/api/users/{user_id} 这种路径参数路由会把 "vehicles"、"campaigns" 等静态段
#       一并吃掉（FastAPI 按声明顺序匹配），因此统一收在本文件末尾，避免遮蔽业务路由。
# ---------------------------------------------------------------------------
@router.get("/{user_id}", response_model=UserMaskedOut, summary="按用户ID查询脱敏档案")
def get_user_detail(
    user_id: int,
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
    user_id: int,
    payload: UserStatusIn,
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
