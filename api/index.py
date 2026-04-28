"""
Vercel entry point for Pizzeria Network AI Dashboard.
Vercel expects a WSGI/ASGI app at api/index.py.
"""
import sys
import os

# Add root to path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from main import app  # FastAPI app

# Vercel Python runtime calls this as a handler
handler = app