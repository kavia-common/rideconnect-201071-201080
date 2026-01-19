from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.api.db import get_db
from src.api.deps import get_current_user, require_driver
from src.api.models.driver import Driver
from src.api.models.ride import Ride, RideEvent, RideStatus
from src.api.models.user import User, UserRole
from src.api.schemas.ride import (
    RideAssignRequest,
    RideCreateRequest,
    RideEventPublic,
    RideHistoryResponse,
    RidePublic,
    RideStatusUpdateRequest,
)

router = APIRouter(prefix="/rides", tags=["rides"])


def _utcnow() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _to_public(ride: Ride) -> RidePublic:
    """Convert ORM Ride row to public schema."""
    return RidePublic(
        id=ride.id,
        rider_id=ride.rider_id,
        driver_id=ride.driver_id,
        origin_lat=float(ride.origin_lat),
        origin_lng=float(ride.origin_lng),
        dest_lat=float(ride.dest_lat),
        dest_lng=float(ride.dest_lng),
        status=ride.status,
        fare_cents=ride.fare_cents,
        created_at=ride.created_at,
        updated_at=ride.updated_at,
    )


def _to_event_public(ev: RideEvent) -> RideEventPublic:
    """Convert ORM RideEvent row to public schema."""
    return RideEventPublic(
        id=ev.id,
        ride_id=ev.ride_id,
        event_type=ev.event_type,
        payload=dict(ev.payload or {}),
        created_at=ev.created_at,
    )


def _forbidden(detail: str) -> HTTPException:
    """Standardized forbidden exception."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _not_found() -> HTTPException:
    """Standardized not-found exception."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ride not found.")


def _ensure_user_can_view_ride(current_user: User, ride: Ride) -> None:
    """Ensure current user is ride rider or driver."""
    if ride.rider_id == current_user.id:
        return
    if ride.driver_id is not None and ride.driver_id == current_user.id:
        return
    raise _forbidden("You do not have access to this ride.")


def _ensure_driver_can_modify_ride(driver_user: User, ride: Ride) -> None:
    """Ensure the ride is assigned to the current driver (or not assigned yet for assignment flows)."""
    role_value = driver_user.role.value if hasattr(driver_user.role, "value") else str(driver_user.role)
    if role_value != UserRole.driver.value:
        raise _forbidden("Driver role required.")
    if ride.driver_id is None:
        # Some operations (like assign) allow assignment when unassigned; callers should validate context.
        return
    if ride.driver_id != driver_user.id:
        raise _forbidden("This ride is not assigned to the current driver.")


def _allowed_transitions() -> Dict[RideStatus, set[RideStatus]]:
    """
    Transition rules.

    DB enum values: requested -> assigned -> enroute -> started -> completed
    Cancellation can happen from requested/assigned/enroute/started.

    Note: We keep transitions aligned with DB enum (enroute/started) while the
    user instruction uses accepted/en_route/picked_up; those map to:
    accepted -> assigned
    en_route -> enroute
    picked_up -> started
    completed -> completed
    canceled -> canceled
    """
    return {
        RideStatus.requested: {RideStatus.assigned, RideStatus.canceled},
        RideStatus.assigned: {RideStatus.enroute, RideStatus.canceled},
        RideStatus.enroute: {RideStatus.started, RideStatus.canceled},
        RideStatus.started: {RideStatus.completed, RideStatus.canceled},
        RideStatus.completed: set(),
        RideStatus.canceled: set(),
    }


def _validate_transition(current: RideStatus, new: RideStatus) -> None:
    """Validate ride status transition, raising 409 if invalid."""
    if new == current:
        return
    allowed = _allowed_transitions().get(current, set())
    if new not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invalid status transition from '{current.value}' to '{new.value}'.",
        )


def _add_event(db: Session, ride: Ride, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Persist a ride event row."""
    ev = RideEvent(
        id=__import__("uuid").uuid4(),
        ride_id=ride.id,
        event_type=event_type,
        payload=payload or {},
    )
    db.add(ev)


@router.post(
    "",
    response_model=RidePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a ride booking",
    description="Rider creates a new ride booking (status=requested).",
    operation_id="rides_create",
)
def create_ride(
    payload: RideCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RidePublic:
    """
    Create a new ride booking.

    Auth:
    - Bearer JWT required
    - role must be 'rider'
    """
    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_value != UserRole.rider.value:
        raise _forbidden("Rider role required to create a ride booking.")

    ride = Ride(
        id=__import__("uuid").uuid4(),
        rider_id=current_user.id,
        driver_id=None,
        origin_lat=payload.origin_lat,
        origin_lng=payload.origin_lng,
        dest_lat=payload.dest_lat,
        dest_lng=payload.dest_lng,
        status=RideStatus.requested,
        fare_cents=None,
        updated_at=_utcnow(),
    )

    db.add(ride)
    _add_event(
        db,
        ride,
        "ride_created",
        {
            "rider_id": str(current_user.id),
            "origin": {"lat": payload.origin_lat, "lng": payload.origin_lng},
            "destination": {"lat": payload.dest_lat, "lng": payload.dest_lng},
        },
    )
    db.commit()
    db.refresh(ride)
    return _to_public(ride)


@router.post(
    "/{ride_id}/assign",
    response_model=RidePublic,
    summary="Assign a driver to a ride",
    description="Driver assignment. Only drivers may assign themselves; only when ride is in requested status.",
    operation_id="rides_assign_driver",
)
def assign_driver(
    ride_id: UUID,
    payload: RideAssignRequest,
    db: Session = Depends(get_db),
    current_driver_user: User = Depends(require_driver),
) -> RidePublic:
    """
    Assign a driver to a ride.

    Auth:
    - Bearer JWT required
    - role must be 'driver'

    Rules:
    - Driver can only assign themselves (payload.driver_id must equal current user id).
    - Ride must be in status=requested and unassigned.
    - Driver should be available (drivers.is_available=true) for assignment.
    - Status transitions to assigned.
    """
    if payload.driver_id != current_driver_user.id:
        raise _forbidden("Drivers can only assign themselves to rides.")

    ride = db.scalar(select(Ride).where(Ride.id == ride_id))
    if not ride:
        raise _not_found()

    if ride.driver_id is not None and ride.driver_id != current_driver_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ride is already assigned to another driver.",
        )

    if ride.status != RideStatus.requested:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ride can only be assigned when status is 'requested'.",
        )

    driver_profile = db.scalar(select(Driver).where(Driver.id == current_driver_user.id))
    if not driver_profile or not bool(driver_profile.is_available):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Driver is not available for assignment.",
        )

    ride.driver_id = current_driver_user.id
    ride.status = RideStatus.assigned
    ride.updated_at = _utcnow()

    _add_event(
        db,
        ride,
        "driver_assigned",
        {"driver_id": str(current_driver_user.id)},
    )

    db.add(ride)
    db.commit()
    db.refresh(ride)
    return _to_public(ride)


@router.patch(
    "/{ride_id}/status",
    response_model=RidePublic,
    summary="Update ride status",
    description=(
        "Update ride lifecycle status. Drivers may move assigned rides forward; riders may cancel their own rides "
        "before completion."
    ),
    operation_id="rides_update_status",
)
def update_ride_status(
    ride_id: UUID,
    payload: RideStatusUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RidePublic:
    """
    Update ride status.

    Auth:
    - Bearer JWT required

    Authorization:
    - Rider may cancel their own ride (if not completed/canceled).
    - Driver may update status for rides assigned to them.

    Transition validation:
    - Enforced via _validate_transition() based on DB enum flow.
    """
    ride = db.scalar(select(Ride).where(Ride.id == ride_id))
    if not ride:
        raise _not_found()

    _ensure_user_can_view_ride(current_user, ride)

    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    desired = payload.status

    # Rider constraints
    if role_value == UserRole.rider.value:
        if ride.rider_id != current_user.id:
            raise _forbidden("Only the ride rider may update this ride.")
        if desired != RideStatus.canceled:
            raise _forbidden("Riders may only cancel rides.")
        if ride.status in (RideStatus.completed, RideStatus.canceled):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ride is already completed or canceled.",
            )

    # Driver constraints
    if role_value == UserRole.driver.value:
        _ensure_driver_can_modify_ride(current_user, ride)
        # If driver hasn't been assigned yet, they shouldn't be able to update status.
        if ride.driver_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ride must be assigned to a driver before status can be updated.",
            )
        if ride.driver_id != current_user.id:
            raise _forbidden("This ride is not assigned to the current driver.")

    # Validate transition
    _validate_transition(ride.status, desired)

    old_status = ride.status
    ride.status = desired
    ride.updated_at = _utcnow()

    _add_event(
        db,
        ride,
        "status_changed",
        {"from": old_status.value, "to": desired.value, "by_user_id": str(current_user.id)},
    )

    db.add(ride)
    db.commit()
    db.refresh(ride)
    return _to_public(ride)


@router.get(
    "/{ride_id}",
    response_model=RidePublic,
    summary="Get ride by id",
    description="Return ride details if the current user is the rider or assigned driver.",
    operation_id="rides_get_by_id",
)
def get_ride(
    ride_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RidePublic:
    """
    Get ride details.

    Auth:
    - Bearer JWT required

    Authorization:
    - rider_id == current user id OR driver_id == current user id
    """
    ride = db.scalar(select(Ride).where(Ride.id == ride_id))
    if not ride:
        raise _not_found()
    _ensure_user_can_view_ride(current_user, ride)
    return _to_public(ride)


@router.get(
    "",
    response_model=List[RidePublic],
    summary="List rides for current user",
    description="List rides by role (rider or driver) with optional status filter and pagination.",
    operation_id="rides_list",
)
def list_rides(
    role: str = Query(..., pattern="^(rider|driver)$", description="List rides for current user as rider or driver."),
    status_filter: Optional[RideStatus] = Query(default=None, alias="status", description="Optional ride status filter."),
    limit: int = Query(default=50, ge=1, le=200, description="Max rides to return."),
    offset: int = Query(default=0, ge=0, description="Offset for pagination."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[RidePublic]:
    """
    List rides for current user.

    Auth:
    - Bearer JWT required

    Authorization:
    - role=rider requires current_user.role=rider
    - role=driver requires current_user.role=driver
    """
    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role != role_value:
        raise _forbidden("Requested role does not match current user's role.")

    stmt = select(Ride)
    if role == UserRole.rider.value:
        stmt = stmt.where(Ride.rider_id == current_user.id)
    else:
        stmt = stmt.where(Ride.driver_id == current_user.id)

    if status_filter is not None:
        stmt = stmt.where(Ride.status == status_filter)

    # Show latest rides first; supported by composite indexes we define in ORM (and DB has created_at index).
    stmt = stmt.order_by(desc(Ride.created_at)).limit(limit).offset(offset)

    rides = list(db.scalars(stmt).all())
    return [_to_public(r) for r in rides]


@router.get(
    "/{ride_id}/history",
    response_model=RideHistoryResponse,
    summary="Get ride event history",
    description="Return ride_events for a ride (oldest to newest) if authorized.",
    operation_id="rides_get_history",
)
def get_ride_history(
    ride_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RideHistoryResponse:
    """
    Get ride event history.

    Auth:
    - Bearer JWT required

    Authorization:
    - rider_id == current user id OR driver_id == current user id
    """
    ride = db.scalar(select(Ride).where(Ride.id == ride_id))
    if not ride:
        raise _not_found()

    _ensure_user_can_view_ride(current_user, ride)

    events = list(
        db.scalars(
            select(RideEvent).where(RideEvent.ride_id == ride_id).order_by(RideEvent.created_at.asc())
        ).all()
    )
    return RideHistoryResponse(ride_id=ride_id, events=[_to_event_public(e) for e in events])
