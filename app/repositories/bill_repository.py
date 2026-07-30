from sqlmodel import Session, select
from app.models import Bill, BillItem
from typing import Optional, List

class BillRepository:
    """Handles all database interactions for Bills and BillItems."""
    def __init__(self, session: Session):
        self.session = session
        
    def get_draft_bill(self, chat_id: int) -> Optional[Bill]:
        # Return the most recent draft (in case parallel tool calls created multiple)
        drafts = self.session.exec(
            select(Bill).where(Bill.chat_id == chat_id, Bill.status == "draft")
            .order_by(Bill.id.desc())
        ).all()
        if not drafts:
            return None
        # Clean up stale duplicates
        if len(drafts) > 1:
            for extra in drafts[1:]:
                self.session.delete(extra)
            self.session.commit()
        return drafts[0]
        
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
