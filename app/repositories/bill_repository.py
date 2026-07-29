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
        ).first()
        
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
