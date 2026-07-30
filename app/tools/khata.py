from sqlmodel import Session
from app.schemas.khata_schemas import OpenKhataInput, RecordKhataPaymentInput, GetKhataBalanceInput
from app.repositories.khata_repository import KhataRepository
from app.services.khata_service import KhataService
from app.logger import setup_logger

log = setup_logger("tools.khata")


def get_khata_service(session: Session) -> KhataService:
    repo = KhataRepository(session)
    return KhataService(repo, session)

# --- Tools ---

def open_khata_account(session: Session, chat_id: int, args: OpenKhataInput) -> str:
    """Create a new Khata (credit) account for a customer."""
    log.info(f"chat_id={chat_id} | open_khata_account({args.customer_name})")
    service = get_khata_service(session)
    result = service.open_account(chat_id, args.customer_name)
    log.info(f"chat_id={chat_id} | open_khata_account → {result}")
    return result

def record_khata_payment(session: Session, chat_id: int, args: RecordKhataPaymentInput) -> str:
    """Record a payment received from a customer towards their Khata balance."""
    log.info(f"chat_id={chat_id} | record_khata_payment({args.customer_name}, ₹{args.amount})")
    service = get_khata_service(session)
    result = service.record_payment(chat_id, args.customer_name, args.amount)
    log.info(f"chat_id={chat_id} | record_khata_payment → {result}")
    return result

def get_khata_balance(session: Session, chat_id: int, args: GetKhataBalanceInput) -> str:
    """Get the current Khata balance for a customer."""
    log.info(f"chat_id={chat_id} | get_khata_balance({args.customer_name})")
    service = get_khata_service(session)
    result = service.get_balance(chat_id, args.customer_name)
    log.info(f"chat_id={chat_id} | get_khata_balance → {result}")
    return result
