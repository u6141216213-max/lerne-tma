"""Lerne TMA Admin Console — FastAPI application entry point.

All route logic lives in tools/admin/routers/. All business logic lives in tools/admin/services/.
This file only wires the app together: middleware, startup, static mount, and include_router calls.
"""
import os
import sys
import logging
import threading
import webbrowser

# Add project root to sys.path so `api` and `tools` packages are importable.
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api import models

logger = logging.getLogger(__name__)

app = FastAPI(title="Lerne TMA Admin Console", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_db():
    if not models.tma_db.obj:
        models.initialize_database()


# Serve the React/Vite admin UI
_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/")
def get_admin_ui():
    index_path = os.path.join(_static_dir, "index.html")
    if os.path.exists(index_path):
        resp = FileResponse(index_path)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    return JSONResponse({"message": "Admin UI index.html not found"}, status_code=404)


# ─── Register routers ─────────────────────────────────────────────────────────
from tools.admin.routers import prompts, users, decks, cards, folders, tasks, classification, media, backups  # noqa: E402

app.include_router(prompts.router)
app.include_router(users.router)
app.include_router(decks.router)
app.include_router(cards.router)
app.include_router(folders.router)
app.include_router(tasks.router)
app.include_router(classification.router)
app.include_router(media.router)
app.include_router(backups.router)


# ─── Dev launch ──────────────────────────────────────────────────────────────
def _open_browser():
    import time
    time.sleep(1.2)
    try:
        webbrowser.open("http://127.0.0.1:8050")
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run("tools.admin.server:app", host="127.0.0.1", port=8050, reload=True)
