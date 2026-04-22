"""Feature-scoped FastAPI routers.

Extracted from ``autonoma.api`` to keep the main module focused on
app/lifespan setup and cross-cutting concerns. Each router module
exports a ``router`` attribute that ``api.py`` mounts via
``app.include_router``.
"""
