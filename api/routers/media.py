import io
import os
import re
import uuid
import hashlib
from fastapi import APIRouter, HTTPException, Depends, Body, Query, Response, UploadFile, File, Request
import logging
from PIL import Image, UnidentifiedImageError

import models
from api import models # Ensure we use the api.models package
from api.dependencies.auth import get_user_id
from api.utils.image import optimize_image # Импортируем наш оптимизатор

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/media",
    tags=["media"],
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024 # Увеличим лимит, так как мы всё равно сожмем
SUPPORTED_IMAGE_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}

LANG_DEFAULT_VOICES = {
    "de": "de-DE-KatjaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "en": "en-US-JennyNeural",
    "no": "nb-NO-FinnNeural",
    "uk": "uk-UA-PolinaNeural",
}

RATE_RE = re.compile(r"^[+-]\d{1,3}%$")


def _normalize_tts_rate(value: str | None) -> str | None:
    if value is None:
        return None
    rate_value = str(value).strip()
    if not RATE_RE.match(rate_value):
        return None
    numeric = max(-100, min(100, int(rate_value[:-1])))
    return f"{numeric:+d}%"


def _clean_param(value, default=None):
    return value if isinstance(value, str) and value.strip() else default


def get_range_response(request: Request, content: bytes, media_type: str) -> Response:
    """Helper to handle HTTP 206 Partial Content for media streaming."""
    file_size = len(content)
    range_header = request.headers.get("range")
    etag = f'"{hashlib.md5(content[:1024]).hexdigest()}"'
    
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
        "Cache-Control": "public, max-age=604800",
        "ETag": etag,
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
    }

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cors_headers)

    if not range_header:
        return Response(
            content=content,
            media_type=media_type,
            headers=cors_headers
        )

    try:
        # Expected format: bytes=0-1024 or bytes=1024-
        byte_range = range_header.replace("bytes=", "").split("-")
        start = int(byte_range[0]) if byte_range[0] else 0
        end = int(byte_range[1]) if len(byte_range) > 1 and byte_range[1] else file_size - 1
        
        if start >= file_size or end >= file_size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}", "Access-Control-Allow-Origin": "*"})
            
        chunk = content[start:end+1]
        
        range_cors_headers = {
            **cors_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(len(chunk)),
        }
        return Response(
            content=chunk,
            status_code=206,
            media_type=media_type,
            headers=range_cors_headers
        )
    except Exception:
        return Response(
            content=content,
            media_type=media_type,
            headers=cors_headers
        )

@router.post("/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    user_id: int = Depends(get_user_id)
):
    """Upload an image, optimize it to WebP and store it in TMAMedia."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Image file is empty")
    
    # Мы всё равно проверяем формат перед сжатием
    try:
        image = Image.open(io.BytesIO(content))
        image_format = image.format
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="Unsupported image file")

    if image_format not in SUPPORTED_IMAGE_FORMATS:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, WEBP and GIF images are supported")

    # --- ОПТИМИЗАЦИЯ ---
    # Вызываем наш оптимизатор. Он вернет сжатые байты и новый MIME-тип (image/webp)
    optimized_content, media_type = optimize_image(content)
    filename = f"upload_{user_id}_{uuid.uuid4().hex[:12]}.webp" # Всегда .webp

    try:
        models.TMAMedia.create(
            filename=filename,
            folder='images',
            content=optimized_content
        )
    except Exception as e:
        logger.error(f"Image upload save error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save image")

    return {
        "path": f"images/{filename}",
        "url": f"/api/media/images/{filename}",
        "media_type": media_type,
        "original_size": len(content),
        "optimized_size": len(optimized_content)
    }

@router.post("/generate-audio")
async def generate_audio_endpoint(
    data: dict = Body(None), 
    text: str = Query(None),
    lang: str = Query("de"),
    voice: str = Query(None),
    rate: str = Query(None),
    user_id: int = Depends(get_user_id)
):
    """Генерация озвучки через Edge TTS и загрузка в облако."""
    with_boundaries = False
    if data:
        text = data.get('text') if data.get('text') is not None else text
        lang = data.get('lang') if data.get('lang') is not None else lang
        voice = data.get('voice') if data.get('voice') is not None else voice
        rate = data.get('rate') if data.get('rate') is not None else rate
        with_boundaries = bool(data.get('with_boundaries', False))

    text = _clean_param(text)
    lang = _clean_param(lang, "de")
    voice = _clean_param(voice)
    rate = _clean_param(rate)
    
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    # 1. Пытаемся получить настройки из БД
    db_settings = {}
    try:
        for s in models.TMASetting.select():
            db_settings[s.key] = s.value
    except Exception as e:
        logger.error(f"Error fetching settings for audio: {e}")

    # 2. Определяем голос
    if not voice:
        clean_lang = (lang or "de").lower().strip()
        if clean_lang == "de":
            voice = db_settings.get("TTS_VOICE") or LANG_DEFAULT_VOICES["de"]
        elif clean_lang == "ru":
            voice = db_settings.get("TTS_VOICE_RU") or LANG_DEFAULT_VOICES["ru"]
        elif clean_lang == "no":
            voice = db_settings.get("TTS_VOICE_NO") or LANG_DEFAULT_VOICES["no"]
        elif clean_lang == "uk":
            voice = db_settings.get("TTS_VOICE_UK") or LANG_DEFAULT_VOICES["uk"]
        elif clean_lang == "en":
            voice = db_settings.get("TTS_VOICE_EN") or LANG_DEFAULT_VOICES["en"]
        else:
            voice = db_settings.get(f"TTS_VOICE_{clean_lang.upper()}") or LANG_DEFAULT_VOICES.get(clean_lang, LANG_DEFAULT_VOICES["de"])
            
    # 3. Определяем скорость
    if not rate:
        if lang == "de":
            rate = db_settings.get("TTS_SPEED")
        elif lang == "ru":
            rate = db_settings.get("TTS_SPEED_RU") or db_settings.get("TTS_SPEED")
        else:
            rate = db_settings.get("TTS_SPEED")
    rate = _normalize_tts_rate(rate) or "+0%"
    
    logger.info(f"AUDIO GENERATION START: Text='{text[:30]}...', Voice={voice}, Rate={rate}, Boundaries={with_boundaries}")
            
    try:
        from api.utils import audio
        result, word_boundaries = await audio.generate_audio(
            text, voice=voice, rate=rate, with_boundaries=with_boundaries
        )
        
        if not result:
            raise HTTPException(status_code=500, detail="Failed to generate audio")
            
        if result.startswith("http"):
            return {
                "path": result,
                "url": result,
                "word_boundaries": word_boundaries
            }
        
        try:
            filename = os.path.basename(result)
            with open(result, "rb") as f:
                content = f.read()
            
            models.TMAMedia.get_or_create(
                filename=filename,
                folder='audio',
                defaults={'content': content}
            )
            
            try: os.remove(result)
            except Exception: pass
            
            return {
                "path": filename,
                "url": f"/api/media/audio/{filename}",
                "word_boundaries": word_boundaries
            }
        except Exception as db_err:
            logger.error(f"DATABASE SAVE ERROR for audio: {db_err}")
            raise HTTPException(status_code=500, detail=f"Database Save Error: {str(db_err)}")
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        logger.error(f"TTS generation error: {e}\n{err_msg}")
        raise HTTPException(status_code=500, detail=f"TTS Error: {str(e)}")


@router.post("/generate-card-audio")
async def generate_card_audio_endpoint(
    data: dict = Body(...),
    user_id: int = Depends(get_user_id),
):
    """Generate and attach audio without granting general card edit access."""
    card_id = data.get('card_id')
    side = data.get('side', 'front')
    if not card_id or side not in ('front', 'back'):
        raise HTTPException(status_code=400, detail="card_id and a valid side are required")

    card = models.TMA_Card.get_or_none(
        (models.TMA_Card.id == card_id) & (models.TMA_Card.is_deleted == False)
    )
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    from api.services.collaborative_service import can_edit_audio, get_effective_user_role
    if not get_effective_user_role(user_id, 'deck', card.deck_id):
        raise HTTPException(status_code=403, detail="Access denied")
    if not can_edit_audio(user_id, 'deck', card.deck_id):
        raise HTTPException(status_code=403, detail="Audio editing is not allowed for this shared item")

    text_value = data.get('text')
    if not isinstance(text_value, str) or not text_value.strip():
        text_value = card.front_text if side == 'front' else card.back_text
    if not text_value or not text_value.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    lang = _clean_param(data.get('lang'), 'de')
    voice = _clean_param(data.get('voice'))
    rate = _normalize_tts_rate(_clean_param(data.get('rate'))) or '+0%'
    if not voice:
        settings = {s.key: s.value for s in models.TMASetting.select()}
        clean_lang = lang.lower().split('-')[0]
        voice = settings.get(f'TTS_VOICE_{clean_lang.upper()}') or settings.get('TTS_VOICE') or LANG_DEFAULT_VOICES.get(clean_lang, LANG_DEFAULT_VOICES['de'])
        rate = _normalize_tts_rate(settings.get('TTS_SPEED_RU' if clean_lang == 'ru' else 'TTS_SPEED')) or rate

    try:
        from api.utils.audio import generate_audio
        result = await generate_audio(text_value.strip(), voice=voice, rate=rate)
        result = result[0] if isinstance(result, tuple) else result
        if not result:
            raise HTTPException(status_code=500, detail="Failed to generate audio")

        if result.startswith('http'):
            path = result
            url = result
        else:
            filename = os.path.basename(result)
            with open(result, 'rb') as audio_file:
                content = audio_file.read()
            models.TMAMedia.get_or_create(filename=filename, folder='audio', defaults={'content': content})
            try:
                os.remove(result)
            except OSError:
                pass
            path = filename
            url = f'/api/media/audio/{filename}'

        field = 'audio_back_path' if side == 'back' else 'audio_path'
        setattr(card, field, path)
        card.updated_at = models.datetime.datetime.now()
        card.save(only=[getattr(models.TMA_Card, field), models.TMA_Card.updated_at])
        return {'path': path, 'url': url}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error('Card audio generation failed for card %s: %s', card_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate and save card audio")



@router.post("/upload-audio")
async def upload_audio_file(
    file: UploadFile = File(...),
    user_id: int = Depends(get_user_id)
):
    """Upload an audio file for a deck or card."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    
    # 20MB max
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio is too large. Maximum size is 20 MB")

    filename = f"aud_{user_id}_{uuid.uuid4().hex[:12]}.mp3"
    try:
        models.TMAMedia.create(
            filename=filename,
            folder='audio',
            content=content
        )
    except Exception as e:
        logger.error(f"Audio upload error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save audio")

    return {
        "path": f"audio/{filename}",
        "url": f"/api/media/audio/{filename}"
    }

from collections import OrderedDict

class MediaMemoryCache:
    def __init__(self, max_items=250, max_bytes=64 * 1024 * 1024):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self.cache = OrderedDict() # (folder, filename) -> (bytes, media_type)
        self.current_bytes = 0

    def get(self, folder: str, filename: str):
        key = (folder, filename)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, folder: str, filename: str, content: bytes, media_type: str = None):
        key = (folder, filename)
        size = len(content)
        if size > 10 * 1024 * 1024:
            return
        
        if key in self.cache:
            old_content, _ = self.cache[key]
            self.current_bytes -= len(old_content)
            del self.cache[key]

        while self.cache and (len(self.cache) >= self.max_items or self.current_bytes + size > self.max_bytes):
            _, (old_c, _) = self.cache.popitem(last=False)
            self.current_bytes -= len(old_c)

        self.cache[key] = (content, media_type)
        self.current_bytes += size

_media_cache = MediaMemoryCache()


@router.get("/audio/{filename:path}")
def get_audio(filename: str, request: Request):
    clean_filename = os.path.basename(filename)
    cached = _media_cache.get('audio', clean_filename)
    if cached:
        content, _ = cached
        return get_range_response(request, content, "audio/mpeg")

    logger.debug(f"MEDIA: Requesting audio: {clean_filename}")
    media = models.TMAMedia.get_or_none(
        (models.TMAMedia.filename == clean_filename) & 
        (models.TMAMedia.folder == 'audio')
    )
    if not media:
        raise HTTPException(status_code=404, detail="Audio not found in DB")
    
    content = bytes(media.content)
    _media_cache.set('audio', clean_filename, content, "audio/mpeg")
    return get_range_response(request, content, "audio/mpeg")

@router.get("/images/{filename:path}")
def get_image(filename: str, request: Request):
    clean_filename = os.path.basename(filename)
    cached = _media_cache.get('images', clean_filename)
    if cached:
        content, media_type = cached
        return get_range_response(request, content, media_type)

    logger.debug(f"MEDIA: Requesting image: {clean_filename}")
    media = models.TMAMedia.get_or_none(
        (models.TMAMedia.filename == clean_filename) & 
        (models.TMAMedia.folder == 'images')
    )
    if not media:
        raise HTTPException(status_code=404, detail="Image not found in DB")
    
    content = bytes(media.content)
    
    # Detect exact magic bytes to guarantee valid browser rendering
    if content.startswith(b'\xff\xd8\xff'):
        media_type = "image/jpeg"
    elif content.startswith(b'\x89PNG\r\n\x1a\n'):
        media_type = "image/png"
    elif content.startswith(b'RIFF') and b'WEBP' in content[:16]:
        media_type = "image/webp"
    elif content.startswith(b'GIF8'):
        media_type = "image/gif"
    else:
        ext = clean_filename.split('.')[-1].lower()
        media_type = f"image/{ext}" if ext != 'jpg' else "image/jpeg"
    
    _media_cache.set('images', clean_filename, content, media_type)
    return get_range_response(request, content, media_type)

@router.post("/upload-video")
async def upload_video(
    file: UploadFile = File(...),
    user_id: int = Depends(get_user_id)
):
    """Upload a video for a card."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Video file is empty")
    
    # 20MB max for now
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Video is too large. Maximum size is 20 MB")

    filename = f"vid_{user_id}_{uuid.uuid4().hex[:12]}.mp4"
    try:
        models.TMAMedia.create(
            filename=filename,
            folder='videos',
            content=content
        )
    except Exception as e:
        logger.error(f"Video upload error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save video")

    return {
        "path": f"videos/{filename}",
        "url": f"/api/media/videos/{filename}"
    }

@router.post("/upload-background")
async def upload_background(
    file: UploadFile = File(...),
    user_id: int = Depends(get_user_id)
):
    """Upload a custom background video."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Background file is empty")
    
    filename = f"bg_{user_id}_{uuid.uuid4().hex[:12]}.mp4"
    try:
        models.TMAMedia.create(
            filename=filename,
            folder='backgrounds',
            content=content
        )
    except Exception as e:
        logger.error(f"Background upload error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save background")

    return {
        "path": f"backgrounds/{filename}",
        "url": f"/api/media/backgrounds/{filename}"
    }

@router.get("/backgrounds")
def list_backgrounds(user_id: int = Depends(get_user_id)):
    """List all custom background videos."""
    query = models.TMAMedia.select(models.TMAMedia.filename).where(models.TMAMedia.folder == 'backgrounds')
    return [
        {"filename": m.filename, "url": f"/api/media/backgrounds/{m.filename}"}
        for m in query
    ]

@router.get("/videos/{filename}")
def get_video(filename: str, request: Request):
    media = models.TMAMedia.get_or_none(
        models.TMAMedia.filename == filename, 
        models.TMAMedia.folder == 'videos'
    )
    if not media:
        raise HTTPException(status_code=404, detail="Video not found")
    
    content = bytes(media.content)
    return get_range_response(request, content, "video/mp4")

@router.get("/backgrounds/{filename}")
def get_background_video(filename: str, request: Request):
    media = models.TMAMedia.get_or_none(
        models.TMAMedia.filename == filename, 
        models.TMAMedia.folder == 'backgrounds'
    )
    if not media:
        raise HTTPException(status_code=404, detail="Background not found")
    
    content = bytes(media.content)
    return get_range_response(request, content, "video/mp4")
