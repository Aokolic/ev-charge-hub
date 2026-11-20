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
SQL_BILL_ROW = "SELECT * FROM v_settle_bill_detail WHERE bill_id = :bid"


def list_bills(db: Session, page: PageParams, operator_id: int | None = None) -> dict[str, Any]:
    if operator_id:
        return fetch_view_page(
            db, SQL_BILL_OF_OPERATOR, {"oid": operator_id}, page, "bill_month DESC, bill_id DESC"
        )
    return fetch_view_page(db, SQL_BILL_DETAIL, None, page, "bill_month DESC, bill_id DESC")


def list_open_diffs(db: Session, page: PageParams) -> dict[str, Any]:
    return fetch_view_page(db, SQL_DIFF_OPEN, None, page, "created_at DESC")


def get_bill(db: Session, bill_id: int) -> SettlementBill | None:
    return db.get(SettlementBill, bill_id)


def get_bill_detail(db: Session, bill_id: int) -> dict[str, Any] | None:
    """账单详情走 v_settle_bill_detail，开票进度和未处理差异都在视图里算好了。"""
    row = db.execute(text(SQL_BILL_ROW), {"bid": bill_id}).mappings().first()
    return dict(row) if row else None


def generate_bill(db: Session, payload: BillGenerateIn) -> int:
    """调用存储过程按月生成/刷新结算单，返回影响的账单数。

    过程内部按 operator_id 过滤场站，已确认/已开票的账单不会被覆盖。
    """
    try:
        db.execute(
            text("CALL sp_settle_generate(:op, :month, :rate)"),
            {
                "op": payload.operator_id,
                "month": payload.bill_month,
                "rate": payload.platform_rate,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    cnt = db.execute(
        text(
            "SELECT COUNT(*) FROM settlement_bill "
            "WHERE operator_id = :op AND bill_month = :month"
        ),
        {"op": payload.operator_id, "month": payload.bill_month},
    ).scalar_one()
    return int(cnt)


def set_bill_status(db: Session, bill_id: int, action: str) -> SettlementBill | None:
    """账单状态流转：DRAFT -> CONFIRMED -> CLOSED。

    开票后（INVOICED）不允许再确认或关单，只能先红冲。
    """
    bill = db.get(SettlementBill, bill_id)
    if bill is None:
        return None

    if action == "confirm":
        if bill.status != "DRAFT":
            raise ValueError("只有草稿账单可以确认")
        bill.status = "CONFIRMED"
    elif action == "close":
        if bill.status not in ("CONFIRMED", "INVOICED"):
            raise ValueError("只有已确认或已开票的账单可以关单")
        bill.status = "CLOSED"
    else:
        raise ValueError("未知的状态流转")

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(bill)
    return bill


def resolve_diff(db: Session, diff_id: int, remark: str | None) -> ReconcileDiff | None:
    diff = db.get(ReconcileDiff, diff_id)
    if diff is None:
        return None
    if diff.resolved == 1:
        raise ValueError("这条差异已经处理过了")

    diff.resolved = 1
    if remark:
        diff.remark = remark
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(diff)
    return diff


def list_invoices(db: Session, bill_id: int) -> list[InvoiceRecord]:
    return list(
        db.execute(
            select(InvoiceRecord)
            .where(InvoiceRecord.bill_id == bill_id)
            .order_by(InvoiceRecord.invoice_id)
        ).scalars().all()
    )


def issue_invoice(db: Session, bill_id: int, payload: InvoiceIn) -> InvoiceRecord | None:
    """开票。金额上限、账单状态由触发器 trg_invoice_guard 校验。"""
    bill = db.get(SettlementBill, bill_id)
    if bill is None:
        return None
    if bill.status == "CLOSED":
        raise ValueError("账单已关单，不能再开票")

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

    原票要加 FOR UPDATE 锁，否则两笔并发红冲都能通过校验，把已开票额冲成负数。
    """
    origin = db.execute(
        select(InvoiceRecord).where(InvoiceRecord.invoice_id == invoice_id).with_for_update()
    ).scalars().first()
    if origin is None:
        return None
    if origin.status != "ISSUED":
        raise ValueError("这张发票已经红冲过了")
    if origin.red_flush_of is not None:
        raise ValueError("红冲票不能再红冲")

    flush_amount = amount if amount is not None else -Decimal(origin.amount)
    if flush_amount < 0 and abs(flush_amount) > Decimal(origin.amount):
        raise ValueError("红冲金额不能超过原票金额")

    flush = InvoiceRecord(
        bill_id=origin.bill_id,
        invoice_no=invoice_no,
        amount=flush_amount,
        invoice_type=origin.invoice_type,
        status="ISSUED",
        red_flush_of=invoice_id,
        issued_at=datetime.now(),
    )
    try:
        db.add(flush)
        db.flush()

        origin.status = "RED_FLUSHED"
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(flush)
    return flush
