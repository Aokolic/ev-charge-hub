"""业务线5 持久层：账单生成、列表、对账差异、开票与红冲。

账单数据由存储过程 sp_settle_generate 汇总生成，Python 侧不重复算一遍，
免得“代码里的口径”和“SQL 脚本里的口径”对不上。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.dao.common import fetch_view_page
from app.models.settle import InvoiceRecord, ReconcileDiff, SettlementBill
from app.schemas.common import PageParams
from app.schemas.settle import BillGenerateIn, InvoiceIn

SQL_BILL_DETAIL = "SELECT * FROM v_settle_bill_detail WHERE 1 = 1"
SQL_BILL_OF_OPERATOR = "SELECT * FROM v_settle_bill_detail WHERE operator_id = :oid"
SQL_DIFF_OPEN = "SELECT * FROM v_settle_diff_open WHERE 1 = 1"


def list_bills(db: Session, page: PageParams, operator_id: int | None = None) -> dict[str, Any]:
    if operator_id:
        return fetch_view_page(
            db, SQL_BILL_OF_OPERATOR, {"oid": operator_id}, page, "bill_month DESC"
        )
    return fetch_view_page(db, SQL_BILL_DETAIL, None, page, "bill_month DESC")


def list_open_diffs(db: Session, page: PageParams) -> dict[str, Any]:
    return fetch_view_page(db, SQL_DIFF_OPEN, None, page, "created_at DESC")


def get_bill(db: Session, bill_id: int) -> SettlementBill | None:
    return db.get(SettlementBill, bill_id)


def generate_bill(db: Session, payload: BillGenerateIn) -> int:
    """调用存储过程按月生成/刷新结算单，返回影响的账单数。"""
    db.execute(
        text("CALL sp_settle_generate(:op, :month, :rate)"),
        {
            "op": payload.operator_id,
            "month": payload.bill_month,
            "rate": payload.platform_rate,
        },
    )
    db.commit()
    cnt = db.execute(
        text(
            "SELECT COUNT(*) FROM settlement_bill "
            "WHERE operator_id = :op AND bill_month = :month"
        ),
        {"op": payload.operator_id, "month": payload.bill_month},
    ).scalar_one()
    return int(cnt)


def resolve_diff(db: Session, diff_id: int, remark: str | None) -> ReconcileDiff | None:
    diff = db.get(ReconcileDiff, diff_id)
    if diff is None:
        return None
    diff.resolved = 1
    if remark:
        diff.remark = remark
    db.commit()
    db.refresh(diff)
    return diff


def issue_invoice(db: Session, bill_id: int, payload: InvoiceIn) -> InvoiceRecord | None:
    """开票。金额上限由触发器 trg_invoice_guard 校验。"""
    bill = db.get(SettlementBill, bill_id)
    if bill is None:
        return None

    invoice = InvoiceRecord(
        bill_id=bill_id,
        invoice_no=payload.invoice_no,
        amount=payload.amount,
        invoice_type=payload.invoice_type,
        status="ISSUED",
        issued_at=datetime.now(),
    )
    db.add(invoice)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(invoice)
    return invoice


def red_flush_invoice(
    db: Session, invoice_id: int, invoice_no: str, amount: Decimal | None
) -> InvoiceRecord | None:
    """红冲：开一张负数票指向原票，再把原票状态改成 RED_FLUSHED。

    触发器里不能改自己这张表（MySQL 限制），所以原票状态在这里补一条 UPDATE。
    """
    origin = db.get(InvoiceRecord, invoice_id)
    if origin is None:
        return None
    if origin.status != "ISSUED":
        raise ValueError("这张发票已经红冲过了")

    flush = InvoiceRecord(
        bill_id=origin.bill_id,
        invoice_no=invoice_no,
        amount=amount if amount is not None else -origin.amount,
        invoice_type=origin.invoice_type,
        status="ISSUED",
        red_flush_of=invoice_id,
        issued_at=datetime.now(),
    )
    db.add(flush)
    db.flush()

    origin.status = "RED_FLUSHED"
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(flush)
    return flush
