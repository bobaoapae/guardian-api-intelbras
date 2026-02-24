"""FastAPI application entry point for Intelbras Guardian middleware."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.core.exceptions import GuardianBaseException
from app.api.v1 import api_router
from app.services import state_manager, auth_service, guardian_client
from app.services.isecnet_client import isecnet_client

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Intelbras Guardian API Middleware")
    logger.info(f"API URL: {settings.INTELBRAS_API_URL}")
    logger.info(f"State Backend: {settings.STATE_BACKEND}")
    logger.info(f"Debug Mode: {settings.DEBUG}")

    # Start state manager cleanup task
    await state_manager.start_cleanup_task()

    # Start ISECNet client service
    await isecnet_client.start()
    logger.info("ISECNet client service started")

    yield

    # Shutdown
    logger.info("Shutting down Intelbras Guardian API Middleware")

    # Stop ISECNet client service
    await isecnet_client.stop()
    logger.info("ISECNet client service stopped")

    # Stop cleanup task
    await state_manager.stop_cleanup_task()

    # Close HTTP sessions
    await auth_service.close()
    await guardian_client.close()


# Create FastAPI app
app = FastAPI(
    title="Intelbras Guardian API Middleware",
    description="OAuth 2.0 middleware for Intelbras Guardian alarm systems",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router)


# Exception handlers
@app.exception_handler(GuardianBaseException)
async def guardian_exception_handler(request, exc: GuardianBaseException):
    """Handle custom Guardian exceptions."""
    logger.error(f"Guardian error: {exc.message}", extra={"details": exc.details})
    return JSONResponse(
        status_code=400,
        content={
            "error": exc.__class__.__name__,
            "message": exc.message,
            "details": exc.details
        }
    )


# Health check endpoint
@app.get("/api/v1/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint.

    Returns service status and configuration info (non-sensitive).
    """
    stats = await state_manager.get_stats()

    # Get ISECNet connection stats
    isecnet_connections = {
        device_id: {
            "connected": conn.protocol.is_connected,
            "authenticated": conn.protocol.is_authenticated,
            "mac": conn.mac,
            "connected_at": conn.connected_at.isoformat(),
            "last_activity": conn.last_activity.isoformat()
        }
        for device_id, conn in isecnet_client._connections.items()
    }

    return {
        "status": "healthy",
        "service": "intelbras-guardian-middleware",
        "version": "1.0.0",
        "config": {
            "api_url": settings.INTELBRAS_API_URL,
            "state_backend": settings.STATE_BACKEND,
            "debug_mode": settings.DEBUG
        },
        "stats": stats,
        "isecnet": {
            "active_connections": len(isecnet_connections),
            "connections": isecnet_connections
        }
    }


# Static files directory
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", tags=["Root"], include_in_schema=False)
async def root():
    """Serve the web UI."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api", tags=["Root"])
async def api_info():
    """API information endpoint."""
    return {
        "message": "Intelbras Guardian API Middleware",
        "docs": "/docs",
        "health": "/api/v1/health",
        "endpoints": {
            "auth": "/api/v1/auth",
            "devices": "/api/v1/devices",
            "alarm": "/api/v1/alarm",
            "events": "/api/v1/events"
        }
    }
