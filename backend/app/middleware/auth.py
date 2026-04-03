"""
API Authentication
==================
Simple API key authentication for all non-webhook, non-health endpoints.
Set DASHBOARD_API_KEY in environment variables.
"""

import logging
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import get_settings

logger = logging.getLogger(__name__)

# Paths that don't require authentication
OPEN_PATHS = {
    "/api/webhook",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Check X-API-KEY header on all protected endpoints."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        settings = get_settings()

        # Skip auth if no API key configured (development mode)
        if not settings.dashboard_api_key:
            return await call_next(request)

        # Skip auth for open paths
        if any(path.startswith(p) for p in OPEN_PATHS):
            return await call_next(request)

        # Check header or query param
        api_key = request.headers.get("x-api-key") or request.query_params.get("api_key")

        if api_key != settings.dashboard_api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

        return await call_next(request)
