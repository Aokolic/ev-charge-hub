"""业务线2 持久层：订单查询、开单、结束计费、支付与退款。

约定：
1. 列表查询一律走 v_order_owner 视图，不直接 select 原表；
2. 写操作走 ORM，一次业务操作只 commit 一次；
3. 费用不由 Python 算，交给存储过程 sp_order_calc，保证和 SQL 脚本口径一致；
4. 开单要锁枪、校验归属并联动枪状态，不能只信任前端传的 gun_id/station_id。
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text, update
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
SELECT g.gun_id,
       g.current_status AS gun_status,
       g.pile_id,
       p.station_id,
       p.acceptance_status,
       p.current_status AS pile_status,
       s.station_status
  FROM charging_gun g
  JOIN charging_pile p ON p.pile_id = g.pile_id
  JOIN station s ON s.station_id = p.station_id
 WHERE g.gun_id = :gid
   FOR UPDATE
"""

SQL_LOCK_GUN_FOR_RELEASE = """
SELECT g.current_status AS gun_status,
       p.acceptance_status,
       p.current_status AS pile_status
  FROM charging_gun g
  JOIN charging_pile p ON p.pile_id = g.pile_id
 WHERE g.gun_id = :gid
   FOR UPDATE
"""

SQL_VEHICLE_OWNER = "SELECT user_id, fleet_id FROM vehicle WHERE vehicle_id = :vid"

SQL_SET_ORDER_GUN_VARS = """
SET @status_change_source = 'ORDER',
    @status_todo_status   = 'NONE',
    @status_change_remark = :remark
"""

SQL_UPDATE_GUN_STATUS = """
UPDATE charging_gun
   SET current_status = :st, status_updated_at = NOW()
 WHERE gun_id = :gid
"""

SQL_CLEAR_GUN_VARS = """
SET @status_change_source = NULL,
    @status_todo_status   = NULL,
    @status_change_remark = NULL
"""

SQL_LOCK_ACTIVE_RULES = """
SELECT rule_id, start_time, end_time, effective_from, effective_to
  FROM pricing_rule
 WHERE station_id = :sid
   AND is_active = 1
   FOR UPDATE
"""

_DAY_SECONDS = 24 * 3600


def _set_order_gun_status(db: Session, gun_id: int, new_status: str, remark: str) -> None:
    """透传 ORDER 来源会话变量更新充电枪状态，由触发器写入唯一一条状态日志。"""
    has_error = False
    try:
        db.execute(text(SQL_SET_ORDER_GUN_VARS), {"remark": remark})
        db.execute(text(SQL_UPDATE_GUN_STATUS), {"st": new_status, "gid": gun_id})
    except Exception:
        has_error = True
        raise
    finally:
        try:
            db.execute(text(SQL_CLEAR_GUN_VARS))
        except Exception:
            if not has_error:
                raise


def _release_gun_if_healthy(db: Session, gun_id: int, order_no: str, action: str) -> None:
    """仅当枪处于占用且所属桩已验收且非故障/离线时释放为空闲，避免触发 45000 死锁。"""
    row = db.execute(text(SQL_LOCK_GUN_FOR_RELEASE), {"gid": gun_id}).mappings().first()
    if row is None:
        return
    if (
        row["gun_status"] == "OCCUPIED"
        and row["acceptance_status"] == "PASSED"
        and row["pile_status"] not in ("FAULT", "OFFLINE")
    ):
        _set_order_gun_status(db, gun_id, "IDLE", f"{action}释放 {order_no}")


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

    顺序是：锁枪 -> 校验站/桩/枪健康态 -> 校验车辆归属 -> 插订单 -> 改枪状态（触发器记日志）。
    同一把枪已经有人在充时，uk_gun_active 会抛 1062，仍然由唯一索引兜底。
    """
    gun = db.execute(text(SQL_LOCK_GUN), {"gid": payload.gun_id}).mappings().first()
    if gun is None:
        raise ValueError("充电枪不存在")
    if int(gun["station_id"]) != payload.station_id:
        raise ValueError("该充电枪不属于指定场站")
    if gun["station_status"] != "OPEN":
        raise ValueError("场站当前非营业状态，不能开单")
    if gun["acceptance_status"] != "PASSED":
        raise ValueError("所属充电桩未通过验收，不能开单")
    if gun["pile_status"] in ("FAULT", "OFFLINE"):
        raise ValueError("所属充电桩当前处于故障或离线状态，不能开单")
    if gun["gun_status"] != "IDLE":
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
        start_time=datetime.now().replace(microsecond=0),
        start_soc=payload.start_soc,
        kwh=Decimal("0.000"),
        status="CHARGING",
    )
    db.add(order)
    try:
        db.flush()
        _set_order_gun_status(db, payload.gun_id, "OCCUPIED", f"开单占用 {order.order_no}")
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

    end_time = datetime.now().replace(microsecond=0)
    # MySQL DATETIME 无微秒，同一秒内结单会让 ck_order_time 失败，兜一个最小 1 秒时长
    if end_time <= order.start_time:
        end_time = order.start_time + timedelta(seconds=1)

    try:
        order.end_time = end_time
        order.end_soc = payload.end_soc
        order.kwh = payload.kwh
        db.flush()

        # 过程内部会重算明细，并回写 energy_fee/service_fee/total_amount/status
        db.execute(text("CALL sp_order_calc(:oid)"), {"oid": order_id})
        _release_gun_if_healthy(db, order.gun_id, order.order_no, "结单")
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return order


def pay_order(db: Session, order_id: int, payload: PaymentIn) -> PaymentRecord | None:
    """记一笔支付流水（只收正数）。

    超付、充电中不能付以及余额/授信校验都在触发器 trg_payment_guard 里，
    Python 侧加行锁并插流水；触发器会把订单的 paid_amount/status 与账户余额一起改掉。
    """
    order = db.execute(
        select(ChargeOrder).where(ChargeOrder.order_id == order_id).with_for_update()
    ).scalars().first()
    if order is None:
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
    """退款：插一条负数流水，触发器负责扣减 paid_amount 并回写状态与退回余额/授信。"""
    order = db.execute(
        select(ChargeOrder).where(ChargeOrder.order_id == order_id).with_for_update()
    ).scalars().first()
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
    order = db.execute(
        select(ChargeOrder).where(ChargeOrder.order_id == order_id).with_for_update()
    ).scalars().first()
    if order is None:
        return None
    if order.status != "CHARGING":
        raise ValueError("只有充电中的订单才能取消")
    if Decimal(order.paid_amount) != 0:
        raise ValueError("已有支付流水的订单不能取消，请走退款")

    end_time = datetime.now().replace(microsecond=0)
    if end_time <= order.start_time:
        end_time = order.start_time + timedelta(seconds=1)

    try:
        order.status = "CANCELLED"
        order.end_time = end_time
        db.flush()
        _release_gun_if_healthy(db, order.gun_id, order.order_no, "取消")
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


def _to_seconds_since_midnight(value: time | timedelta | str) -> int:
    """将 TIME 列值（timedelta/time）或 HH:MM 字符串统一转为距零点秒数。"""
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    if isinstance(value, time):
        return value.hour * 3600 + value.minute * 60 + value.second
    if isinstance(value, str):
        parts = value.strip().split(":")
        if len(parts) not in (2, 3):
            raise ValueError(f"时间格式非法: {value}")
        hh = int(parts[0])
        mm = int(parts[1])
        ss = int(parts[2]) if len(parts) == 3 else 0
        if not (
            0 <= mm < 60
            and 0 <= ss < 60
            and (0 <= hh < 24 or (hh == 24 and mm == 0 and ss == 0))
        ):
            raise ValueError(f"时间范围非法: {value}")
        return hh * 3600 + mm * 60 + ss
    raise ValueError(f"不支持的时间类型: {type(value).__name__}")


def _split_day_intervals(start_sec: int, end_sec: int) -> list[tuple[int, int]]:
    """将日内时段拆分为半开区间列表，正确处理跨零点（start > end）规则。"""
    if start_sec < end_sec:
        return [(start_sec, end_sec)]
    intervals = [(start_sec, _DAY_SECONDS)]
    if end_sec > 0:
        intervals.append((0, end_sec))
    return intervals


def _intervals_overlap(
    a_list: list[tuple[int, int]], b_list: list[tuple[int, int]]
) -> bool:
    """判断两组半开区间 [a1, a2) 与 [b1, b2) 是否存在交集。"""
    return any(a1 < b2 and b1 < a2 for a1, a2 in a_list for b1, b2 in b_list)


def _dates_overlap(
    a_from: date, a_to: date | None, b_from: date, b_to: date | None
) -> bool:
    """判断两段闭区间生效日期是否重叠（None 视为长期有效 date.max）。"""
    return a_from <= (b_to or date.max) and b_from <= (a_to or date.max)


def create_price_rule(db: Session, payload) -> PricingRule:
    """新增一条分时电价。

    同一场站不论峰/平/谷，凡与新规则在日内时段和生效日期上同时重叠的在用规则均须停用，
    防止跨时段或跨零点规则重叠导致 sp_order_calc 重复计费。
    """
    new_start_sec = _to_seconds_since_midnight(payload.start_time)
    new_end_sec = _to_seconds_since_midnight(payload.end_time)
    if new_start_sec == new_end_sec:
        raise ValueError("电价时段开始时间与结束时间不能相同")
    if payload.effective_to is not None and payload.effective_to < payload.effective_from:
        raise ValueError("失效日期不能早于生效日期")
    new_intervals = _split_day_intervals(new_start_sec, new_end_sec)

    active_rules = db.execute(
        text(SQL_LOCK_ACTIVE_RULES), {"sid": payload.station_id}
    ).mappings().all()

    deactivate_ids: list[int] = []
    for row in active_rules:
        old_intervals = _split_day_intervals(
            _to_seconds_since_midnight(row["start_time"]),
            _to_seconds_since_midnight(row["end_time"]),
        )
        if _intervals_overlap(new_intervals, old_intervals) and _dates_overlap(
            payload.effective_from,
            payload.effective_to,
            row["effective_from"],
            row["effective_to"],
        ):
            deactivate_ids.append(int(row["rule_id"]))

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
    try:
        if deactivate_ids:
            db.execute(
                update(PricingRule)
                .where(PricingRule.rule_id.in_(deactivate_ids))
                .values(is_active=0)
            )
        db.add(rule)
        db.commit()
    except Exception:
        db.rollback()
        raise
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
