"""
In-memory real-time broker for per-ride WebSocket channels.

This module intentionally uses in-memory structures (dict/rooms) for early-stage
development. In production, this should be swapped for a shared broker (Redis,
NATS, etc.) to support horizontal scaling.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

import jwt
from fastapi import HTTPException, WebSocket, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.api.models.ride import Ride
from src.api.models.user import User, UserRole
from src.api.security import decode_token

# Heartbeat and backpressure behavior.
PING_INTERVAL_SECONDS = 20
SEND_TIMEOUT_SECONDS = 3
# When a client is slow for too long, disconnect to protect server memory/CPU.
MAX_CONSECUTIVE_SEND_TIMEOUTS = 3


@dataclass
class Connection:
    """Represents one active WebSocket client connection."""
    websocket: WebSocket
    user_id: UUID
    role: str  # "rider" | "driver"
    connected_at: float


class RideRoom:
    """
    Per-ride room maintaining active rider/driver/admin subscribers.

    The room also stores a last-known driver location snapshot so new subscribers
    can be immediately hydrated.
    """

    def __init__(self, ride_id: UUID):
        self.ride_id = ride_id
        self.riders: dict[UUID, Connection] = {}
        self.drivers: dict[UUID, Connection] = {}
        self.admins: dict[UUID, Connection] = {}
        self.last_location: Optional[dict[str, Any]] = None
        self._lock = asyncio.Lock()

    async def add(self, conn: Connection, channel: str) -> None:
        async with self._lock:
            if channel == "rider":
                self.riders[conn.user_id] = conn
            elif channel == "driver":
                self.drivers[conn.user_id] = conn
            elif channel == "admin":
                self.admins[conn.user_id] = conn
            else:
                raise ValueError(f"Unknown channel: {channel}")

    async def remove(self, user_id: UUID) -> None:
        async with self._lock:
            self.riders.pop(user_id, None)
            self.drivers.pop(user_id, None)
            self.admins.pop(user_id, None)

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "ride_id": str(self.ride_id),
                "rider_count": len(self.riders),
                "driver_count": len(self.drivers),
                "admin_count": len(self.admins),
                "last_location": self.last_location,
            }


class InMemoryRideBroker:
    """In-memory broker mapping ride_id -> RideRoom."""

    def __init__(self):
        self._rooms: dict[UUID, RideRoom] = {}
        self._lock = asyncio.Lock()

    async def get_room(self, ride_id: UUID) -> RideRoom:
        async with self._lock:
            room = self._rooms.get(ride_id)
            if room is None:
                room = RideRoom(ride_id)
                self._rooms[ride_id] = room
            return room

    async def cleanup_if_empty(self, ride_id: UUID) -> None:
        async with self._lock:
            room = self._rooms.get(ride_id)
            if not room:
                return
            snap = await room.snapshot()
            if snap["rider_count"] == 0 and snap["driver_count"] == 0 and snap["admin_count"] == 0:
                self._rooms.pop(ride_id, None)


broker = InMemoryRideBroker()


def _close_code(code: int) -> int:
    """Ensure a valid close code."""
    if 1000 <= code <= 4999:
        return code
    return 1008


async def _safe_send_json(conn: Connection, payload: dict[str, Any]) -> bool:
    """
    Send JSON with timeout. Returns False if client should be disconnected.
    """
    if conn.websocket.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await asyncio.wait_for(conn.websocket.send_text(json.dumps(payload)), timeout=SEND_TIMEOUT_SECONDS)
        return True
    except (asyncio.TimeoutError, RuntimeError):
        return False


async def broadcast_to_ride(
    ride_id: UUID,
    payload: dict[str, Any],
    *,
    to_riders: bool = True,
    to_drivers: bool = True,
    to_admins: bool = True,
) -> None:
    """
    Broadcast payload to all subscribed clients in a ride room.

    This is used by both WebSocket handlers and REST lifecycle updates.

    PUBLIC_INTERFACE
    """
    room = await broker.get_room(ride_id)
    # Copy current connections under lock to avoid iterating while mutating.
    async with room._lock:
        conns: list[Connection] = []
        if to_riders:
            conns.extend(room.riders.values())
        if to_drivers:
            conns.extend(room.drivers.values())
        if to_admins:
            conns.extend(room.admins.values())

    # Attempt to send; collect stale connections for cleanup.
    stale: list[UUID] = []
    send_timeouts: dict[UUID, int] = {}

    for c in conns:
        ok = await _safe_send_json(c, payload)
        if not ok:
            send_timeouts[c.user_id] = send_timeouts.get(c.user_id, 0) + 1
            if send_timeouts[c.user_id] >= MAX_CONSECUTIVE_SEND_TIMEOUTS:
                stale.append(c.user_id)

    for user_id in stale:
        await room.remove(user_id)
    await broker.cleanup_if_empty(ride_id)


def _extract_token_from_ws(websocket: WebSocket) -> Optional[str]:
    """
    Extract JWT from:
    - Authorization: Bearer <token>
    - ?token=<token> query
    """
    auth = websocket.headers.get("authorization")
    if auth:
        parts = auth.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()

    token = websocket.query_params.get("token")
    if token:
        return token.strip()
    return None


# PUBLIC_INTERFACE
async def authenticate_ws_user(websocket: WebSocket, db: Session) -> User:
    """
    Authenticate a WebSocket connection using the existing JWT logic.

    Accepts token via Authorization header or `?token=...`.

    Raises:
        HTTPException(401): on missing/invalid token or unknown user.
    """
    token = _extract_token_from_ws(websocket)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token (use Authorization: Bearer ... or ?token=...).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = UUID(str(sub))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# PUBLIC_INTERFACE
def authorize_ws_ride_access(user: User, ride: Ride, channel: str) -> None:
    """
    Authorization for WebSocket subscription/publish on a ride.

    Rules:
    - /driver channel: only ride.driver_id may connect
    - /rider channel: only ride.rider_id may connect
    - /admin channel: currently allows drivers only (placeholder); tighten later.

    Raises:
        HTTPException(403): if not authorized.
    """
    role_value = user.role.value if hasattr(user.role, "value") else str(user.role)

    if channel == "driver":
        if role_value != UserRole.driver.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Driver role required.")
        if ride.driver_id is None or ride.driver_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the assigned driver for this ride.")
        return

    if channel == "rider":
        if role_value != UserRole.rider.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Rider role required.")
        if ride.rider_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the rider for this ride.")
        return

    if channel == "admin":
        # Placeholder: allow drivers as "admin" view for now.
        if role_value != UserRole.driver.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin channel not allowed.")
        return

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid channel.")


async def _heartbeat_sender(conn: Connection, stop_event: asyncio.Event) -> None:
    """Send periodic ping messages until stop_event is set."""
    while not stop_event.is_set():
        await asyncio.sleep(PING_INTERVAL_SECONDS)
        if stop_event.is_set():
            break
        # Use app-level ping payload for clients (since browser WS doesn't expose ping frames easily).
        ok = await _safe_send_json(conn, {"type": "ping", "ts": time.time()})
        if not ok:
            stop_event.set()


async def run_ws_session(
    websocket: WebSocket,
    *,
    db: Session,
    ride_id: UUID,
    channel: str,
) -> None:
    """
    Generic WebSocket session runner with:
    - auth + authorization
    - room join/leave
    - heartbeat pings
    - graceful disconnect

    For "driver" channel, expects client to send location updates; will broadcast to
    riders/drivers/admins in the same room.

    For "rider"/"admin", primarily acts as a subscriber; it will still accept
    basic client messages (pong, etc.) without affecting state.

    PUBLIC_INTERFACE
    """
    # Accept early so client gets WS upgrade; on auth failure we close with 1008.
    await websocket.accept()

    try:
        user = await authenticate_ws_user(websocket, db)
        ride = db.scalar(select(Ride).where(Ride.id == ride_id))
        if not ride:
            await websocket.close(code=_close_code(1008), reason="Ride not found.")
            return

        authorize_ws_ride_access(user, ride, channel)

        role_value = user.role.value if hasattr(user.role, "value") else str(user.role)
        conn = Connection(
            websocket=websocket,
            user_id=user.id,
            role=role_value,
            connected_at=time.time(),
        )

        room = await broker.get_room(ride_id)
        await room.add(conn, channel)

        # Send initial hydrate message.
        await _safe_send_json(
            conn,
            {
                "type": "connected",
                "ride_id": str(ride_id),
                "channel": channel,
                "user_id": str(user.id),
                "role": role_value,
                "ride_status": ride.status.value if hasattr(ride.status, "value") else str(ride.status),
                "driver_id": str(ride.driver_id) if ride.driver_id else None,
                "rider_id": str(ride.rider_id),
                "last_location": room.last_location,
            },
        )

        stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(_heartbeat_sender(conn, stop))

        try:
            while websocket.client_state == WebSocketState.CONNECTED and not stop.is_set():
                try:
                    msg = await websocket.receive_json()
                except WebSocketDisconnect:
                    break
                except Exception:
                    # Client sent invalid JSON or unexpected frame.
                    await websocket.send_json({"type": "error", "message": "Invalid message format; expected JSON."})
                    continue

                mtype = msg.get("type")
                if mtype == "pong":
                    continue

                if channel == "driver" and mtype in ("location", "driver_location"):
                    lat = msg.get("lat")
                    lng = msg.get("lng")
                    if lat is None or lng is None:
                        await websocket.send_json({"type": "error", "message": "Missing lat/lng."})
                        continue

                    # Store last known location in memory.
                    location_payload = {
                        "type": "driver_location",
                        "ride_id": str(ride_id),
                        "driver_id": str(user.id),
                        "lat": float(lat),
                        "lng": float(lng),
                        "ts": msg.get("ts") or time.time(),
                    }
                    async with room._lock:
                        room.last_location = location_payload

                    # Persist minimal event for auditing.
                    # NOTE: Keep persistence small to avoid DB bloat; callers may throttle on client side.
                    from src.api.routers.rides import add_ride_event  # local import to avoid circular at module level

                    await add_ride_event(
                        db,
                        ride_id=ride_id,
                        event_type="driver_location",
                        payload={"driver_id": str(user.id), "lat": float(lat), "lng": float(lng)},
                    )

                    await broadcast_to_ride(
                        ride_id,
                        location_payload,
                        to_riders=True,
                        to_drivers=True,
                        to_admins=True,
                    )
                    continue

                # Unknown message types are ignored (forward compatibility).
                await websocket.send_json({"type": "ack", "received_type": mtype})
        finally:
            stop.set()
            heartbeat_task.cancel()
            await room.remove(user.id)
            await broker.cleanup_if_empty(ride_id)

    except HTTPException as e:
        # If we already accepted, close politely. Also send a structured error first.
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_json({"type": "error", "message": e.detail})
            except Exception:
                pass
            await websocket.close(code=_close_code(1008), reason=str(e.detail))
        return
    except Exception:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=_close_code(1011), reason="Internal server error")
        return
