"""
FastAPI application entrypoint for RideConnect Taxi Backend.

Provides:
- Health check
- Authentication (/auth/*)
- Current user profile (/users/me)

Configuration:
- DATABASE_URL: Postgres connection string
- JWT_SECRET_KEY: secret used to sign access tokens
- JWT_ALGORITHM: optional (default HS256)
- ACCESS_TOKEN_EXPIRE_MINUTES: optional (default 60)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import auth as auth_router
from src.api.routers import drivers as drivers_router
from src.api.routers import rides as rides_router
from src.api.routers import users as users_router

openapi_tags = [
    {"name": "health", "description": "Health and readiness endpoints."},
    {"name": "auth", "description": "Registration and login endpoints."},
    {"name": "users", "description": "User profile endpoints."},
    {"name": "drivers", "description": "Driver onboarding, availability, and discovery endpoints."},
    {"name": "rides", "description": "Ride booking, assignment, lifecycle, and history endpoints."},
]

app = FastAPI(
    title="RideConnect Taxi Backend",
    description="Backend API for RideConnect (riders & drivers).",
    version="0.1.0",
    openapi_tags=openapi_tags,
)

# Basic permissive CORS for early development; tighten for production later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(drivers_router.router)
app.include_router(rides_router.router)


@app.get(
    "/",
    tags=["health"],
    summary="Health check",
    description="Simple health check endpoint.",
    operation_id="health_check",
)
def health_check():
    """Return a simple health response."""
    return {"message": "Healthy"}
