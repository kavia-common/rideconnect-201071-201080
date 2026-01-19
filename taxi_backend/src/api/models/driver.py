import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.api.models.base import Base


class Driver(Base):
    """
    ORM model for the existing 'drivers' table created by database/rideconnect_init.sql.

    Notes:
    - Primary key equals the corresponding users.id (1:1 relationship).
    - updated_at is managed server-side by default now(); the API also updates it
      when availability/location changes.
    """

    __tablename__ = "drivers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    vehicle_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_no: Mapped[str | None] = mapped_column(Text, nullable=True)

    rating: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, server_default="5.00")
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    location_lat: Mapped[float | None] = mapped_column(nullable=True)
    location_lng: Mapped[float | None] = mapped_column(nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationship is optional for current feature, but useful.
    user = relationship("User", lazy="joined")
