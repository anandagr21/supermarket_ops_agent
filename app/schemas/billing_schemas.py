from typing import Optional, Literal
from pydantic import BaseModel, Field

class AddToBillInput(BaseModel):
    product_name: str = Field(description="The exact name of the product to add.")
    quantity: float = Field(gt=0, description="The amount to add (must be > 0).")

class FinalizeBillInput(BaseModel):
    payment_mode: Literal["cash", "upi", "card", "khata"] = Field(description="The method of payment.")
    khata_customer_name: Optional[str] = Field(default=None, description="If payment_mode is khata, provide the customer name.")

class QuerySalesInput(BaseModel):
    pass

class RemoveFromBillInput(BaseModel):
    product_name: str = Field(description="The exact name of the product to remove from the draft bill.")

class UpdateBillItemInput(BaseModel):
    product_name: str = Field(description="The exact name of the product to update.")
    quantity: float = Field(gt=0, description="The new quantity (must be > 0).")

class ViewDraftBillInput(BaseModel):
    pass

class GenerateInvoiceInput(BaseModel):
    bill_id: Optional[int] = Field(default=None, description="Specific bill ID. Leave blank for the most recent finalized bill.")
