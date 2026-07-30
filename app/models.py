import uuid
from typing import List, Optional
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship


def _uuid() -> str:
    return str(uuid.uuid4())

# --- Idempotency (Telegram retry protection) ---
class ProcessedUpdate(SQLModel, table=True):
    update_id: int = Field(primary_key=True)
    reply: str
    processed_at: datetime = Field(default_factory=datetime.utcnow)

# --- Preferences ---
class OwnerPreference(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: int = Field(index=True) # Telegram Chat ID (Isolates stores for different reviewers)
    key: str
    value: str

# --- Products (Inventory) ---
class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: int = Field(index=True)
    name: str = Field(index=True) # Removed unique=True globally, should be unique per chat_id (handled in code)
    unit: str # kg, g, litre, ml, packet, dozen, piece
    gst_slab_percent: float # 0, 5, 12, 18
    hsn_code: Optional[str] = None
    cost_price: float
    mrp: float
    stock_quantity: float = Field(default=0.0)
    reorder_level: float = Field(default=0.0)

# --- Khata (Credit Ledger) ---
class KhataAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: int = Field(index=True)
    customer_name: str = Field(index=True)
    # Positive balance means the customer owes the store money.
    balance: float = Field(default=0.0)
    
    transactions: List["KhataTransaction"] = Relationship(back_populates="account")

class KhataTransaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="khataaccount.id")
    amount: float
    transaction_type: str # "purchase_on_credit", "payment_received"
    reference: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    account: KhataAccount = Relationship(back_populates="transactions")

# --- Billing ---
class Bill(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=_uuid, index=True, unique=True)  # public-facing ID
    chat_id: int = Field(index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    total_amount: float = Field(default=0.0)
    total_tax: float = Field(default=0.0)
    total_cgst: float = Field(default=0.0)
    total_sgst: float = Field(default=0.0)
    payment_mode: Optional[str] = None # cash, upi, khata
    customer_name: Optional[str] = None # optional name on cash/upi bills
    status: str = Field(default="draft") # draft, finalized
    
    items: List["BillItem"] = Relationship(back_populates="bill", sa_relationship_kwargs={"cascade": "all, delete"})

class BillItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bill_id: int = Field(foreign_key="bill.id", ondelete="CASCADE")
    product_id: int = Field(foreign_key="product.id")
    quantity: float
    unit_price: float # price per unit at the time of sale
    gst_amount: float
    cgst_amount: float = Field(default=0.0)
    sgst_amount: float = Field(default=0.0)
    total_price: float
    
    bill: Bill = Relationship(back_populates="items")
