"""业务线5：结算对账路由。

前缀 /api/settlements，整组接口只给结算专员和管理员。
账单本身由存储过程生成，这里只负责触发、查询、状态流转和开票。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, RoleEnum, require_roles
from app.dao import settle as settle_dao
from app.db import get_db
from app.schemas.common import PageParams, PageResult
from app.schemas.settle import (
    BillDetailOut,
    BillGenerateIn,
    BillGenerateOut,
    BillOut,
    DiffOut,
    DiffResolveIn,
    InvoiceIn,
    InvoiceOut,
    RedFlushIn,
)

router = APIRouter(prefix="/api/settlements", tags=["业务线5-结算对账"])

_settle_only = require_roles(RoleEnum.SETTLEMENT, RoleEnum.ADMIN)


@router.post(
    "/generate",
    response_model=BillGenerateOut,
    status_code=status.HTTP_201_CREATED,
    summary="按月生成结算单",
)
def generate_bill(
    payload: BillGenerateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> BillGenerateOut:
    """调存储过程按月汇总：只算该运营商的场站，已确认/已开票的账单不会被覆盖。"""
    affected = settle_dao.generate_bill(db, payload)
    return BillGenerateOut(
        success=True,
        message=f"{payload.bill_month} 结算单已生成/刷新",
        affected_bills=affected,
    )


@router.get("", response_model=PageResult[dict], summary="账单列表")
def list_bills(
    page: PageParams = Depends(),
    operator_id: int | None = Query(default=None, description="按运营商过滤"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> PageResult[dict]:
    return PageResult[dict](**settle_dao.list_bills(db, page, operator_id))


@router.get("/diffs", response_model=PageResult[dict], summary="未处理差异")
def list_diffs(
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> PageResult[dict]:
    return PageResult[dict](**settle_dao.list_open_diffs(db, page))


@router.post("/diffs/{diff_id}/resolve", response_model=DiffOut, summary="处理差异")
def resolve_diff(
    diff_id: int,
    payload: DiffResolveIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> DiffOut:
    try:
        diff = settle_dao.resolve_diff(db, diff_id, payload.remark)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if diff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "差异记录不存在")
    return DiffOut.model_validate(diff)


@router.get("/{bill_id}", response_model=BillDetailOut, summary="账单详情")
def bill_detail(
    bill_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> BillDetailOut:
    row = settle_dao.get_bill_detail(db, bill_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账单不存在")
    return BillDetailOut.model_validate(row)


@router.post("/{bill_id}/confirm", response_model=BillOut, summary="确认账单")
def confirm_bill(
    bill_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> BillOut:
    try:
        bill = settle_dao.set_bill_status(db, bill_id, "confirm")
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if bill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账单不存在")
    return BillOut.model_validate(bill)


@router.post("/{bill_id}/close", response_model=BillOut, summary="关单")
def close_bill(
    bill_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> BillOut:
    try:
        bill = settle_dao.set_bill_status(db, bill_id, "close")
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if bill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账单不存在")
    return BillOut.model_validate(bill)


@router.get("/{bill_id}/invoices", response_model=list[InvoiceOut], summary="账单发票列表")
def list_invoices(
    bill_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> list[InvoiceOut]:
    return [InvoiceOut.model_validate(i) for i in settle_dao.list_invoices(db, bill_id)]


@router.post(
    "/{bill_id}/invoices",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="开票",
)
def issue_invoice(
    bill_id: int,
    payload: InvoiceIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> InvoiceOut:
    """开票金额上限、账单状态由触发器 trg_invoice_guard 兜底，超了会 1644。"""
    try:
        invoice = settle_dao.issue_invoice(db, bill_id, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账单不存在")
    return InvoiceOut.model_validate(invoice)


@router.post(
    "/invoices/{invoice_id}/red-flush",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="红冲发票",
)
def red_flush_invoice(
    invoice_id: int,
    payload: RedFlushIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_settle_only),
) -> InvoiceOut:
    """红冲不删原票：新开一张负数票指向它，原票状态改成 RED_FLUSHED。"""
    try:
        flush = settle_dao.red_flush_invoice(
            db, invoice_id, payload.invoice_no, payload.amount
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if flush is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "发票不存在")
    return InvoiceOut.model_validate(flush)
