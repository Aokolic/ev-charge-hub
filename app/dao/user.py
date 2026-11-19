"""业务线4 持久层：用户档案、车辆绑定、企业车队授信与优惠券发放/核销（负责人：成员 4）。

双轨策略：
1. 列表与看板一律查 RBAC 隔离视图（v_user_masked_profile / v_vehicle_binding /
   v_coupon_user_wallet / v_coupon_campaign_stat / v_fleet_credit_usage），
   原始表不直出前端；动态条件只允许从本文件的 DAO 白名单片段拼接，值全部参数绑定；
2. 单表写入走 SQLAlchemy 2.0 ORM（db.add / db.flush / db.commit）；
3. 发券与核销走存储过程（sp_issue_coupon_pack / sp_coupon_redeem），
   业务校验与“写核销记录 + 回写券状态 + 抵扣订单金额”全部在库内一次事务完成，
   Python 侧只负责组装参数，保证与 SQL 脚本口径一致；
4. 约束异常（1062 车牌重复/券重复核销、3819 CHECK、1644 触发器 SIGNAL）不在本层吞掉，
   直接向上抛出，由 app/main.py 统一翻译为 409/400。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.dao.common import (
    create_account,
    encrypt_sensitive,
    fetch_view_page,
    hash_sensitive_lookup,
    mask_phone,
    mask_plate,
)
from app.models.user import CouponCampaign, FleetAccount, UserAccount, Vehicle
from app.schemas.common import PageParams
from app.schemas.user import (
    CampaignIn,
    CouponIssueIn,
    CouponRedeemIn,
    CouponRedeemOut,
    FleetCardIn,
    UserRegisterIn,
    VehicleBindIn,
)

# --- 受控 SQL 常量：只允许在此拼接本文件的白名单片段，严禁拼接任何用户输入 ---------------
SQL_USER_LIST = "SELECT * FROM v_user_masked_profile WHERE 1 = 1"
SQL_USER_ONE = "SELECT * FROM v_user_masked_profile WHERE user_id = :uid"
SQL_VEHICLE_LIST = "SELECT * FROM v_vehicle_binding WHERE 1 = 1"
SQL_COUPON_WALLET = "SELECT * FROM v_coupon_user_wallet WHERE 1 = 1"
SQL_CAMPAIGN_STAT = "SELECT * FROM v_coupon_campaign_stat WHERE 1 = 1"
SQL_FLEET_CREDIT = "SELECT * FROM v_fleet_credit_usage WHERE 1 = 1"

# 动态过滤片段白名单：键 -> 参数化 SQL 片段（值永远走 :param 绑定）
_USER_FILTERS = {
    "role": " AND role = :role",
    "status": " AND status = :status",
    "keyword": " AND (username LIKE :keyword OR phone_masked LIKE :keyword)",
}
_VEHICLE_FILTERS = {
    "user_id": " AND user_id = :user_id",
    "fleet_id": " AND fleet_id = :fleet_id",
    "bind_status": " AND bind_status = :bind_status",
}
_COUPON_FILTERS = {
    "user_id": " AND user_id = :user_id",
    "usable": " AND usable_flag = 1",
    "status": " AND status = :status",
}


def _fetch_row(db: Session, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """执行单行查询并返回字典（视图查询统一入口，参数一律绑定）。"""
    row = db.execute(text(sql), params).mappings().first()
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# 一、用户档案与账号
# ---------------------------------------------------------------------------
def list_users(
    db: Session,
    page: PageParams,
    role: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    """按角色/状态/关键字分页查询脱敏用户档案（默认按会员等级与余额排序）。"""
    sql = SQL_USER_LIST
    params: dict[str, Any] = {}
    if role:
        sql += _USER_FILTERS["role"]
        params["role"] = role
    if status:
        sql += _USER_FILTERS["status"]
        params["status"] = status
    if keyword and keyword.strip():
        sql += _USER_FILTERS["keyword"]
        params["keyword"] = f"%{keyword.strip()}%"
    return fetch_view_page(db, sql, params, page, "created_at DESC, user_id ASC")


def get_user(db: Session, user_id: int) -> UserAccount | None:
    """按主键取账号实体（用于状态变更等写操作）。"""
    return db.get(UserAccount, user_id)


def get_user_masked(db: Session, user_id: int) -> dict[str, Any] | None:
    """按主键取脱敏档案视图行（对外只出脱敏手机号）。"""
    return _fetch_row(db, SQL_USER_ONE, {"uid": user_id})


def get_user_by_phone(db: Session, phone_plain: str) -> UserAccount | None:
    """用 HMAC 盲索引按手机号等值检索账号，全程不比对明文。"""
    return db.execute(
        select(UserAccount).where(
            UserAccount.phone_hash == hash_sensitive_lookup(phone_plain)
        )
    ).scalars().first()


def create_staff_account(
    db: Session,
    payload: UserRegisterIn,
    password_hash: str,
) -> UserAccount:
    """管理员代开运维/结算/管理员/车队账号：复用公共底座写入加密手机号，再补业务档案列。"""
    account = create_account(
        db=db,
        username=payload.username,
        password_hash=password_hash,
        role=payload.role.value,
        phone_plain=payload.phone,
        fleet_id=payload.fleet_id,
    )
    # 公共底座只写认证必需列，实名状态/会员等级/注册渠道属于业务线4 的档案列，这里补齐
    db.execute(
        text(
            "UPDATE user_account "
            "SET realname_status = :realname, "
            "    realname_verified_at = CASE WHEN :realname = 'VERIFIED' THEN NOW() ELSE NULL END, "
            "    member_level = :level, "
            "    register_channel = :channel "
            "WHERE user_id = :uid"
        ),
        {
            "realname": payload.realname_status,
            "level": payload.member_level,
            "channel": payload.register_channel,
            "uid": account.user_id,
        },
    )
    db.commit()

    entity = db.get(UserAccount, account.user_id)
    if entity is None:  # pragma: no cover - 理论上提交成功后必然存在
        raise RuntimeError("账号写入后回读失败")
    return entity


def set_user_status(db: Session, user_id: int, status: str) -> UserAccount | None:
    """冻结/解冻/禁用账号；账号不存在返回 None。"""
    entity = db.get(UserAccount, user_id)
    if entity is None:
        return None
    entity.status = status
    db.commit()
    db.refresh(entity)
    return entity


# ---------------------------------------------------------------------------
# 二、车辆绑定（一车一主：plate_hash 唯一索引兜底）
# ---------------------------------------------------------------------------
def list_vehicles(
    db: Session,
    page: PageParams,
    user_id: int | None = None,
    fleet_id: int | None = None,
    bind_status: str | None = None,
) -> dict[str, Any]:
    """分页查询车辆绑定视图，可按车主、车队与绑定状态过滤。"""
    sql = SQL_VEHICLE_LIST
    params: dict[str, Any] = {}
    if user_id:
        sql += _VEHICLE_FILTERS["user_id"]
        params["user_id"] = user_id
    if fleet_id:
        sql += _VEHICLE_FILTERS["fleet_id"]
        params["fleet_id"] = fleet_id
    if bind_status:
        sql += _VEHICLE_FILTERS["bind_status"]
        params["bind_status"] = bind_status
    return fetch_view_page(db, sql, params, page, "created_at DESC, vehicle_id DESC")


def get_vehicle(db: Session, vehicle_id: int) -> Vehicle | None:
    """按主键取车辆实体（用于解绑校验）。"""
    return db.get(Vehicle, vehicle_id)


def bind_vehicle(
    db: Session,
    user_id: int,
    payload: VehicleBindIn,
    fleet_id: int | None = None,
) -> Vehicle:
    """绑定车辆：车牌经 Fernet 加密 + HMAC 盲索引 + 脱敏后落库。

    车牌盲索引全局唯一：同一车牌第二次绑定会触发 uk_vehicle_plate_hash 的 1062，
    由全局异常处理器翻译为 409，从结构上保证“同刻一车一主”。
    """
    vehicle = Vehicle(
        user_id=user_id,
        fleet_id=fleet_id,
        plate_cipher=encrypt_sensitive(payload.plate_no),
        plate_hash=hash_sensitive_lookup(payload.plate_no),
        plate_masked=mask_plate(payload.plate_no),
        plate_color=payload.plate_color,
        vehicle_model=payload.vehicle_model,
        battery_capacity_kwh=payload.battery_capacity_kwh,
        max_charge_power=payload.max_charge_power,
        supports_fast_charge=payload.supports_fast_charge,
        bind_status="BOUND",
        bound_at=datetime.now(),
    )
    db.add(vehicle)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(vehicle)
    return vehicle


def unbind_vehicle(db: Session, vehicle_id: int, user_id: int | None = None) -> Vehicle | None:
    """解绑车辆（置 UNBOUND 并清空车主）；非本人车辆返回 None，避免越权解绑。"""
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None:
        return None
    if user_id is not None and vehicle.user_id != user_id:
        return None
    vehicle.bind_status = "UNBOUND"
    vehicle.user_id = None
    db.commit()
    db.refresh(vehicle)
    return vehicle


def get_vehicle_masked(db: Session, vehicle_id: int) -> dict[str, Any] | None:
    """按主键取车辆绑定视图行（脱敏车牌）。"""
    return _fetch_row(db, "SELECT * FROM v_vehicle_binding WHERE vehicle_id = :vid", {"vid": vehicle_id})


# ---------------------------------------------------------------------------
# 三、企业车队与授信额度
# ---------------------------------------------------------------------------
def create_fleet(db: Session, payload: FleetCardIn) -> FleetAccount:
    """企业车队开卡建档：对接人手机号同样走加密 + 盲索引 + 脱敏三列。"""
    fleet = FleetAccount(
        fleet_code=payload.fleet_code,
        fleet_name=payload.fleet_name,
        credit_code=payload.credit_code,
        contact_name=payload.contact_name,
        contact_phone_cipher=encrypt_sensitive(payload.contact_phone),
        contact_phone_hash=hash_sensitive_lookup(payload.contact_phone),
        contact_phone_masked=mask_phone(payload.contact_phone),
        credit_limit=payload.credit_limit,
        credit_used=Decimal("0.00"),
        status="ACTIVE",
    )
    db.add(fleet)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(fleet)
    return fleet


def list_fleet_credit(
    db: Session,
    page: PageParams,
    fleet_id: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """分页查询车队授信与本月消费视图；fleet 角色在路由层传入自身 fleet_id 做行级隔离。"""
    sql = SQL_FLEET_CREDIT
    params: dict[str, Any] = {}
    if fleet_id:
        sql += " AND fleet_id = :fleet_id"
        params["fleet_id"] = fleet_id
    if status:
        sql += " AND status = :status"
        params["status"] = status
    return fetch_view_page(db, sql, params, page, "credit_used DESC, fleet_id ASC")


# ---------------------------------------------------------------------------
# 四、优惠活动
# ---------------------------------------------------------------------------
def create_campaign(db: Session, payload: CampaignIn, created_by: int) -> CouponCampaign:
    """创建优惠活动（面额/门槛/有效期已在 Schema 层校验，库内 CHECK 再兜底一次）。"""
    campaign = CouponCampaign(
        campaign_code=payload.campaign_code,
        campaign_name=payload.campaign_name,
        coupon_kind=payload.coupon_kind,
        face_value=payload.face_value,
        threshold_amount=payload.threshold_amount,
        applicable_station_id=payload.applicable_station_id,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        issue_total=payload.issue_total,
        issued_count=0,
        per_user_limit=payload.per_user_limit,
        redeem_rule=payload.redeem_rule,
        status=payload.status,
        created_by=created_by,
    )
    db.add(campaign)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(campaign)
    return campaign


def get_campaign(db: Session, campaign_id: int) -> CouponCampaign | None:
    """按主键取活动实体。"""
    return db.get(CouponCampaign, campaign_id)


def list_campaigns(
    db: Session,
    page: PageParams,
    status: str | None = None,
    applicable_station_id: int | None = None,
) -> dict[str, Any]:
    """分页查询活动投放与核销看板视图，可按状态与适用场站过滤。"""
    sql = SQL_CAMPAIGN_STAT
    params: dict[str, Any] = {}
    if status:
        sql += " AND status = :status"
        params["status"] = status
    if applicable_station_id:
        sql += " AND applicable_station_id = :station_id"
        params["station_id"] = applicable_station_id
    return fetch_view_page(db, sql, params, page, "valid_from DESC, campaign_id DESC")


# ---------------------------------------------------------------------------
# 五、优惠券发放与核销
# ---------------------------------------------------------------------------
def issue_coupons(db: Session, campaign_id: int, payload: CouponIssueIn) -> dict[str, Any]:
    """调用存储过程批量发券；活动状态、券量上限与每人限领均由发券触发器校验。

    返回本次生成的券码，便于前端/客服把券码告知持券人。
    """
    db.execute(
        text("CALL sp_issue_coupon_pack(:uid, :cid, :cnt, :channel, @issued)"),
        {
            "uid": payload.user_id,
            "cid": campaign_id,
            "cnt": payload.count,
            "channel": payload.channel,
        },
    )
    issued = int(db.execute(text("SELECT @issued")).scalar_one() or 0)
    db.commit()

    coupon_nos = [
        str(row[0])
        for row in db.execute(
            text(
                "SELECT coupon_no FROM coupon_record "
                "WHERE user_id = :uid AND campaign_id = :cid "
                "ORDER BY coupon_id DESC LIMIT :cnt"
            ),
            {"uid": payload.user_id, "cid": campaign_id, "cnt": max(issued, 1)},
        ).all()
    ]
    return {
        "campaign_id": campaign_id,
        "user_id": payload.user_id,
        "issued_count": issued,
        "coupon_nos": list(reversed(coupon_nos)) if issued else [],
    }


def list_my_coupons(
    db: Session,
    user_id: int,
    page: PageParams,
    usable_only: bool = False,
    status: str | None = None,
) -> dict[str, Any]:
    """车主本人券包分页查询：可按“仅可用”或指定券状态过滤。"""
    sql = SQL_COUPON_WALLET
    params: dict[str, Any] = {"user_id": user_id}
    sql += _COUPON_FILTERS["user_id"]
    if usable_only:
        sql += _COUPON_FILTERS["usable"]
    if status:
        sql += _COUPON_FILTERS["status"]
        params["status"] = status
    return fetch_view_page(db, sql, params, page, "usable_flag DESC, expire_at ASC, coupon_id DESC")


def redeem_coupon(db: Session, user_id: int, payload: CouponRedeemIn) -> CouponRedeemOut:
    """调用存储过程核销优惠券。

    过程内部：写 coupon_redeem_record -> 触发器回写券状态为 REDEEMED
    -> 抵扣 charge_order.discount_amount / total_amount，全程一条事务；
    任一步失败（触发器 SIGNAL、uk_redeem_order 一单一券冲突）整体回滚。
    """
    db.execute(
        text("CALL sp_coupon_redeem(:cno, :oid, @discount)"),
        {"cno": payload.coupon_no, "oid": payload.order_id},
    )
    discount = Decimal(str(db.execute(text("SELECT @discount")).scalar_one() or "0.00"))
    db.commit()

    order = db.execute(
        text(
            "SELECT total_amount, discount_amount FROM charge_order WHERE order_id = :oid"
        ),
        {"oid": payload.order_id},
    ).mappings().first()
    if order is None:  # pragma: no cover - 过程内已校验订单存在
        raise ValueError("充电订单不存在")

    payable = Decimal(str(order["total_amount"]))
    return CouponRedeemOut(
        coupon_no=payload.coupon_no,
        order_id=payload.order_id,
        face_value=discount,
        order_amount=payable + discount,
        payable_amount=payable,
    )


def get_order_owner(db: Session, order_id: int) -> int | None:
    """只读取充电订单的车主ID（跨线只读，不参与业务线2 的写入）。

    路由层用它先做一次归属校验：非本人订单直接 403，不必等触发器报错再回滚。
    """
    row = db.execute(
        text("SELECT user_id FROM charge_order WHERE order_id = :oid"), {"oid": order_id}
    ).first()
    return int(row[0]) if row is not None else None


def list_redeem_records(
    db: Session,
    page: PageParams,
    user_id: int | None = None,
    station_id: int | None = None,
) -> dict[str, Any]:
    """核销台账分页查询（admin / settlement 对账用）：券码 + 订单 + 抵扣金额。"""
    sql = (
        "SELECT d.redeem_id, d.coupon_id, c.coupon_no, d.order_id, d.user_id, "
        "       d.station_id, d.face_value, d.order_amount, d.redeemed_at "
        "FROM coupon_redeem_record AS d "
        "INNER JOIN coupon_record AS c ON c.coupon_id = d.coupon_id "
        "WHERE 1 = 1"
    )
    params: dict[str, Any] = {}
    if user_id:
        sql += " AND d.user_id = :user_id"
        params["user_id"] = user_id
    if station_id:
        sql += " AND d.station_id = :station_id"
        params["station_id"] = station_id
    return fetch_view_page(db, sql, params, page, "d.redeemed_at DESC, d.redeem_id DESC")


__all__ = [
    "bind_vehicle",
    "create_campaign",
    "create_fleet",
    "create_staff_account",
    "get_campaign",
    "get_order_owner",
    "get_user",
    "get_user_by_phone",
    "get_user_masked",
    "get_vehicle",
    "get_vehicle_masked",
    "issue_coupons",
    "list_campaigns",
    "list_fleet_credit",
    "list_my_coupons",
    "list_redeem_records",
    "list_users",
    "list_vehicles",
    "redeem_coupon",
    "set_user_status",
    "unbind_vehicle",
]
