from sqlmodel import Session, select
from app.models import Product
from typing import Optional, List

class ProductRepository:
    """
    Handles all database interactions for Products.
    Single Responsibility Principle (SRP): Only concerned with DB access.
    """
    def __init__(self, session: Session):
        self.session = session
        
    def get_by_name(self, chat_id: int, name: str) -> Optional[Product]:
        return self.session.exec(
            select(Product).where(Product.chat_id == chat_id, Product.name == name)
        ).first()
        
    def get_low_stock(self, chat_id: int) -> List[Product]:
        return self.session.exec(
            select(Product).where(
                Product.chat_id == chat_id,
                Product.stock_quantity <= Product.reorder_level
            )
        ).all()

    def get_all(self, chat_id: int) -> List[Product]:
        return self.session.exec(
            select(Product).where(Product.chat_id == chat_id)
        ).all()
        
    def save(self, product: Product) -> Product:
        self.session.add(product)
        self.session.commit()
        self.session.refresh(product)
        return product
