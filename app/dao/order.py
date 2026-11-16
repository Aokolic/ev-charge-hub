"""业务线2 持久层：订单查询、开单、结束计费、支付。

约定：
1. 列表查询一律走 v_order_owner 视图，不直接 select 原表；
2. 写操作走 ORM，一次业务操作只 commit 一次；
3. 费用不由 Python 算，交给存储过程 sp_order_calc，保证和 SQL 脚本口径一致。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.dao.common import fetch_view_page
from app.models.order import ChargeOrder, OrderFeeDetail, PaymentRecord
from app.schemas.common import PageParams
from app.schemas.order import OrderFinishIn, OrderStartIn, PaymentIn

# 视图查询语句写死在常量里，fetch_view_page 只接受这类受控 SQL
SQL_ORDER_MINE = "SELECT * FROM v_order_owner WHERE user_id = :uid"
SQL_ORDER_ALL = "SELECT * FROM v_order_owner WHERE 1 = 1"
SQL_ORDER_PERIOD_STAT = """
SELECT period, stat_date, order_cnt, total_kwh, energy_fee, service_fee, avg_price
FROM v_order_period_stat
WHERE stat_date BETWEEN :start_date AND :end_date
ORDER BY stat_date, period
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


def list_my_orders(db: Session, user_id: int, page: PageParams) -> dict[str, Any]:
    return fetch_view_page(db, SQL_ORDER_MINE, {"uid": user_id}, page, "start_time DESC")


def list_all_orders(db: Session, page: PageParams) -> dict[str, Any]:
    return fetch_view_page(db, SQL_ORDER_ALL, None, page, "start_time DESC")


def start_order(db: Session, user_id: int, payload: OrderStartIn) -> ChargeOrder:
    """开单。

    同一把枪已经有人在充时，uk_gun_active 会抛 1062。
    这里不去查“枪占没占着”，交给唯一索引兜底——先查再写会有并发窗口。
    """
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
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return order


def finish_order(db: Session, order_id: int, payload: OrderFinishIn) -> ChargeOrder | None:
    """结束充电并计费：先落 end_time/kwh，再调存储过程拆峰平谷。"""
    order = db.get(ChargeOrder, order_id)
    if order is None:
        return None
    if order.status != "CHARGING":
        raise ValueError("订单不在充电中，不能结束")

    order.end_time = datetime.now()
    order.end_soc = payload.end_soc
    order.kwh = payload.kwh
    db.flush()

    # 过程内部会重算明细，并回写 energy_fee/service_fee/total_amount/status
    db.execute(text("CALL sp_order_calc(:oid)"), {"oid": order_id})
    db.commit()
    db.refresh(order)
    return order


def pay_order(db: Session, order_id: int, payload: PaymentIn) -> PaymentRecord | None:
    """记一笔支付。

    超付、退款超过已付、充电中不能付，这些判断都在触发器 trg_payment_guard 里，
    Python 侧只管插流水；触发器会把订单的 paid_amount/status 一起改掉。
    """
    if db.get(ChargeOrder, order_id) is None:
        return None

    record = PaymentRecord(
        order_id=order_id,
        amount=payload.amount,
        method=payload.method,
        txn_no=payload.txn_no,
        status="SUCCESS" if payload.amount > 0 else "REFUNDED",
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


def list_period_stat(db: Session, start_date, end_date) -> list[dict[str, Any]]:
    rows = db.execute(
        text(SQL_ORDER_PERIOD_STAT),
        {"start_date": start_date, "end_date": end_date},
    ).mappings().all()
    return [dict(r) for r in rows]
