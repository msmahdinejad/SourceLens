from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

_DOMAIN_ROUTERS = {
    "workspaces": APIRouter(tags=["workspaces"]),
    "studio": APIRouter(tags=["studio"]),
    "chat": APIRouter(tags=["chat"]),
    "providers": APIRouter(tags=["providers"]),
    "observability": APIRouter(tags=["observability"]),
    "compatibility": APIRouter(tags=["compatibility"]),
}

def _domain(path: str) -> str:
    if "/studio/" in path or path == "/api/studio/catalog":
        return "studio"
    if path.startswith(("/api/chat", "/api/tools", "/api/transcribe")) or "/conversations" in path:
        return "chat"
    if path.startswith(
        ("/api/provider-settings", "/api/integrations", "/api/fonts", "/api/status")
    ):
        return "providers"
    if path.startswith("/api/traces"):
        return "observability"
    return "workspaces"


def install_domain_routers(app: FastAPI) -> None:
    """Move registered API routes into tagged domain routers without wrapping handlers."""

    api_routes: list[APIRoute] = []
    retained = []
    for route in app.router.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api"):
            api_routes.append(route)
        else:
            retained.append(route)
    app.router.routes = retained

    for route in api_routes:
        domain = _domain(route.path)
        route.tags = [domain]
        _DOMAIN_ROUTERS[domain].routes.append(route)
    for router in _DOMAIN_ROUTERS.values():
        app.include_router(router)


def domain_router_names() -> tuple[str, ...]:
    return tuple(_DOMAIN_ROUTERS)
