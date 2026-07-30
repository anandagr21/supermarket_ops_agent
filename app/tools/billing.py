from sqlmodel import Session, select
from app.schemas.billing_schemas import (
    AddToBillInput, FinalizeBillInput, QuerySalesInput,
    RemoveFromBillInput, UpdateBillItemInput, ViewDraftBillInput,
    GenerateInvoiceInput, GenerateAnalysisInput,
)
from app.services.billing_service import BillingService
from app.services.invoice_service import InvoiceService
from app.services.analysis_service import AnalysisService
from app.models import Bill
from app.logger import setup_logger

log = setup_logger("tools.billing")


def get_billing_service(session: Session) -> BillingService:
    return BillingService(session)

# --- Tools ---

def add_to_bill(session: Session, chat_id: int, args: AddToBillInput) -> str:
    """Add an item to the current draft bill. Creates a draft bill if one doesn't exist."""
    log.info(f"chat_id={chat_id} | add_to_bill({args.product_name}, qty={args.quantity})")
    service = get_billing_service(session)
    result = service.add_to_bill(chat_id, args.product_name, args.quantity)
    log.info(f"chat_id={chat_id} | add_to_bill → {result}")
    return result

def remove_from_bill(session: Session, chat_id: int, args: RemoveFromBillInput) -> str:
    """Remove an item from the current draft bill."""
    log.info(f"chat_id={chat_id} | remove_from_bill({args.product_name})")
    service = get_billing_service(session)
    result = service.remove_from_bill(chat_id, args.product_name)
    log.info(f"chat_id={chat_id} | remove_from_bill → {result}")
    return result

def update_bill_item(session: Session, chat_id: int, args: UpdateBillItemInput) -> str:
    """Change the quantity of an existing item in the draft bill."""
    log.info(f"chat_id={chat_id} | update_bill_item({args.product_name}, qty={args.quantity})")
    service = get_billing_service(session)
    result = service.update_bill_item(chat_id, args.product_name, args.quantity)
    log.info(f"chat_id={chat_id} | update_bill_item → {result}")
    return result

def view_draft_bill(session: Session, chat_id: int, args: ViewDraftBillInput) -> str:
    """Show all items currently in the draft bill."""
    service = get_billing_service(session)
    result = service.view_draft_bill(chat_id)
    log.info(f"chat_id={chat_id} | view_draft_bill → {result[:120]}")
    return result

def finalize_bill(session: Session, chat_id: int, args: FinalizeBillInput) -> str:
    """Finalize the draft bill, decrement stock, and calculate taxes."""
    log.info(f"chat_id={chat_id} | finalize_bill({args.payment_mode}, khata={args.khata_customer_name})")
    service = get_billing_service(session)
    result = service.finalize_bill(chat_id, args.payment_mode, args.khata_customer_name)
    log.info(f"chat_id={chat_id} | finalize_bill → {result}")
    return result

def query_todays_sales(session: Session, chat_id: int, args: QuerySalesInput) -> str:
    """Check the total revenue and sales for today."""
    log.info(f"chat_id={chat_id} | query_todays_sales")
    service = get_billing_service(session)
    result = service.query_todays_sales(chat_id)
    log.info(f"chat_id={chat_id} | query_todays_sales → {result[:150]}")
    return result

def generate_invoice(session: Session, chat_id: int, args: GenerateInvoiceInput) -> str:
    """Generate a GST-compliant PDF invoice for a finalized bill and send it via Telegram."""
    bill_id = args.bill_id
    if bill_id is None:
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

    log.info(f"chat_id={chat_id} | generate_invoice(bill_id={bill_id})")
    invoice_service = InvoiceService(session)
    path = invoice_service.generate(bill_id)

    import app.main as main_module
    main_module.PENDING_FILES[chat_id] = path

    result = f"Invoice INV-{bill_id:04d} generated as PDF and sent."
    log.info(f"chat_id={chat_id} | generate_invoice → {result}")
    return result


def generate_analysis_deck(session: Session, chat_id: int, args: GenerateAnalysisInput) -> str:
    """Generate a PowerPoint analysis deck with charts — sales, top items, stock health, GST."""
    log.info(f"chat_id={chat_id} | generate_analysis_deck")
    service = AnalysisService(session)
    path = service.generate(chat_id)

    import app.main as main_module
    main_module.PENDING_FILES[chat_id] = path

    result = f"Sales analysis deck generated and sent."
    log.info(f"chat_id={chat_id} | generate_analysis_deck → {result}")
    return result
