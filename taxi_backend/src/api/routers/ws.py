"""
WebSocket routes for live ride tracking.

Endpoints:
- /ws/ride/{ride_id}/driver : assigned driver publishes location updates for an active ride
- /ws/ride/{ride_id}/rider  : ride rider subscribes to ride status + driver location
- /ws/ride/{ride_id}/admin  : optional room for admin/driver monitoring

Auth:
- Provide JWT via `Authorization: Bearer <token>` OR query param `?token=<token>`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket
from sqlalchemy.orm import Session

from src.api.db import get_db
from src.api.realtime import run_ws_session

router = APIRouter(prefix="/ws", tags=["realtime"])


@router.websocket("/ride/{ride_id}/driver")
async def ws_ride_driver(websocket: WebSocket, ride_id: UUID, db: Session = Depends(get_db)) -> None:
    """
    Driver WebSocket: publish driver location updates for an active ride.

    Client messages (JSON):
    - {"type":"location","lat":..., "lng":..., "ts": optional}

    Server messages (JSON):
    - {"type":"connected", ...}
    - {"type":"driver_location", ...}  (broadcast)
    - {"type":"ping", ...} heartbeat
    """
    await run_ws_session(websocket, db=db, ride_id=ride_id, channel="driver")


@router.websocket("/ride/{ride_id}/rider")
async def ws_ride_rider(websocket: WebSocket, ride_id: UUID, db: Session = Depends(get_db)) -> None:
    """
    Rider WebSocket: subscribe to live ride status and driver location.

    Server messages (JSON):
    - {"type":"connected", ...} includes last_location if available
    - {"type":"ride_status", ...} when ride lifecycle changes
    - {"type":"driver_location", ...} when driver sends updates
    - {"type":"ping", ...} heartbeat
    """
    await run_ws_session(websocket, db=db, ride_id=ride_id, channel="rider")


@router.websocket("/ride/{ride_id}/admin")
async def ws_ride_admin(websocket: WebSocket, ride_id: UUID, db: Session = Depends(get_db)) -> None:
    """
    Optional admin room channel.

    Currently allows drivers only (placeholder). Intended for operations tooling.
    """
    await run_ws_session(websocket, db=db, ride_id=ride_id, channel="admin")
