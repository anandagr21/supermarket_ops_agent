import os
from sqlmodel import create_engine, SQLModel, Session
from dotenv import load_dotenv

load_dotenv()

# We expect a POSTGRES_URL in the .env file.
# If not present, we can fall back to sqlite for rapid local testing if needed,
# but we aim for Postgres as per the plan.
DATABASE_URL = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/supermarket")

# The connect_args are just for sqlite in case we fallback. Safe to pass empty dict for postgres.
connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
