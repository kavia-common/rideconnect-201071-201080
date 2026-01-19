from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from src.api.models.ride import RideStatus


class RideCreateRequest(BaseModel):
    origin_lat: float = Field(..., ge=-90, le=90, description="Pickup latitude in degrees.")
    origin_lng: float = Field(..., ge=-180, le=180, description="Pickup longitude in degrees.")
    dest_lat: float = Field(..., ge=-90, le=90, description="Destination latitude in degrees.")
    dest_lng: float = Field(..., ge=-180, le=180, description="Destination longitude in degrees.")


class RideAssignRequest(BaseModel):
    driver_id: UUID = Field(..., description="Driver user id to assign to the ride.")


class RideStatusUpdateRequest(BaseModel):
    status: RideStatus = Field(
        ...,
        description="New ride status. Allowed: requested, assigned, enroute, started, completed, canceled.",
    )


class RidePublic(BaseModel):
    id: UUID = Field(..., description="Ride id.")
    rider_id: UUID = Field(..., description="Rider user id who booked the ride.")
    driver_id: Optional[UUID] = Field(default=None, description="Assigned driver user id (nullable).")

    origin_lat: float = Field(..., description="Pickup latitude.")
    origin_lng: float = Field(..., description="Pickup longitude.")
    dest_lat: float = Field(..., description="Destination latitude.")
    dest_lng: float = Field(..., description="Destination longitude.")

    status: RideStatus = Field(..., description="Current ride status.")
    fare_cents: Optional[int] = Field(default=None, description="Final fare in cents (nullable).")

    created_at: datetime = Field(..., description="When the ride was created.")
    updated_at: datetime = Field(..., description="When the ride was last updated.")


class RideEventPublic(BaseModel):
    id: UUID = Field(..., description="Event id.")
    ride_id: UUID = Field(..., description="Ride id.")
    event_type: str = Field(..., description="Event type string.")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Event payload JSON.")
    created_at: datetime = Field(..., description="When event was created.")


class RideHistoryResponse(BaseModel):
    ride_id: UUID = Field(..., description="Ride id.")
    events: List[RideEventPublic] = Field(..., description="Ordered list of ride events (oldest -> newest).")


class RideListQuery(BaseModel):
    """
    Internal helper schema (not used as request body) documenting list query shape.
    """

    role: str = Field(..., pattern="^(rider|driver)$", description="List rides for current user as rider or driver.")
    status: Optional[RideStatus] = Field(default=None, description="Optional status filter.")
    limit: int = Field(default=50, ge=1, le=200, description="Max rides to return.")
    offset: int = Field(default=0, ge=0, description="Offset for pagination.")
