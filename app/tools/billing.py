from sqlmodel import Session
from app.schemas.billing_schemas import AddToBillInput, FinalizeBillInput, QuerySalesInput
from app.services.billing_service import BillingService

def get_billing_service(session: Session) -> BillingService:
    return BillingService(session)

# --- Tools ---

def add_to_bill(session: Session, chat_id: int, args: AddToBillInput) -> str:
    """Add an item to the current draft bill. Creates a draft bill if one doesn't exist."""
    service = get_billing_service(session)
    return service.add_to_bill(chat_id, args.product_name, args.quantity)

def finalize_bill(session: Session, chat_id: int, args: FinalizeBillInput) -> str:
    """Finalize the draft bill, decrement stock, and calculate taxes."""
    service = get_billing_service(session)
    return service.finalize_bill(chat_id, args.payment_mode, args.khata_customer_name)

def query_todays_sales(session: Session, chat_id: int, args: QuerySalesInput) -> str:
    """Check the total revenue and sales for today."""
    service = get_billing_service(session)
    return service.query_todays_sales(chat_id)
