from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DriverProfileUpsert(BaseModel):
    vehicle_info: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Vehicle description (make/model/color/plate).",
    )
    license_no: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Driver license number (freeform).",
    )


class DriverAvailabilityUpdate(BaseModel):
    is_available: bool = Field(..., description="Whether driver is currently available for matching.")


class DriverLocationUpdate(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="Latitude in degrees.")
    lng: float = Field(..., ge=-180, le=180, description="Longitude in degrees.")


class DriverPublic(BaseModel):
    id: UUID = Field(..., description="Driver user id (same as users.id).")
    vehicle_info: Optional[str] = Field(default=None, description="Vehicle description.")
    license_no: Optional[str] = Field(default=None, description="License number (may be null).")
    rating: float = Field(..., description="Driver rating placeholder.")
    is_available: bool = Field(..., description="Current availability status.")
    location_lat: Optional[float] = Field(default=None, description="Last known latitude.")
    location_lng: Optional[float] = Field(default=None, description="Last known longitude.")
    updated_at: datetime = Field(..., description="When driver record was last updated.")


class AvailableDriversQuery(BaseModel):
    """
    Internal helper schema (not used as request body) documenting query parameters shape.
    """

    lat: Optional[float] = Field(default=None, ge=-90, le=90, description="Filter center latitude.")
    lng: Optional[float] = Field(default=None, ge=-180, le=180, description="Filter center longitude.")
    radius_km: Optional[float] = Field(
        default=None,
        gt=0,
        le=200,
        description="Radius in kilometers (max 200km) for proximity filtering.",
    )
