"""业务线5：结算对账路由。

前缀 /api/settlements。账单、对账、开票都只给结算专员（settlement）和管理员（admin），
运营商自己查账单需要平台代查，所以这里不放开给 fleet。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, RoleEnum, require_roles
from app.dao import settle as settle_dao
from app.db import get_db
from app.schemas.common import PageParams, PageResult
from app.schemas.settle import (
    BillDetailOut,
    BillGenerateIn,
    DiffOut,
    DiffResolveIn,
    InvoiceIn,
    InvoiceOut,
    RedFlushIn,
)

router = APIRouter(prefix="/api/settlements", tags=["业务线5-结算对账"])

_ONLY_SETTLE = require_roles(RoleEnum.SETTLEMENT, RoleEnum.ADMIN)


@router.post("/generate", summary="按月生成结算单")
def generate(
    payload: BillGenerateIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_SETTLE),
) -> dict[str, object]:
    """调存储过程按月汇总，重复调用会刷新同一批账单而不是新增。"""
    count = settle_dao.generate_bill(db, payload)
    return {"success": True, "message": f"{payload.bill_month} 共生成/刷新 {count} 张结算单",
            "id": count}


@router.get("", response_model=PageResult[dict], summary="账单列表")
def list_bills(
    page: PageParams = Depends(),
    operator_id: int | None = Query(default=None, gt=0, description="按运营商筛选"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_SETTLE),
) -> PageResult[dict]:
    result = settle_dao.list_bills(db, page, operator_id)
    return PageResult[dict](**result)


@router.get("/diffs", response_model=PageResult[dict], summary="未处理的对账差异")
def list_diffs(
    page: PageParams = Depends(),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_SETTLE),
) -> PageResult[dict]:
    result = settle_dao.list_open_diffs(db, page)
    return PageResult[dict](**result)


@router.post("/diffs/{diff_id}/resolve", response_model=DiffOut, summary="标记差异已处理")
def resolve_diff(
    diff_id: int,
    payload: DiffResolveIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_SETTLE),
) -> DiffOut:
    diff = settle_dao.resolve_diff(db, diff_id, payload.remark)
    if diff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "差异记录不存在")
    return DiffOut.model_validate(diff)


@router.post("/{bill_id}/invoices", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED,
             summary="开票")
def issue_invoice(
    bill_id: int,
    payload: InvoiceIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_SETTLE),
) -> InvoiceOut:
    """开一张发票。累计开票额超过应付时触发器会拒掉，接口返回 400。"""
    invoice = settle_dao.issue_invoice(db, bill_id, payload)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账单不存在")
    return InvoiceOut.model_validate(invoice)


@router.post("/invoices/{invoice_id}/red-flush", response_model=InvoiceOut, summary="红冲发票")
def red_flush(
    invoice_id: int,
    payload: RedFlushIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_SETTLE),
) -> InvoiceOut:
    """红冲不删原票，另开一张负数票；原票标记为 RED_FLUSHED。"""
    try:
        flush = settle_dao.red_flush_invoice(db, invoice_id, payload.invoice_no, payload.amount)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if flush is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "发票不存在")
    return InvoiceOut.model_validate(flush)


@router.get("/{bill_id}", response_model=BillDetailOut, summary="账单详情")
def bill_detail(
    bill_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(_ONLY_SETTLE),
) -> BillDetailOut:
    bill = settle_dao.get_bill(db, bill_id)
    if bill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账单不存在")

    rows = db.execute(
        text("SELECT * FROM v_settle_bill_detail WHERE bill_id = :bid"),
        {"bid": bill_id},
    ).mappings().first()
    return BillDetailOut.model_validate(dict(rows))
