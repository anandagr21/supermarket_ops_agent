from sqlmodel import Session
from app.schemas.inventory_schemas import AddProductInput, ReceiveStockInput, QueryStockInput, ListProductsInput, UpdateProductInput
from app.repositories.product_repository import ProductRepository
from app.services.inventory_service import InventoryService
from app.logger import setup_logger

log = setup_logger("tools.inventory")


def get_inventory_service(session: Session) -> InventoryService:
    repo = ProductRepository(session)
    return InventoryService(repo)

# --- Tools ---

def add_product(session: Session, chat_id: int, args: AddProductInput) -> str:
    """Add a new product/SKU to the store."""
    log.info(f"chat_id={chat_id} | add_product({args.name}, unit={args.unit}, gst={args.gst_slab_percent}%, cost=₹{args.cost_price}, mrp=₹{args.mrp})")
    service = get_inventory_service(session)
    result = service.add_product(
        chat_id, args.name, args.unit, args.gst_slab_percent,
        args.cost_price, args.mrp, args.hsn_code
    )
    log.info(f"chat_id={chat_id} | add_product → {result}")
    return result

def receive_stock(session: Session, chat_id: int, args: ReceiveStockInput) -> str:
    """Add stock to an existing product. Can optionally update cost and MRP."""
    log.info(f"chat_id={chat_id} | receive_stock({args.name}, qty={args.quantity})")
    service = get_inventory_service(session)
    result = service.receive_stock(chat_id, args.name, args.quantity, args.cost_price, args.mrp)
    log.info(f"chat_id={chat_id} | receive_stock → {result}")
    return result

def query_stock(session: Session, chat_id: int, args: QueryStockInput) -> str:
    """Check stock for a specific product, or list low stock items if name is not provided."""
    log.info(f"chat_id={chat_id} | query_stock(name={args.name})")
    service = get_inventory_service(session)
    result = service.query_stock(chat_id, args.name)
    log.info(f"chat_id={chat_id} | query_stock → {result[:120]}")
    return result

def update_product(session: Session, chat_id: int, args: UpdateProductInput) -> str:
    """Update product price, cost, or GST WITHOUT changing stock quantity."""
    log.info(f"chat_id={chat_id} | update_product({args.name}, cost={args.cost_price}, mrp={args.mrp}, gst={args.gst_slab_percent})")
    service = get_inventory_service(session)
    result = service.update_product(chat_id, args.name, args.cost_price, args.mrp, args.gst_slab_percent)
    log.info(f"chat_id={chat_id} | update_product → {result}")
    return result

def list_products(session: Session, chat_id: int, args: ListProductsInput) -> str:
    """List ALL products in inventory with stock levels and prices."""
    log.info(f"chat_id={chat_id} | list_products")
    service = get_inventory_service(session)
    result = service.list_products(chat_id)
    log.info(f"chat_id={chat_id} | list_products → {result[:120]}")
    return result
