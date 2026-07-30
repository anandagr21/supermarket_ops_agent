from sqlmodel import Session, select
from app.models import OwnerPreference


class PreferencesService:
    def __init__(self, session: Session):
        self.session = session

    def set(self, chat_id: int, key: str, value: str) -> str:
        existing = self.session.exec(
            select(OwnerPreference).where(
                OwnerPreference.chat_id == chat_id,
                OwnerPreference.key == key,
            )
        ).first()
        if existing:
            existing.value = value
            self.session.add(existing)
            self.session.commit()
            return f"Updated preference: {key} = {value}."
        pref = OwnerPreference(chat_id=chat_id, key=key, value=value)
        self.session.add(pref)
        self.session.commit()
        return f"Set preference: {key} = {value}."

    def get_all(self, chat_id: int) -> dict[str, str]:
        rows = self.session.exec(
            select(OwnerPreference).where(OwnerPreference.chat_id == chat_id)
        ).all()
        return {r.key: r.value for r in rows}
