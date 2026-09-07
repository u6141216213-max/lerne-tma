import os
import sys
import logging


# ВАЖНО: Добавляем корень проекта в пути поиска модулей
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.append(project_root)
if current_dir not in sys.path:
    sys.path.append(current_dir)

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from api.dependencies.auth import get_user_id

from api import models, services

# Импорт роутеров
from api.routers import decks, cards, study, settings, ai, media, bot, feedback, auth, auth_v2, share, debug, trash, sync, folders, collaborative, lid

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app):
    """Initialize database on startup (traditional servers)."""
    if not models.tma_db.obj:
        models.initialize_database()
        models.create_all_tables()
    logger.info("DATABASE: Startup initialization complete.")
    yield

# Also initialize at module level for Vercel serverless cold starts
try:
    models.initialize_database()
    models.create_all_tables()
    logger.info("DATABASE: Module-level initialization complete.")
except Exception as _db_init_err:
    logger.error(f"DATABASE: Module-level initialization FAILED: {_db_init_err}")

app = FastAPI(title="Lerne TMA API", lifespan=lifespan)

# CORS
cors_origins_env = os.environ.get("CORS_ORIGINS", "")
if cors_origins_env:
    allowed_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
else:
    allowed_origins = [
        "https://localhost",
        "http://localhost",
        "capacitor://localhost",
        "ionic://localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "https://tma-amber.vercel.app",
        "https://web.telegram.org",
        "https://telegram.org",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"^(https?://.*\.vercel\.app|https?://localhost(:[0-9]+)?|capacitor://localhost|ionic://localhost)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Управление жизненным циклом соединений с базой данных (Peewee Connection Pool)
@app.middleware("http")
async def db_session_middleware(request, call_next):
    try:
        if hasattr(models.tma_db, 'obj') and models.tma_db.obj is not None:
            if models.tma_db.is_closed():
                models.tma_db.connect(reuse_if_open=True)
        if hasattr(models.lerne_db, 'obj') and models.lerne_db.obj is not None:
            if models.lerne_db.is_closed():
                models.lerne_db.connect(reuse_if_open=True)
    except Exception as e:
        logger.error(f"Error checking DB connection before request: {e}")
        try:
            models.initialize_database()
        except Exception as e2:
            logger.error(f"CRITICAL: DB initialize failed: {e2}")

    response = None
    try:
        response = await call_next(request)
    except Exception as exc:
        exc_str = str(exc).lower()
        if request.method in ('GET', 'HEAD') and any(k in exc_str for k in ["closed", "terminated", "connection", "socket", "reset", "eof", "exceeded maximum connections"]):
            logger.warning(f"DB connection reset due to error: {exc}. Retrying HTTP request...")
            try:
                if hasattr(models.tma_db, 'obj') and models.tma_db.obj:
                    models.tma_db.close()
                    models.tma_db.connect(reuse_if_open=True)
            except Exception:
                pass
            try:
                response = await call_next(request)
            except Exception as retry_exc:
                logger.error(f"Retry HTTP request failed: {retry_exc}")
                raise retry_exc
        else:
            raise exc
    finally:
        # Crucial: Always release DB connections back to the pool after the HTTP request is finished
        try:
            if hasattr(models.tma_db, 'obj') and models.tma_db.obj is not None:
                if not models.tma_db.is_closed():
                    models.tma_db.close()
        except Exception:
            pass
        try:
            if hasattr(models.lerne_db, 'obj') and models.lerne_db.obj is not None:
                if not models.lerne_db.is_closed():
                    models.lerne_db.close()
        except Exception:
            pass

    if response:
        path = request.url.path
        if path.startswith("/api") and not path.startswith("/api/media"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
    return response

# Подключение роутеров
app.include_router(decks.router, prefix="/api")
app.include_router(cards.router, prefix="/api")
app.include_router(study.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(media.router, prefix="/api")
app.include_router(bot.router, prefix="/api")
app.include_router(feedback.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(auth_v2.router, prefix="/api")
app.include_router(share.router, prefix="/api")
app.include_router(debug.router, prefix="/api")
app.include_router(trash.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(folders.router, prefix="/api")
app.include_router(collaborative.router, prefix="/api")
app.include_router(lid.router, prefix="/api")

# --- Consolidated Init Endpoint ---
@app.get("/api/init")
def get_init_data(user_id: int = Depends(get_user_id)):
    """Returns all initial data needed by the app in a single request."""
    # 1. Ensure starter/default decks and folders are provisioned for this user FIRST
    try:
        services.ensure_starter_decks(user_id)
    except Exception as e:
        logger.error(f"Error ensuring starter decks for user {user_id} in /api/init: {e}")

    # 2. Build folder_map AFTER ensuring starter decks so newly created folders are included
    all_folders = list(models.TMA_Folder.select().where(models.TMA_Folder.is_deleted == False))
    folder_map = {f.id: f for f in all_folders}

    decks = services.get_active_decks(user_id, folder_map=folder_map)
    folders = services.get_active_folders(user_id, folder_map=folder_map)
    
    # Get settings
    settings = {}
    try:
        for s in models.TMASetting.select():
            settings[s.key] = s.value
    except Exception: pass
        
    # Get prompts
    prompts = []
    try:
        for cp in models.TMACustomPrompt.select().where(models.TMACustomPrompt.user_id == user_id):
            prompts.append({
                "id": cp.id,
                "name": cp.name,
                "translation_prompt": cp.translation_prompt or "",
                "context_prompt": cp.context_prompt or "",
                "is_active": cp.is_active,
                "prompt_type": cp.prompt_type
            })
    except Exception: pass
        
    # Get user language and profile
    user_info = {"active_language": "de", "native_language": "uk", "has_selected_language": False}
    try:
        user = models.TMAUser.get_or_none(models.TMAUser.user_id == user_id)
        if user:
            clean_first_name = user.first_name if (user.first_name and user.first_name != "Пользователь") else (user.username or None)
            has_identity = bool(clean_first_name or user.username)
            is_guest = bool(user.is_guest) if has_identity else True
            user_info = {
                "user_id": user.user_id,
                "first_name": clean_first_name,
                "last_name": user.last_name,
                "username": user.username,
                "photo_url": user.photo_url,
                "phone": user.phone,
                "is_guest": is_guest,
                "active_language": user.active_language or "de",
                "native_language": getattr(user, 'native_language', None) or "uk",
                "has_selected_language": bool(user.has_selected_language)
            }
    except Exception: pass

    return {
        "decks": decks,
        "folders": folders,
        "settings": settings,
        "prompts": prompts,
        "user_info": user_info
    }

# --- Базовые Эндпоинты ---
@app.get("/api/health")
def health_check():
    try:
        models.tma_db.connect(reuse_if_open=True)
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok",
        "database_connected": db_ok,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
