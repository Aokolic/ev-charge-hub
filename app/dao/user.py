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

from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.dao.common import (
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
SQL_PUBLIC_CAMPAIGN = "SELECT * FROM v_coupon_public_campaign WHERE 1 = 1"
SQL_FLEET_CREDIT = "SELECT * FROM v_fleet_credit_usage WHERE 1 = 1"
SQL_REDEEM_LEDGER = "SELECT * FROM v_coupon_redeem_ledger WHERE 1 = 1"

# 动态过滤片段白名单：键 -> 参数化 SQL 片段（值永远走 :param 绑定）
_USER_FILTERS = {
    "role": " AND role = :role",
    "status": " AND status = :status",
    "keyword": " AND (username LIKE :keyword ESCAPE '\\\\' OR phone_masked LIKE :keyword ESCAPE '\\\\')",
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
        # 转义 LIKE 通配符：否则用户输入 "%" / "_" 会变成通配查询（可探测全表）
        escaped = (
            keyword.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        sql += _USER_FILTERS["keyword"]
        params["keyword"] = f"%{escaped}%"
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
    """管理员代开运维/结算/管理员/车队账号：一次 INSERT、一条事务。

    公共底座的 create_account 内部自带 commit，若复用它再补档案列就会变成两次提交，
    中途失败会留下"只有认证列、没有档案列"的半成品账号（违反 AGENTS.md 单事务原则）。
    因此这里用一条 INSERT 写全所有列，密码哈希由调用方传入。
    """
    phone_cipher = encrypt_sensitive(payload.phone)
    phone_hash = hash_sensitive_lookup(payload.phone)
    phone_masked = mask_phone(payload.phone)

    try:
        result = db.execute(
            text(
                "INSERT INTO user_account ("
                "username, password_hash, role, fleet_id, "
                "phone_cipher, phone_hash, phone_masked, "
                "realname_status, realname_verified_at, register_channel, member_level, "
                "balance, status, failed_login_count"
                ") VALUES ("
                ":username, :password_hash, :role, :fleet_id, "
                ":phone_cipher, :phone_hash, :phone_masked, "
                ":realname, CASE WHEN :realname = 'VERIFIED' THEN NOW() ELSE NULL END, "
                ":channel, :level, 0.00, 'ACTIVE', 0"
                ")"
            ),
            {
                "username": payload.username,
                "password_hash": password_hash,
                "role": payload.role.value,
                "fleet_id": payload.fleet_id,
                "phone_cipher": phone_cipher,
                "phone_hash": phone_hash,
                "phone_masked": phone_masked,
                "realname": payload.realname_status,
                "channel": payload.register_channel,
                "level": payload.member_level,
            },
        )
        user_id = int(result.lastrowid or 0)
        db.commit()
    except Exception:
        db.rollback()
        raise

    entity = db.get(UserAccount, user_id)
    if entity is None:  # pragma: no cover - 提交成功后必然存在
        raise RuntimeError("账号写入后回读失败")
    return entity


def set_user_status(db: Session, user_id: int, status: str) -> UserAccount | None:
    """冻结/解冻/禁用账号；账号不存在返回 None。"""
    entity = db.get(UserAccount, user_id)
    if entity is None:
        return None
    try:
        entity.status = status
        db.commit()
    except Exception:
        db.rollback()
        raise
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

    一车同刻一主由生成列 active_plate_hash 上的唯一索引 uk_vehicle_active_plate 保证：
    - 若该车牌存在**已解绑**的历史行，直接复用该行改回 BOUND（不新增行，保留档案与主键）；
    - 若该车牌正被他人绑定，本次 INSERT 会撞唯一索引抛 1062，由全局处理器翻译为 409。
    因此“解绑后车牌永久锁死”的问题不再存在。
    """
    plate_hash = hash_sensitive_lookup(payload.plate_no)

    try:
        reuse = db.execute(
            select(Vehicle)
            .where(Vehicle.plate_hash == plate_hash, Vehicle.bind_status == "UNBOUND")
            .with_for_update()
        ).scalars().first()

        if reuse is not None:
            vehicle = reuse
            vehicle.user_id = user_id
            vehicle.fleet_id = fleet_id
            vehicle.bind_status = "BOUND"
            vehicle.bound_at = datetime.now()
            vehicle.plate_cipher = encrypt_sensitive(payload.plate_no)
            vehicle.plate_masked = mask_plate(payload.plate_no)
            vehicle.plate_color = payload.plate_color
            vehicle.vehicle_model = payload.vehicle_model
            vehicle.battery_capacity_kwh = payload.battery_capacity_kwh
            vehicle.max_charge_power = payload.max_charge_power
            vehicle.supports_fast_charge = payload.supports_fast_charge
        else:
            vehicle = Vehicle(
                user_id=user_id,
                fleet_id=fleet_id,
                plate_cipher=encrypt_sensitive(payload.plate_no),
                plate_hash=plate_hash,
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

        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(vehicle)
    return vehicle


def unbind_vehicle(
    db: Session,
    vehicle_id: int,
    user_id: int | None = None,
    fleet_id: int | None = None,
) -> Vehicle | None:
    """解绑车辆：置 UNBOUND 并清空车主与车队，使该车牌可被任何人重新绑定。

    归属校验：driver 传 user_id（只能解绑本人车），fleet 传 fleet_id（只能解绑本企业车），
    不匹配一律返回 None，避免越权解绑并防止通过返回差异探测他人车辆。
    """
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None:
        return None
    if user_id is not None and vehicle.user_id != user_id:
        return None
    if fleet_id is not None and vehicle.fleet_id != fleet_id:
        return None

    try:
        vehicle.bind_status = "UNBOUND"
        vehicle.user_id = None
        vehicle.fleet_id = None
        db.commit()
    except Exception:
        db.rollback()
        raise
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
def list_public_campaigns(
    db: Session,
    page: PageParams,
    applicable_station_id: int | None = None,
) -> dict[str, Any]:
    """车主/车队可见的活动（视图已限定 ACTIVE 且当前在有效期内），不含运营内部指标。"""
    sql = SQL_PUBLIC_CAMPAIGN
    params: dict[str, Any] = {}
    if applicable_station_id:
        sql += " AND applicable_station_id = :station_id"
        params["station_id"] = applicable_station_id
    return fetch_view_page(db, sql, params, page, "face_value DESC, campaign_id DESC")


def update_campaign_status(db: Session, campaign_id: int, status: str) -> CouponCampaign | None:
    """活动状态流转（激活/暂停/结束）；活动不存在返回 None。"""
    campaign = db.get(CouponCampaign, campaign_id)
    if campaign is None:
        return None
    try:
        campaign.status = status
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(campaign)
    return campaign


# ---------------------------------------------------------------------------
# 五、优惠券发放与核销
# ---------------------------------------------------------------------------
def issue_coupons(
    db: Session,
    campaign_id: int,
    payload: CouponIssueIn,
    issued_by: int | None = None,
) -> dict[str, Any]:
    """调用存储过程批量发券；活动状态、券量上限与每人限领均由发券触发器校验。

    返回本次生成的券码：过程按 8 位批次前缀生成券码，本事务内按前缀回查，
    不再使用 “commit 后按 ORDER BY ... LIMIT” 的写法（并发发券时会取到别人的券）。
    """
    batch = uuid4().hex[:8].upper()

    try:
        db.execute(
            text("CALL sp_issue_coupon_pack(:uid, :cid, :cnt, :channel, :by, :batch, @issued)"),
            {
                "uid": payload.user_id,
                "cid": campaign_id,
                "cnt": payload.count,
                "channel": payload.channel,
                "by": issued_by,
                "batch": batch,
            },
        )
        issued = int(db.execute(text("SELECT @issued")).scalar_one() or 0)
        coupon_nos = [
            str(row[0])
            for row in db.execute(
                text(
                    "SELECT coupon_no FROM coupon_record "
                    "WHERE coupon_no LIKE CONCAT(:batch, '%') "
                    "ORDER BY coupon_id ASC"
                ),
                {"batch": batch},
            ).all()
        ]
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "campaign_id": campaign_id,
        "user_id": payload.user_id,
        "issued_count": issued,
        "coupon_nos": coupon_nos,
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

    过程内部：对券行与订单行加 FOR UPDATE 行锁 -> 校验归属/状态/场站/金额
    -> 写 coupon_redeem_record（触发器回写券状态）-> 抵扣订单金额，
    并通过 OUT 参数回传折前/折后应付，调用方无需在 commit 后再回查（避免脏读与竞态）。
    任一步失败（触发器 SIGNAL、uk_redeem_order 一单一券冲突）整体回滚。
    """
    try:
        db.execute(
            text("CALL sp_coupon_redeem(:cno, :oid, :uid, @discount, @before, @after)"),
            {"cno": payload.coupon_no, "oid": payload.order_id, "uid": user_id},
        )
        discount = Decimal(str(db.execute(text("SELECT @discount")).scalar_one() or "0.00"))
        before = Decimal(str(db.execute(text("SELECT @before")).scalar_one() or "0.00"))
        after = Decimal(str(db.execute(text("SELECT @after")).scalar_one() or "0.00"))
        db.commit()
    except Exception:
        db.rollback()
        raise

    return CouponRedeemOut(
        coupon_no=payload.coupon_no,
        order_id=payload.order_id,
        face_value=discount,
        order_amount=before,
        payable_amount=after,
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
    """核销台账分页查询（admin / settlement 对账用）：走视图 v_coupon_redeem_ledger。"""
    sql = SQL_REDEEM_LEDGER
    params: dict[str, Any] = {}
    if user_id:
        sql += " AND user_id = :user_id"
        params["user_id"] = user_id
    if station_id:
        sql += " AND station_id = :station_id"
        params["station_id"] = station_id
    return fetch_view_page(db, sql, params, page, "redeemed_at DESC, redeem_id DESC")


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
    "update_campaign_status",
    "issue_coupons",
    "list_campaigns",
    "list_public_campaigns",
    "list_fleet_credit",
    "list_my_coupons",
    "list_redeem_records",
    "list_users",
    "list_vehicles",
    "redeem_coupon",
    "set_user_status",
    "unbind_vehicle",
]
