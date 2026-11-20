"""业务线2：订单计费路由。

前缀 /api/orders。开单、支付给车主（driver）和车队（fleet）；
结束充电车主/车队/运维都能做；退款只给结算专员和管理员；
峰平谷统计只给运维和管理员。
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
    PriceRuleActiveIn,
    PriceRuleIn,
    PriceRuleOut,
    RefundIn,
)

router = APIRouter(prefix="/api/orders", tags=["业务线2-订单计费"])

_CAN_READ = require_roles(
    RoleEnum.DRIVER, RoleEnum.FLEET, RoleEnum.OPS, RoleEnum.SETTLEMENT, RoleEnum.ADMIN
)


def _check_owner(db: Session, order, user: CurrentUser, action: str) -> None:
    """driver 只能动自己的单；fleet 只能动车队内成员的单；ops 及以上不限。"""
    if user.role == RoleEnum.DRIVER:
        if order.user_id != user.user_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"只能{action}自己的订单")
    elif user.role == RoleEnum.FLEET:
        fleet_id = order_dao.get_user_fleet_id(db, order.user_id)
        if fleet_id is None or fleet_id != user.fleet_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"只能{action}本车队的订单")


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
    """开一张充电单：枪归属、枪状态、车辆归属都会校验，枪被占用时抛 1062。"""
    try:
        order = order_dao.start_order(db, user.user_id, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return OrderOut.model_validate(order)


@router.post("/{order_id}/finish", response_model=OrderDetailOut, summary="结束充电并计费")
def finish_order(
    order_id: int,
    payload: OrderFinishIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.DRIVER, RoleEnum.FLEET, RoleEnum.OPS)),
) -> OrderDetailOut:
    """结束充电，触发分时计费，并把枪状态改回空闲。"""
    order = order_dao.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    _check_owner(db, order, user, "结束")

    try:
        done = order_dao.finish_order(db, order_id, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    _, details = order_dao.get_order_detail(db, order_id)
    data = OrderOut.model_validate(done).model_dump()
    data["details"] = details
    return OrderDetailOut.model_validate(data)


@router.post(
    "/{order_id}/pay",
    response_model=PaymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="支付",
)
def pay_order(
    order_id: int,
    payload: PaymentIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.DRIVER, RoleEnum.FLEET)),
) -> PaymentOut:
    """记一笔支付流水（只收正数）。同一个 txn_no 重复提交会被唯一索引挡住。"""
    order = order_dao.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    _check_owner(db, order, user, "支付")

    record = order_dao.pay_order(db, order_id, payload)
    return PaymentOut.model_validate(record)


@router.post(
    "/{order_id}/refund",
    response_model=PaymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="退款（仅结算专员/管理员）",
)
def refund_order(
    order_id: int,
    payload: RefundIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.SETTLEMENT, RoleEnum.ADMIN)),
) -> PaymentOut:
    """退款走独立接口：金额必须为负，不限制本人订单，由结算端发起。"""
    order = order_dao.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")

    record = order_dao.refund_order(db, order_id, payload)
    return PaymentOut.model_validate(record)


@router.post("/{order_id}/cancel", response_model=OrderOut, summary="取消未结算订单")
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.DRIVER, RoleEnum.FLEET, RoleEnum.OPS)),
) -> OrderOut:
    order = order_dao.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    _check_owner(db, order, user, "取消")
    try:
        done = order_dao.cancel_order(db, order_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return OrderOut.model_validate(done)


@router.get("", response_model=PageResult[dict], summary="订单列表")
def list_orders(
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_CAN_READ),
) -> PageResult[dict]:
    """车主看自己的，车队看本车队，运维/结算/管理员看全部。"""
    if user.role == RoleEnum.DRIVER:
        result = order_dao.list_my_orders(db, user.user_id, page)
    elif user.role == RoleEnum.FLEET and user.fleet_id is not None:
        result = order_dao.list_fleet_orders(db, user.fleet_id, page)
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
    if end_date < start_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "结束日期不能早于开始日期")
    rows = order_dao.list_period_stat(db, start_date, end_date)
    return [PeriodStatOut.model_validate(r) for r in rows]


@router.get("/{order_id}/payments", response_model=list[PaymentOut], summary="订单支付流水")
def list_payments(
    order_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_CAN_READ),
) -> list[PaymentOut]:
    order = order_dao.get_order(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    if user.role in (RoleEnum.DRIVER, RoleEnum.FLEET):
        _check_owner(db, order, user, "查看")
    return [PaymentOut.model_validate(p) for p in order_dao.list_payments(db, order_id)]


@router.get("/{order_id}", response_model=OrderDetailOut, summary="订单详情")
def order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_CAN_READ),
) -> OrderDetailOut:
    order, details = order_dao.get_order_detail(db, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    if user.role in (RoleEnum.DRIVER, RoleEnum.FLEET):
        _check_owner(db, order, user, "查看")
    data = OrderOut.model_validate(order).model_dump()
    data["details"] = details
    return OrderDetailOut.model_validate(data)


# ---------------- 电价规则（运维 / 管理员） ----------------
@router.get(
    "/prices",
    response_model=list[PriceRuleOut],
    summary="查场站的分时电价",
)
def list_prices(
    station_id: int = Query(..., gt=0),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> list[PriceRuleOut]:
    return [PriceRuleOut.model_validate(x) for x in order_dao.list_price_rules(db, station_id)]


@router.post(
    "/prices",
    response_model=PriceRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增分时电价",
)
def create_price(
    payload: PriceRuleIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> PriceRuleOut:
    try:
        rule = order_dao.create_price_rule(db, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return PriceRuleOut.model_validate(rule)


@router.patch(
    "/prices/{rule_id}/active",
    response_model=PriceRuleOut,
    summary="启用 / 停用电价",
)
def set_price_active(
    rule_id: int,
    payload: PriceRuleActiveIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(RoleEnum.OPS, RoleEnum.ADMIN)),
) -> PriceRuleOut:
    rule = order_dao.set_price_rule_active(db, rule_id, payload.is_active)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "电价规则不存在")
    return PriceRuleOut.model_validate(rule)
