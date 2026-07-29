from sqlmodel import Session, select
from app.schemas.billing_schemas import (
    AddToBillInput, FinalizeBillInput, QuerySalesInput,
    RemoveFromBillInput, UpdateBillItemInput, ViewDraftBillInput,
    GenerateInvoiceInput,
)
from app.services.billing_service import BillingService
from app.services.invoice_service import InvoiceService
from app.models import Bill

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

def remove_from_bill(session: Session, chat_id: int, args: RemoveFromBillInput) -> str:
    """Remove an item from the current draft bill."""
    service = get_billing_service(session)
    return service.remove_from_bill(chat_id, args.product_name)

def update_bill_item(session: Session, chat_id: int, args: UpdateBillItemInput) -> str:
    """Change the quantity of an existing item in the draft bill."""
    service = get_billing_service(session)
    return service.update_bill_item(chat_id, args.product_name, args.quantity)

def view_draft_bill(session: Session, chat_id: int, args: ViewDraftBillInput) -> str:
    """Show all items currently in the draft bill."""
    service = get_billing_service(session)
    return service.view_draft_bill(chat_id)

def generate_invoice(session: Session, chat_id: int, args: GenerateInvoiceInput) -> str:
    """Generate a GST-compliant PDF invoice for a finalized bill and send it via Telegram."""
    bill_id = args.bill_id
    if bill_id is None:
        # Find the most recent finalized bill for this chat
        bill = session.exec(
            select(Bill)
            .where(Bill.chat_id == chat_id, Bill.status == "finalized")
            .order_by(Bill.timestamp.desc())
        ).first()
        if not bill:
            return "No finalized bill found to generate an invoice."
        bill_id = bill.id
    else:
        bill = session.get(Bill, bill_id)
        if not bill or bill.status != "finalized":
            return f"Bill #{bill_id} not found or not finalized."

    invoice_service = InvoiceService(session)
    path = invoice_service.generate(bill_id)

    # Send the PDF via Telegram
    import os
    import httpx
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if bot_token:
        url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
        with open(path, "rb") as f:
            files = {"document": (os.path.basename(path), f, "application/pdf")}
            data = {"chat_id": chat_id}
            resp = httpx.post(url, data=data, files=files, timeout=30.0)
            if resp.status_code == 200:
                return f"Invoice INV-{bill_id:04d} has been sent as a PDF."
            else:
                return f"Invoice saved at {path}, but Telegram upload failed: {resp.text}"

    return f"Invoice saved at {path}."
