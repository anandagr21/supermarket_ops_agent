from sqlmodel import Session
from app.schemas.inventory_schemas import AddProductInput, ReceiveStockInput, QueryStockInput
from app.repositories.product_repository import ProductRepository
from app.services.inventory_service import InventoryService

def get_inventory_service(session: Session) -> InventoryService:
    repo = ProductRepository(session)
    return InventoryService(repo)

# --- Tools ---

def add_product(session: Session, chat_id: int, args: AddProductInput) -> str:
    """Add a new product/SKU to the store."""

    service = get_inventory_service(session)
    return service.add_product(
        chat_id, args.name, args.unit, args.gst_slab_percent, 
        args.cost_price, args.mrp, args.hsn_code
    )

def receive_stock(session: Session, chat_id: int, args: ReceiveStockInput) -> str:
    """Add stock to an existing product. Can optionally update cost and MRP."""
    service = get_inventory_service(session)
    return service.receive_stock(chat_id, args.name, args.quantity, args.cost_price, args.mrp)

def query_stock(session: Session, chat_id: int, args: QueryStockInput) -> str:
    """Check stock for a specific product, or list low stock items if name is not provided."""
    service = get_inventory_service(session)
    return service.query_stock(chat_id, args.name)
