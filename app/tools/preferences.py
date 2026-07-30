from sqlmodel import Session
from app.schemas.preferences_schemas import SetPreferenceInput, GetPreferencesInput, NewChatInput
from app.services.preferences_service import PreferencesService
from app.logger import setup_logger

log = setup_logger("tools.preferences")


def get_service(session: Session) -> PreferencesService:
    return PreferencesService(session)


def set_preference(session: Session, chat_id: int, args: SetPreferenceInput) -> str:
    """Save a standing preference for this store. Example keys: default_payment, default_atta, shop_name, gstin."""
    log.info(f"chat_id={chat_id} | set_preference({args.key}, {args.value})")
    service = get_service(session)
    result = service.set(chat_id, args.key, args.value)
    log.info(f"chat_id={chat_id} | set_preference → {result}")
    return result


def get_preferences(session: Session, chat_id: int, args: GetPreferencesInput) -> str:
    """Show all saved preferences for this store."""
    service = get_service(session)
    prefs = service.get_all(chat_id)
    if not prefs:
        return "No preferences have been set yet."
    lines = [f"- {k}: {v}" for k, v in prefs.items()]
    return "Current preferences:\n" + "\n".join(lines)


def new_chat(session: Session, chat_id: int, args: NewChatInput) -> str:
    """Start a fresh conversation while keeping all store data and preferences intact."""
    # Preferences and all business data stay in DB — only the chat thread is reset.
    # The caller (agent.py) handles resetting the LangGraph thread via a new thread_id.
    return "Chat has been reset. All products, bills, khata accounts, and preferences are preserved. Start fresh!"
