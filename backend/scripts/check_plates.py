import sys
sys.path.insert(0, '.')
from app.core.config import settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.vehicle import Vehicle
from app.models.event import Event

engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

vehicles = db.query(Vehicle).all()
print("Vehicles in DB:")
for v in vehicles:
    print(f"- ID: {v.id}, Plate: {v.plate_number}")

events = db.query(Event).all()
print("\nEvents in DB:")
for e in events:
    print(f"- Event: {e.event_type}, Vehicle ID: {e.vehicle_id}, Confidence: {e.confidence}")
