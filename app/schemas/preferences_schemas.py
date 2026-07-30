from typing import Optional
from pydantic import BaseModel, Field


class SetPreferenceInput(BaseModel):
    key: str = Field(description="Preference name, e.g. 'default_payment', 'default_atta', 'shop_name'")
    value: str = Field(description="Preference value, e.g. 'upi', 'Aashirvaad 5kg', 'AnandMart'")


class GetPreferencesInput(BaseModel):
    pass


class NewChatInput(BaseModel):
    pass
