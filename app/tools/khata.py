from sqlmodel import Session
from app.schemas.khata_schemas import OpenKhataInput, RecordKhataPaymentInput, GetKhataBalanceInput
from app.repositories.khata_repository import KhataRepository
from app.services.khata_service import KhataService

def get_khata_service(session: Session) -> KhataService:
    repo = KhataRepository(session)
    return KhataService(repo, session)

# --- Tools ---

def open_khata_account(session: Session, chat_id: int, args: OpenKhataInput) -> str:
    """Create a new Khata (credit) account for a customer."""
    service = get_khata_service(session)
    return service.open_account(chat_id, args.customer_name)

def record_khata_payment(session: Session, chat_id: int, args: RecordKhataPaymentInput) -> str:
    """Record a payment received from a customer towards their Khata balance."""
    service = get_khata_service(session)
    return service.record_payment(chat_id, args.customer_name, args.amount)

def get_khata_balance(session: Session, chat_id: int, args: GetKhataBalanceInput) -> str:
    """Get the current Khata balance for a customer."""
    service = get_khata_service(session)
    return service.get_balance(chat_id, args.customer_name)
