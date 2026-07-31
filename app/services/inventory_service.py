from typing import Optional
from app.models import Product
from app.repositories.product_repository import ProductRepository

WHOLE_UNITS = {"packet", "dozen", "piece"}

def _fmt_qty(qty: float, unit: str) -> str:
    if unit in WHOLE_UNITS:
        return f"{int(qty)} {unit}"
    return f"{qty} {unit}"

class InventoryService:
    """
    Handles all business logic for Inventory.
    Dependency Inversion Principle (DIP): Depends on abstractions/repository, not direct DB connections.
    """
    def __init__(self, repo: ProductRepository):
        self.repo = repo
        
    def add_product(self, chat_id: int, name: str, unit: str, gst_slab_percent: float, cost_price: float, mrp: float, hsn_code: Optional[str] = None) -> str:
        name = name.lower().strip()
        # GST defaults to 0% if not specified
        if gst_slab_percent is None:
            gst_slab_percent = 0.0
        # If cost not given, use MRP (zero margin)
        if cost_price is None:
            cost_price = mrp
        if mrp < cost_price:
            return f"Cannot add product: MRP (₹{mrp}) is below cost price (₹{cost_price})."
        if self.repo.get_by_name(chat_id, name):
            return f"Product '{name}' already exists."
            
        product = Product(
            chat_id=chat_id,
            name=name,
            unit=unit,
            gst_slab_percent=gst_slab_percent,
            cost_price=cost_price,
            mrp=mrp,
            hsn_code=hsn_code
        )
        self.repo.save(product)
        return f"Successfully added {name} at MRP ₹{mrp}."

    def receive_stock(self, chat_id: int, name: str, quantity: float, cost_price: Optional[float] = None, mrp: Optional[float] = None) -> str:
        name = name.lower().strip()
        product = self.repo.get_by_name(chat_id, name)
        if not product:
            return f"Product '{name}' not found. Please add it first."

        # Check sell-below-cost: use new values where provided, fall back to existing
        effective_cost = cost_price if cost_price is not None else product.cost_price
        effective_mrp = mrp if mrp is not None else product.mrp
        if effective_mrp < effective_cost:
            return f"Cannot update: resulting MRP (₹{effective_mrp}) would be below cost price (₹{effective_cost})."
            
        product.stock_quantity = round(product.stock_quantity + quantity, 2)
        if cost_price is not None:
            product.cost_price = cost_price
        if mrp is not None:
            product.mrp = mrp
            
        self.repo.save(product)
        return f"Received {quantity} {product.unit} of {name}. New stock: {product.stock_quantity} {product.unit}."

    def query_stock(self, chat_id: int, name: Optional[str] = None) -> str:
        if name:
            name = name.lower().strip()
            product = self.repo.get_by_name(chat_id, name)
            if not product:
                return f"Product '{name}' not found."
            return f"Stock for {name}: {_fmt_qty(product.stock_quantity, product.unit)} (MRP: ₹{product.mrp}, GST: {product.gst_slab_percent}%)"
        else:
            all_products = self.repo.get_all(chat_id)
            if not all_products:
                return "Inventory is empty. No products have been added yet."
            low_stock_items = [p for p in all_products if p.stock_quantity <= p.reorder_level]
            if not low_stock_items:
                return "No items are currently low on stock."
            lines = [f"{p.name}: {_fmt_qty(p.stock_quantity, p.unit)} left" for p in low_stock_items]
            return "Low stock items:\n" + "\n".join(lines)

    def list_products(self, chat_id: int) -> str:
        products = self.repo.get_all(chat_id)
        if not products:
            return "No products in inventory yet."
        lines = []
        for p in products:
            qty = _fmt_qty(p.stock_quantity, p.unit)
            lines.append(f"{p.name}: {qty}, MRP ₹{p.mrp} (cost ₹{p.cost_price})")
        return "Inventory:\n" + "\n".join(lines)
