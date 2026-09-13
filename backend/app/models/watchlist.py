"""Watchlist SQLAlchemy model — plates flagged for automatic alerting."""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, text

from app.database.connection import Base


class Watchlist(Base):
    """A plate on the watchlist; a match raises an alert."""

    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True)
    plate_number = Column(String(50), nullable=False, index=True)
    description = Column(String(255), nullable=True)
    severity = Column(String(50), nullable=False, server_default="high", default="high")
    is_active = Column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=datetime.utcnow,
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # A plate may be re-added after being deactivated, so uniqueness is
    # enforced only across active rows — mirrors migration b5c1a7d3e9f2.
    __table_args__ = (
        Index(
            "uq_watchlist_active_plate",
            "plate_number",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Watchlist {self.id} {self.plate_number} active={self.is_active}>"
