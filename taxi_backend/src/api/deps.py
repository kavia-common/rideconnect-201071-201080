"""
Shared FastAPI dependencies for authentication/authorization.

This module centralizes JWT parsing and role checks so routers can enforce
consistent access controls.
"""

from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.db import get_db
from src.api.models.user import User, UserRole
from src.api.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    """Create a standardized 401 exception with WWW-Authenticate header."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# PUBLIC_INTERFACE
def get_current_token_payload(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any]:
    """
    Return decoded JWT payload for the current request.

    Authentication: Bearer JWT access token.

    Raises:
        HTTPException(401): if token missing/invalid/expired.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Missing authentication token.")
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise _unauthorized("Invalid or expired token.")

    sub = payload.get("sub")
    if not sub:
        raise _unauthorized("Invalid token.")
    return payload


# PUBLIC_INTERFACE
def get_current_user_id(payload: Dict[str, Any] = Depends(get_current_token_payload)) -> UUID:
    """
    Return current authenticated user's id (UUID).

    Raises:
        HTTPException(401): if sub is not a valid UUID.
    """
    try:
        return UUID(str(payload.get("sub")))
    except (TypeError, ValueError):
        raise _unauthorized("Invalid token.")


# PUBLIC_INTERFACE
def get_current_user(db: Session = Depends(get_db), user_id: UUID = Depends(get_current_user_id)) -> User:
    """
    Return the current authenticated User ORM object.

    Raises:
        HTTPException(401): if token valid but user missing (treat as unauth).
    """
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise _unauthorized("Invalid or expired token.")
    return user


# PUBLIC_INTERFACE
def require_driver(current_user: User = Depends(get_current_user)) -> User:
    """
    Ensure the current user has role=driver.

    Raises:
        HTTPException(403): if user is not a driver.
    """
    role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    if role_value != UserRole.driver.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Driver role required.",
        )
    return current_user
