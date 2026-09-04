"""HTTP-слой (FastAPI) для веб-фронтенда."""

from .server import app, build_runtime, create_app

__all__ = ["app", "build_runtime", "create_app"]
