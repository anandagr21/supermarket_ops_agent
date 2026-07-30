from sqlmodel import Session, select
from app.models import Bill, BillItem
from typing import Optional, List

class BillRepository:
    """Handles all database interactions for Bills and BillItems."""
    def __init__(self, session: Session):
        self.session = session
        
    def get_draft_bill(self, chat_id: int) -> Optional[Bill]:
        return self.session.exec(
            select(Bill).where(Bill.chat_id == chat_id, Bill.status == "draft")
            .order_by(Bill.id.desc())
        ).first()

    def get_or_create_draft_bill(self, chat_id: int) -> Bill:
        """Get existing draft or create one. Handles parallel tool calls by
        retrying once if a race created a duplicate draft between check and create."""
        import time
        bill = self.get_draft_bill(chat_id)
        if bill:
            return bill
        bill = Bill(chat_id=chat_id, status="draft")
        self.session.add(bill)
        try:
            self.session.commit()
            self.session.refresh(bill)
            return bill
        except Exception:
            self.session.rollback()
            # Another parallel call already created a draft — retry once
            time.sleep(0.1)
            bill = self.get_draft_bill(chat_id)
            if bill:
                return bill
            # Still none? retry create
            bill = Bill(chat_id=chat_id, status="draft")
            self.session.add(bill)
            self.session.commit()
            self.session.refresh(bill)
            return bill
        
    def get_bill_item(self, bill_id: int, product_id: int) -> Optional[BillItem]:
        return self.session.exec(
            select(BillItem).where(BillItem.bill_id == bill_id, BillItem.product_id == product_id)
        ).first()
        
    def get_all_bill_items(self, bill_id: int) -> List[BillItem]:
        return self.session.exec(
            select(BillItem).where(BillItem.bill_id == bill_id)
        ).all()
        
    def delete_bill_item(self, item: BillItem):
        self.session.delete(item)
        self.session.commit()
