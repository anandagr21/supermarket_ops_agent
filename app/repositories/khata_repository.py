from sqlmodel import Session, select
from app.models import KhataAccount, KhataTransaction
from typing import Optional

class KhataRepository:
    """Handles all database interactions for Khata accounts and transactions."""
    def __init__(self, session: Session):
        self.session = session
        
    def get_account(self, chat_id: int, customer_name: str) -> Optional[KhataAccount]:
        return self.session.exec(
            select(KhataAccount).where(KhataAccount.chat_id == chat_id, KhataAccount.customer_name == customer_name)
        ).first()

    def get_all(self, chat_id: int) -> list[KhataAccount]:
        return list(self.session.exec(
            select(KhataAccount).where(KhataAccount.chat_id == chat_id)
        ).all())
