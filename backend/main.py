"""
Backward-compatible entry point.
Run with: uvicorn main:app --reload
All application logic is in app/main.py.
"""

from app.main import app  # noqa: F401