import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Use environment variable or fallback to default development URL
DATABASE_URL = os.environ.get(
    "DATABASE_URL", 
    "postgresql+psycopg2://sentinel:sentinel_password@127.0.0.1:5433/sentinel"
)

# pool_pre_ping=True ensures the connection is valid before usage
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
