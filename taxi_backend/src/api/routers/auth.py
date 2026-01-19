from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.db import get_db
from src.api.models.user import User, UserRole
from src.api.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from src.api.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Create a new user account (rider or driver) and return an access token.",
    operation_id="auth_register",
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """
    Register a new user and return a JWT access token.

    Errors:
    - 400 if role is invalid (defensive)
    - 409 if email already exists
    """
    try:
        role = UserRole(payload.role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Must be 'rider' or 'driver'.",
        )

    user = User(
        # Let DB default generate UUID if not provided, but SQLAlchemy model uses PK required;
        # We'll rely on server default in DB by leaving id unset is not possible due to mapped_column.
        # The schema uses DEFAULT gen_random_uuid(); to use it, we insert via SQL expression by omitting id.
        # However ORM requires PK value for identity map; easiest is to generate UUID here.
        id=__import__("uuid").uuid4(),
        name=payload.name.strip(),
        email=str(payload.email).lower().strip(),
        password_hash=hash_password(payload.password),
        role=role,
    )

    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    db.refresh(user)

    token = create_access_token(subject=user.id, role=user.role.value)
    return TokenResponse(access_token=token)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login",
    description="Authenticate a user by email/password and return an access token.",
    operation_id="auth_login",
)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """
    Login by verifying user credentials and return a JWT access token.

    Errors:
    - 401 for invalid credentials
    """
    email = str(payload.email).lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(subject=user.id, role=user.role.value)
    return TokenResponse(access_token=token)
