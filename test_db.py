import os

# We will temporarily force SQLite for this quick test 
# so it doesn't fail if you haven't started your Postgres Docker container yet.
os.environ["POSTGRES_URL"] = "sqlite:///./test.db"

from app.database import create_db_and_tables, get_session
from app.models import Product

def test():
    try:
        print("1. Creating tables in DB...")
        create_db_and_tables()
        print("   ✅ Tables created successfully!")
        
        print("\n2. Testing data insertion (SQLModel + SQLAlchemy)...")
        session_gen = get_session()
        session = next(session_gen)
        
        # Try to insert a dummy product
        p = Product(
            chat_id=123, 
            name="test_sugar", 
            unit="kg", 
            gst_slab_percent=5.0, 
            cost_price=40.0, 
            mrp=50.0
        )
        session.add(p)
        session.commit()
        
        print("   ✅ Insert successful!")
        print("\n🎉 The Database schemas and connection logic are working perfectly!")
    except Exception as e:
        print(f"\n❌ Error occurred: {e}")

if __name__ == "__main__":
    test()
