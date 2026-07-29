from pydantic import BaseModel, Field

class OpenKhataInput(BaseModel):
    customer_name: str = Field(description="Name of the customer for the credit ledger.")

class RecordKhataPaymentInput(BaseModel):
    customer_name: str = Field(description="Name of the customer paying their debt.")
    amount: float = Field(gt=0, description="Amount paid in INR.")

class GetKhataBalanceInput(BaseModel):
    customer_name: str = Field(description="Name of the customer.")
