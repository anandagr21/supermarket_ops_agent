from typing import Optional
from sqlmodel import Session
from app.models import KhataAccount, KhataTransaction
from app.repositories.khata_repository import KhataRepository

class KhataService:
    def __init__(self, repo: KhataRepository, session: Session):
        self.repo = repo
        self.session = session # Need session for atomic commits
        
    def open_account(self, chat_id: int, customer_name: str) -> str:
        customer_name = customer_name.lower().strip()
        account = self.repo.get_account(chat_id, customer_name)
        
        if account:
            return f"Khata account for '{customer_name}' already exists. Balance: ₹{account.balance}."
            
        account = KhataAccount(chat_id=chat_id, customer_name=customer_name)
        self.session.add(account)
        self.session.commit()
        return f"Opened new Khata account for '{customer_name}'."

    def record_payment(self, chat_id: int, customer_name: str, amount: float) -> str:
        customer_name = customer_name.lower().strip()
        account = self.repo.get_account(chat_id, customer_name)
        
        if not account:
            return f"Error: Khata account for '{customer_name}' does not exist. Cannot settle."
            
        account.balance -= amount
        txn = KhataTransaction(
            account_id=account.id,
            amount=amount,
            transaction_type="payment_received",
            reference="Direct Payment"
        )
        
        self.session.add(account)
        self.session.add(txn)
        self.session.commit()
        
        return f"Recorded payment of ₹{amount} from '{customer_name}'. New Khata balance: ₹{account.balance}."

    def get_balance(self, chat_id: int, customer_name: str) -> str:
        customer_name = customer_name.lower().strip()
        account = self.repo.get_account(chat_id, customer_name)

        if not account:
            return f"No Khata account found for '{customer_name}'."

        return f"Khata balance for '{customer_name}': ₹{account.balance}."

    def add_credit(self, chat_id: int, customer_name: str, amount: float) -> str:
        """Add a purchase-on-credit entry (e.g. 'put ₹500 on Ramesh's credit')."""
        customer_name = customer_name.lower().strip()
        account = self.repo.get_account(chat_id, customer_name)

        if not account:
            # Auto-create account if it doesn't exist
            account = KhataAccount(chat_id=chat_id, customer_name=customer_name)
            self.session.add(account)
            self.session.commit()
            self.session.refresh(account)

        account.balance += amount
        txn = KhataTransaction(
            account_id=account.id,
            amount=amount,
            transaction_type="purchase_on_credit",
            reference="Manual credit entry",
        )

        self.session.add(account)
        self.session.add(txn)
        self.session.commit()

        return f"Added ₹{amount} to {customer_name}'s credit. New balance: ₹{account.balance}."
