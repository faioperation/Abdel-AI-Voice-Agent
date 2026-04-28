from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import uvicorn
from fastapi.staticfiles import StaticFiles
from app.routes import auth_router, assistants_router, calls_router, chat_router, telephony_router, billing_router, orders_router
from app.database import init_db

# Removed init_db() from global scope to prevent Vercel build timeouts

import os

app = FastAPI(title="Pizzeria Network AI Dashboard")
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],)

app.include_router(auth_router)
app.include_router(assistants_router)
app.include_router(calls_router)
app.include_router(chat_router)
app.include_router(telephony_router)
app.include_router(billing_router)
app.include_router(orders_router)

from fastapi.responses import HTMLResponse
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return Path("templates/index.html").read_text(encoding="utf-8")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)