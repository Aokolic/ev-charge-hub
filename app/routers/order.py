"""业务线2：订单计费路由。

前缀 /api/orders。开单/支付给车主（driver）和车队（fleet），
结束充电车主和运维（ops）都能做，峰平谷统计只给运维和管理员。
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, RoleEnum, require_roles
from app.dao import order as order_dao
from app.db import get_db
from app.schemas.common import PageParams, PageResult
from app.schemas.order import (
    OrderDetailOut,
    OrderFinishIn,
    OrderOut,
    OrderStartIn,
    PaymentIn,
    PaymentOut,
    PeriodStatOut,
)

router = APIRouter(prefix="/api/orders", tags=["业务线2-订单计费"])


@router.post(
    "/start",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="扫码开单",
)
def start_order(
    payload: OrderStartIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.DRIVER, RoleEnum.FLEET)),
) -> OrderOut:
    """开一张充电单。枪被占用时数据库抛 1062，main.py 统一翻成 409。"""
    order = order_dao.start_order(db, user.user_id, payload)
    return OrderOut.model_validate(order)


@router.post("/{order_id}/finish", response_model=OrderDetailOut, summary="结束充电并计费")
def finish_order(
    order_id: int,
    payload: OrderFinishIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.DRIVER, RoleEnum.OPS)),
) -> OrderDetailOut:
    """结束充电，触发分时计费。车主只能结束自己的单。"""
    order = order_dao.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    if user.role == RoleEnum.DRIVER and order.user_id != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能结束自己的订单")

    try:
        done = order_dao.finish_order(db, order_id, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    _, details = order_dao.get_order_detail(db, order_id)
    data = OrderOut.model_validate(done).model_dump()
    data["details"] = details
    return OrderDetailOut.model_validate(data)


@router.post("/{order_id}/pay", response_model=PaymentOut, summary="支付或退款")
def pay_order(
    order_id: int,
    payload: PaymentIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.DRIVER, RoleEnum.FLEET)),
) -> PaymentOut:
    """记一笔支付流水。同一个 txn_no 重复提交会被唯一索引挡住。"""
    order = order_dao.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    if order.user_id != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能支付自己的订单")

    record = order_dao.pay_order(db, order_id, payload)
    return PaymentOut.model_validate(record)


@router.get("", response_model=PageResult[dict], summary="订单列表")
def list_orders(
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(
        require_roles(
            RoleEnum.DRIVER,
            RoleEnum.FLEET,
            RoleEnum.OPS,
            RoleEnum.SETTLEMENT,
            RoleEnum.ADMIN,
        )
    ),
) -> PageResult[dict]:
    """车主只看自己的；运维、结算、管理员看全部。"""
    if user.role in (RoleEnum.DRIVER, RoleEnum.FLEET):
        result = order_dao.list_my_orders(db, user.user_id, page)
    else:
        result = order_dao.list_all_orders(db, page)
    return PageResult[dict](**result)


@router.get("/stats/period", response_model=list[PeriodStatOut], summary="峰平谷电量与均价")
def period_stat(
    start_date: date = Query(description="起始日期"),
    end_date: date = Query(description="结束日期"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> list[PeriodStatOut]:
    """按天给出峰平谷电量、电费、服务费和平均单价，用来核对分时规则。"""
    rows = order_dao.list_period_stat(db, start_date, end_date)
    return [PeriodStatOut.model_validate(r) for r in rows]


@router.get("/{order_id}", response_model=OrderDetailOut, summary="订单详情")
def order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(
        require_roles(
            RoleEnum.DRIVER,
            RoleEnum.FLEET,
            RoleEnum.OPS,
            RoleEnum.SETTLEMENT,
            RoleEnum.ADMIN,
        )
    ),
) -> OrderDetailOut:
    order, details = order_dao.get_order_detail(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    if user.role in (RoleEnum.DRIVER, RoleEnum.FLEET) and order.user_id != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能查看自己的订单")
    data = OrderOut.model_validate(order).model_dump()
    data["details"] = details
    return OrderDetailOut.model_validate(data)
