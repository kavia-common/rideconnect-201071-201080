from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from src.api.db import get_db
from src.api.deps import require_driver
from src.api.models.driver import Driver
from src.api.models.user import User
from src.api.schemas.driver import (
    DriverAvailabilityUpdate,
    DriverLocationUpdate,
    DriverProfileUpsert,
    DriverPublic,
)

router = APIRouter(prefix="/drivers", tags=["drivers"])


def _utcnow() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _to_public(d: Driver) -> DriverPublic:
    """Convert ORM Driver row to public schema."""
    # rating is Numeric(3,2) -> could be Decimal; cast to float for JSON.
    rating_value = float(d.rating) if d.rating is not None else 5.0
    return DriverPublic(
        id=d.id,
        vehicle_info=d.vehicle_info,
        license_no=d.license_no,
        rating=rating_value,
        is_available=bool(d.is_available),
        location_lat=d.location_lat,
        location_lng=d.location_lng,
        updated_at=d.updated_at,
    )


def _validate_proximity_args(lat: Optional[float], lng: Optional[float], radius_km: Optional[float]) -> None:
    """Validate that proximity params are provided consistently."""
    any_prox = lat is not None or lng is not None or radius_km is not None
    if not any_prox:
        return
    if lat is None or lng is None or radius_km is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="lat, lng, and radius_km must be provided together for proximity filtering.",
        )


def _distance_km_haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Haversine distance in km.

    We do proximity filtering in Python to avoid adding PostGIS as a dependency.
    """
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)

    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


@router.get(
    "/me",
    response_model=DriverPublic,
    summary="Get current driver's profile",
    description="Return the authenticated driver's driver-profile record.",
    operation_id="drivers_get_me",
)
def get_my_driver_profile(
    current_user: User = Depends(require_driver),
    db: Session = Depends(get_db),
) -> DriverPublic:
    """
    Get the current driver's driver profile.

    Auth:
    - Bearer JWT
    - role must be 'driver'
    """
    driver = db.scalar(select(Driver).where(Driver.id == current_user.id))
    if not driver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver profile not found.")
    return _to_public(driver)


@router.put(
    "/me",
    response_model=DriverPublic,
    summary="Create or update driver profile (onboarding)",
    description="Upsert the authenticated driver's profile (vehicle info, license).",
    operation_id="drivers_upsert_me",
)
def upsert_my_driver_profile(
    payload: DriverProfileUpsert,
    current_user: User = Depends(require_driver),
    db: Session = Depends(get_db),
) -> DriverPublic:
    """
    Upsert the current driver's profile.

    Creates a row in drivers table if one doesn't exist yet, otherwise updates
    vehicle/license fields.

    Notes:
    - rating remains default/placeholder.
    - updated_at is refreshed.
    """
    driver = db.scalar(select(Driver).where(Driver.id == current_user.id))
    if not driver:
        driver = Driver(id=current_user.id)

    # Apply updates; allow nulls to clear values.
    driver.vehicle_info = payload.vehicle_info.strip() if payload.vehicle_info is not None else None
    driver.license_no = payload.license_no.strip() if payload.license_no is not None else None
    driver.updated_at = _utcnow()

    db.add(driver)
    db.commit()
    db.refresh(driver)
    return _to_public(driver)


@router.patch(
    "/me/availability",
    response_model=DriverPublic,
    summary="Update driver availability",
    description="Toggle whether the authenticated driver is available for matching.",
    operation_id="drivers_update_availability",
)
def update_my_availability(
    payload: DriverAvailabilityUpdate,
    current_user: User = Depends(require_driver),
    db: Session = Depends(get_db),
) -> DriverPublic:
    """
    Set driver's availability (is_available).

    Creates driver row if absent (common during onboarding).
    """
    driver = db.scalar(select(Driver).where(Driver.id == current_user.id))
    if not driver:
        driver = Driver(id=current_user.id)

    driver.is_available = payload.is_available
    driver.updated_at = _utcnow()

    db.add(driver)
    db.commit()
    db.refresh(driver)
    return _to_public(driver)


@router.patch(
    "/me/location",
    response_model=DriverPublic,
    summary="Update driver current location",
    description="Persist the authenticated driver's last known lat/lng and refresh updated_at.",
    operation_id="drivers_update_location",
)
def update_my_location(
    payload: DriverLocationUpdate,
    current_user: User = Depends(require_driver),
    db: Session = Depends(get_db),
) -> DriverPublic:
    """
    Update driver's last known location.

    Creates driver row if absent (common during onboarding).

    Note:
    - Location update does not automatically set is_available; clients should
      call availability endpoint separately as needed.
    """
    driver = db.scalar(select(Driver).where(Driver.id == current_user.id))
    if not driver:
        driver = Driver(id=current_user.id)

    driver.location_lat = payload.lat
    driver.location_lng = payload.lng
    driver.updated_at = _utcnow()

    db.add(driver)
    db.commit()
    db.refresh(driver)
    return _to_public(driver)


@router.get(
    "/available",
    response_model=List[DriverPublic],
    summary="List currently-available drivers",
    description=(
        "Fetch drivers with is_available=true. Optionally filter by proximity using "
        "lat/lng/radius_km (Haversine computed server-side)."
    ),
    operation_id="drivers_list_available",
)
def list_available_drivers(
    lat: Optional[float] = Query(default=None, ge=-90, le=90, description="Filter center latitude."),
    lng: Optional[float] = Query(default=None, ge=-180, le=180, description="Filter center longitude."),
    radius_km: Optional[float] = Query(
        default=None,
        gt=0,
        le=200,
        description="Radius in kilometers (max 200km) for proximity filtering.",
    ),
    db: Session = Depends(get_db),
) -> List[DriverPublic]:
    """
    List available drivers for matching.

    This endpoint is intentionally not restricted to drivers; riders/matching
    services may call it.

    Implementation detail:
    - Performs a coarse DB filter for non-null lat/lng when proximity is used.
    - Computes exact Haversine distance in Python and filters by radius_km.
    """
    _validate_proximity_args(lat, lng, radius_km)

    stmt = select(Driver).where(Driver.is_available.is_(True))

    # If proximity filtering requested, we require non-null coordinates.
    if lat is not None and lng is not None and radius_km is not None:
        stmt = stmt.where(and_(Driver.location_lat.is_not(None), Driver.location_lng.is_not(None)))

    drivers = list(db.scalars(stmt).all())

    if lat is not None and lng is not None and radius_km is not None:
        filtered: list[Driver] = []
        for d in drivers:
            if d.location_lat is None or d.location_lng is None:
                continue
            dist = _distance_km_haversine(lat, lng, float(d.location_lat), float(d.location_lng))
            if dist <= radius_km:
                filtered.append(d)
        drivers = filtered

    return [_to_public(d) for d in drivers]
