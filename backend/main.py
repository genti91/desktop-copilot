"""Compatibility entry point for Uvicorn: `uvicorn main:app`."""

from app.main import app

__all__ = ["app"]
