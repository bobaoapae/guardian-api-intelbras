"""API v1 routes."""
from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.devices import router as devices_router
from app.api.v1.alarm import router as alarm_router
from app.api.v1.events import router as events_router
from app.api.v1.zones import router as zones_router

# Create main API router
api_router = APIRouter(prefix="/api/v1")

# Include all routers
api_router.include_router(auth_router)
api_router.include_router(devices_router)
api_router.include_router(alarm_router)
api_router.include_router(events_router)
api_router.include_router(zones_router)
