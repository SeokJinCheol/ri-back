"""Compatibility entry point for `uvicorn main:app --reload`."""

from app.main import app

__all__ = ["app"]
