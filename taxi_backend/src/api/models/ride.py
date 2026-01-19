from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.api.models.base import Base


class RideStatus(str, enum.Enum):
    """
    Ride status values matching the Postgres enum `ride_status`.

    Note: The DB enum values are:
    - requested
    - assigned
    - enroute
    - started
    - completed
    - canceled
    """

    requested = "requested"
    assigned = "assigned"
    enroute = "enroute"
    started = "started"
    completed = "completed"
    canceled = "canceled"


class Ride(Base):
    """
    ORM model for the existing `rides` table created by database/rideconnect_init.sql.
    """

    __tablename__ = "rides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    rider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    origin_lat: Mapped[float] = mapped_column(Float, nullable=False)
    origin_lng: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lng: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[RideStatus] = mapped_column(
        Enum(RideStatus, name="ride_status"),
        nullable=False,
        server_default=RideStatus.requested.value,
        index=True,
    )

    fare_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    rider = relationship("User", foreign_keys=[rider_id], lazy="joined")
    driver = relationship("User", foreign_keys=[driver_id], lazy="joined")
    events = relationship(
        "RideEvent",
        back_populates="ride",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class RideEvent(Base):
    """
    ORM model for the existing `ride_events` table created by database/rideconnect_init.sql.
    """

    __tablename__ = "ride_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    ride_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rides.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    event_type: Mapped[str] = mapped_column(nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    ride = relationship("Ride", back_populates="events")


# Extra composite indexes to support common list queries efficiently.
Index("idx_rides_rider_created_at", Ride.rider_id, Ride.created_at.desc())
Index("idx_rides_driver_created_at", Ride.driver_id, Ride.created_at.desc())
Index("idx_ride_events_ride_created_at", RideEvent.ride_id, RideEvent.created_at.asc())
