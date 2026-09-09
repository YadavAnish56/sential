from sqlalchemy import text
from app.database.connection import engine

def test_connection():
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            for row in result:
                print(f"Database connection successful! Result: {row[0]}")
    except Exception as e:
        print(f"Database connection failed! Error: {e}")

if __name__ == "__main__":
    test_connection()