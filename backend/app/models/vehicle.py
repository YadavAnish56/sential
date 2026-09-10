"""Vehicle SQLAlchemy model — deduplicated vehicle identities by plate number."""

from sqlalchemy import Column, Integer, String

from app.database.connection import Base


class Vehicle(Base):
    """A vehicle identity, uniquely keyed by its canonical plate number."""

    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True)
    plate_number = Column(String(50), nullable=False, unique=True, index=True)
    vehicle_type = Column(String(50), nullable=True)
    color = Column(String(50), nullable=True)
    make = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)

    def __repr__(self) -> str:
        return f"<Vehicle {self.plate_number}>"
