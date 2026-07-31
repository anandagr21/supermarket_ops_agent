from typing import Optional, Literal
from pydantic import BaseModel, Field

class AddProductInput(BaseModel):
    name: str = Field(description="Name of the product/SKU.")
    unit: Literal["kg", "g", "litre", "ml", "packet", "dozen", "piece"] = Field(description="Unit of measurement.")
    gst_slab_percent: Optional[float] = Field(default=0.0, description="GST percentage (e.g. 0, 5, 12, 18). Defaults to 0%.")
    cost_price: Optional[float] = Field(default=None, gt=0, description="Cost price in INR. If omitted, defaults to MRP.")
    mrp: float = Field(gt=0, description="Maximum Retail Price (Sell price) in INR.")
    hsn_code: Optional[str] = Field(default=None, description="Optional HSN code for GST billing.")

class ReceiveStockInput(BaseModel):
    name: str = Field(description="Product name")
    quantity: float = Field(gt=0, description="Amount of stock received.")
    cost_price: Optional[float] = Field(default=None, gt=0, description="Optional new cost price.")
    mrp: Optional[float] = Field(default=None, gt=0, description="Optional new MRP.")

class QueryStockInput(BaseModel):
    name: Optional[str] = Field(default=None, description="Specific product name to check. Leave blank to check all low stock.")

class ListProductsInput(BaseModel):
    pass

class SetReorderLevelInput(BaseModel):
    name: str = Field(description="Product name.")
    reorder_level: float = Field(ge=0, description="Minimum stock level before low-stock warning.")

class UpdateProductInput(BaseModel):
    name: str = Field(description="Product name to update.")
    cost_price: Optional[float] = Field(default=None, gt=0, description="New cost price.")
    mrp: Optional[float] = Field(default=None, gt=0, description="New MRP.")
    gst_slab_percent: Optional[float] = Field(default=None, description="New GST percentage.")
