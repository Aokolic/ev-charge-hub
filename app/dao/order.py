"""业务线2 持久层：订单查询、开单、结束计费、支付与退款。

约定：
1. 列表查询一律走 v_order_owner 视图，不直接 select 原表；
2. 写操作走 ORM，一次业务操作只 commit 一次；
3. 费用不由 Python 算，交给存储过程 sp_order_calc，保证和 SQL 脚本口径一致；
4. 开单要锁枪、校验归属并联动枪状态，不能只信任前端传的 gun_id/station_id。
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.dao.common import fetch_view_page
from app.models.order import ChargeOrder, OrderFeeDetail, PaymentRecord, PricingRule
from app.schemas.common import PageParams
from app.schemas.order import OrderFinishIn, OrderStartIn, PaymentIn, RefundIn

# 视图查询语句写死在常量里，fetch_view_page 只接受这类受控 SQL
SQL_ORDER_MINE = "SELECT * FROM v_order_owner WHERE user_id = :uid"
SQL_ORDER_ALL = "SELECT * FROM v_order_owner WHERE 1 = 1"
SQL_ORDER_FLEET = (
    "SELECT v.* FROM v_order_owner v "
    "JOIN user_account u ON u.user_id = v.user_id "
    "WHERE u.fleet_id = :fid"
)
SQL_ORDER_PERIOD_STAT = """
SELECT period, stat_date, order_cnt, total_kwh, energy_fee, service_fee, avg_price
FROM v_order_period_stat
WHERE stat_date BETWEEN :start_date AND :end_date
ORDER BY stat_date, period
"""

# 锁枪并反查桩、站；FOR UPDATE 保证并发开单时同一把枪只有一个事务能进
SQL_LOCK_GUN = """
SELECT g.gun_id, g.current_status, g.pile_id, p.station_id
  FROM charging_gun g
  JOIN charging_pile p ON p.pile_id = g.pile_id
 WHERE g.gun_id = :gid
   FOR UPDATE
"""

SQL_VEHICLE_OWNER = "SELECT user_id, fleet_id FROM vehicle WHERE vehicle_id = :vid"

SQL_GUN_OCCUPY = """
UPDATE charging_gun
   SET current_status = 'OCCUPIED', status_updated_at = NOW()
 WHERE gun_id = :gid
"""

SQL_GUN_RELEASE = """
UPDATE charging_gun
   SET current_status = 'IDLE', status_updated_at = NOW()
 WHERE gun_id = :gid
"""

SQL_STATUS_LOG = """
INSERT INTO device_status_log
       (device_type, pile_id, gun_id, prev_status, new_status, change_source, remark)
VALUES ('GUN', NULL, :gid, :prev, :new, 'ORDER', :remark)
"""


def get_order(db: Session, order_id: int) -> ChargeOrder | None:
    return db.get(ChargeOrder, order_id)


def get_order_detail(db: Session, order_id: int) -> tuple[ChargeOrder | None, list[OrderFeeDetail]]:
    order = db.get(ChargeOrder, order_id)
    if order is None:
        return None, []
    details = db.execute(
        select(OrderFeeDetail)
        .where(OrderFeeDetail.order_id == order_id)
        .order_by(OrderFeeDetail.begin_time)
    ).scalars().all()
    return order, list(details)


def get_user_fleet_id(db: Session, user_id: int) -> int | None:
    return db.execute(
        text("SELECT fleet_id FROM user_account WHERE user_id = :uid"), {"uid": user_id}
    ).scalar()


def list_my_orders(db: Session, user_id: int, page: PageParams) -> dict[str, Any]:
    return fetch_view_page(db, SQL_ORDER_MINE, {"uid": user_id}, page, "start_time DESC")


def list_all_orders(db: Session, page: PageParams) -> dict[str, Any]:
    return fetch_view_page(db, SQL_ORDER_ALL, None, page, "start_time DESC")


def list_fleet_orders(db: Session, fleet_id: int, page: PageParams) -> dict[str, Any]:
    return fetch_view_page(db, SQL_ORDER_FLEET, {"fid": fleet_id}, page, "start_time DESC")


def start_order(db: Session, user_id: int, payload: OrderStartIn) -> ChargeOrder:
    """开单。

    顺序是：锁枪 -> 校验归属与状态 -> 校验车辆归属 -> 插订单 -> 改枪状态并记日志。
    同一把枪已经有人在充时，uk_gun_active 会抛 1062，仍然由唯一索引兜底。
    """
    gun = db.execute(text(SQL_LOCK_GUN), {"gid": payload.gun_id}).mappings().first()
    if gun is None:
        raise ValueError("充电枪不存在")
    if int(gun["station_id"]) != payload.station_id:
        raise ValueError("该充电枪不属于指定场站")
    if gun["current_status"] != "IDLE":
        raise ValueError("充电枪当前不是空闲状态，不能开单")

    if payload.vehicle_id is not None:
        owner = db.execute(text(SQL_VEHICLE_OWNER), {"vid": payload.vehicle_id}).mappings().first()
        if owner is None:
            raise ValueError("车辆不存在")
        if owner["user_id"] is not None and int(owner["user_id"]) != user_id:
            raise ValueError("车辆不属于当前用户")

    order = ChargeOrder(
        order_no="O" + uuid4().hex[:20].upper(),
        user_id=user_id,
        vehicle_id=payload.vehicle_id,
        gun_id=payload.gun_id,
        station_id=payload.station_id,
        start_time=datetime.now(),
        start_soc=payload.start_soc,
        kwh=Decimal("0.000"),
        status="CHARGING",
    )
    db.add(order)
    try:
        db.flush()
        db.execute(text(SQL_GUN_OCCUPY), {"gid": payload.gun_id})
        db.execute(
            text(SQL_STATUS_LOG),
            {
                "pid": gun["pile_id"],
                "gid": payload.gun_id,
                "prev": "IDLE",
                "new": "OCCUPIED",
                "remark": f"开单占用 {order.order_no}",
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return order


def finish_order(db: Session, order_id: int, payload: OrderFinishIn) -> ChargeOrder | None:
    """结束充电并计费：先落 end_time/kwh，再调存储过程拆峰平谷。"""
    # 锁行，避免同一单被两个请求同时结单把明细算两遍
    order = db.execute(
        select(ChargeOrder).where(ChargeOrder.order_id == order_id).with_for_update()
    ).scalars().first()
    if order is None:
        return None
    if order.status != "CHARGING":
        raise ValueError("订单不在充电中，不能结束")
    if payload.end_soc is not None and order.start_soc is not None:
        if Decimal(payload.end_soc) < Decimal(order.start_soc):
            raise ValueError("结束 SOC 不能小于开始 SOC")

    end_time = datetime.now()
    # 同一秒内结单会让 ck_order_time 失败，兜一个最小 1 秒时长
    if end_time <= order.start_time:
        end_time = order.start_time + timedelta(seconds=1)

    try:
        order.end_time = end_time
        order.end_soc = payload.end_soc
        order.kwh = payload.kwh
        db.flush()

        # 过程内部会重算明细，并回写 energy_fee/service_fee/total_amount/status
        db.execute(text("CALL sp_order_calc(:oid)"), {"oid": order_id})
        db.execute(text(SQL_GUN_RELEASE), {"gid": order.gun_id})
        db.execute(
            text(SQL_STATUS_LOG),
            {
                "pid": None,
                "gid": order.gun_id,
                "prev": "OCCUPIED",
                "new": "IDLE",
                "remark": f"结单释放 {order.order_no}",
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return order


def pay_order(db: Session, order_id: int, payload: PaymentIn) -> PaymentRecord | None:
    """记一笔支付流水（只收正数）。

    超付、充电中不能付，这些判断都在触发器 trg_payment_guard 里，
    Python 侧只管插流水；触发器会把订单的 paid_amount/status 一起改掉。
    """
    if db.get(ChargeOrder, order_id) is None:
        return None

    record = PaymentRecord(
        order_id=order_id,
        amount=payload.amount,
        method=payload.method,
        txn_no=payload.txn_no,
        status="SUCCESS",
        paid_at=datetime.now(),
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(record)
    return record


def refund_order(db: Session, order_id: int, payload: RefundIn) -> PaymentRecord | None:
    """退款：插一条负数流水，触发器负责扣减 paid_amount 并回写状态。"""
    order = db.get(ChargeOrder, order_id)
    if order is None:
        return None

    record = PaymentRecord(
        order_id=order_id,
        amount=payload.amount,
        method=payload.method,
        txn_no=payload.txn_no,
        status="REFUNDED",
        paid_at=datetime.now(),
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(record)
    return record


def list_payments(db: Session, order_id: int) -> list[PaymentRecord]:
    return list(
        db.execute(
            select(PaymentRecord)
            .where(PaymentRecord.order_id == order_id)
            .order_by(PaymentRecord.payment_id)
        ).scalars().all()
    )


def cancel_order(db: Session, order_id: int) -> ChargeOrder | None:
    """取消未开始的订单：只有 CHARGING 且没付过钱才能取消。"""
    order = db.get(ChargeOrder, order_id)
    if order is None:
        return None
    if order.status != "CHARGING":
        raise ValueError("只有充电中的订单才能取消")
    if Decimal(order.paid_amount) != 0:
        raise ValueError("已有支付流水的订单不能取消，请走退款")

    try:
        order.status = "CANCELLED"
        order.end_time = datetime.now()
        db.execute(text(SQL_GUN_RELEASE), {"gid": order.gun_id})
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return order


def list_period_stat(db: Session, start_date, end_date) -> list[dict[str, Any]]:
    rows = db.execute(
        text(SQL_ORDER_PERIOD_STAT),
        {"start_date": start_date, "end_date": end_date},
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------- 电价规则维护 ----------
def list_price_rules(db: Session, station_id: int) -> list[PricingRule]:
    """按场站列出生效中的规则，按开始时间排。"""
    return list(
        db.execute(
            select(PricingRule)
            .where(PricingRule.station_id == station_id, PricingRule.is_active.is_(True))
            .order_by(PricingRule.start_time)
        ).scalars()
    )


def create_price_rule(db: Session, payload) -> PricingRule:
    """新增一条分时电价；同一站同一 period 不允许时间重叠。"""
    overlap = db.execute(
        text(
            "SELECT COUNT(*) FROM pricing_rule "
            "WHERE station_id = :sid AND period = :p AND is_active = 1 "
            "  AND start_time < :end AND end_time > :start"
        ),
        {"sid": payload.station_id, "p": payload.period,
         "start": payload.start_time, "end": payload.end_time},
    ).scalar_one()
    if overlap:
        raise ValueError("该场站同一时段已有电价规则，请先停用旧的")

    rule = PricingRule(
        station_id=payload.station_id,
        period=payload.period,
        start_time=payload.start_time,
        end_time=payload.end_time,
        energy_price=payload.energy_price,
        service_price=payload.service_price,
        free_minutes=payload.free_minutes,
        min_charge=payload.min_charge,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        is_active=True,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def set_price_rule_active(db: Session, rule_id: int, active: bool) -> PricingRule | None:
    rule = db.get(PricingRule, rule_id)
    if rule is None:
        return None
    rule.is_active = active
    db.commit()
    db.refresh(rule)
    return rule
