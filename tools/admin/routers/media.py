"""Admin media and voice-preview router."""
import logging
import os

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse

from api import models

logger = logging.getLogger(__name__)
router = APIRouter()

VOICE_SAMPLE_PHRASES = {
    "de": "Hallo! Ich lerne Deutsch mit der Lerne App. Wie geht es dir heute?",
    "en": "Hello! I am learning languages with the Lerne App. How are you today?",
    "nb": "Hei! Jeg lærer språk med Lerne App. Hvordan har du det i dag?",
    "no": "Hei! Jeg lærer språk med Lerne App. Hvordan har du det i dag?",
    "uk": "Привіт! Я вивчаю іноземні мови разом з додатком Lerne. Як твої справи?",
    "ru": "Привет! Я изучаю иностранные языки вместе с приложением Lerne.",
}

# project_root for audio disk fallback
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@router.get("/api/admin/voice-preview")
async def get_voice_preview(
    voice: str = Query("de-DE-KatjaNeural"),
    text: str = Query(None),
    rate: str = Query("+0%")
):
    """Generates on-the-fly voice preview audio for the chosen TTS voice and speed rate."""
    from api.utils.audio import SUPPORTED_VOICES, _prepare_tts_text

    clean_voice = voice.strip()
    clean_voice = SUPPORTED_VOICES.get(clean_voice, clean_voice)
    if not clean_voice or clean_voice.lower() in ("none", "off", "no", "disabled", ""):
        clean_voice = "de-DE-KatjaNeural"

    clean_rate = (rate or "+0%").strip()
    if not clean_rate.startswith(("+", "-")):
        clean_rate = f"+{clean_rate}"
    if not clean_rate.endswith("%"):
        clean_rate = f"{clean_rate}%"

    if not text or not text.strip():
        prefix = clean_voice[:2].lower()
        phrase = VOICE_SAMPLE_PHRASES.get(prefix, "Hallo! Ich lerne Sprachen mit der Lerne App.")
    else:
        phrase = _prepare_tts_text(text) or text.strip()

    try:
        import edge_tts
        communicate = edge_tts.Communicate(phrase, clean_voice, rate=clean_rate)
        audio_chunks = []
        async for event in communicate.stream():
            if event["type"] == "audio":
                audio_chunks.append(event["data"])

        audio_bytes = b"".join(audio_chunks)
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="Failed to synthesize voice preview")

        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-cache",
                "Content-Disposition": f'inline; filename="preview_{clean_voice}.mp3"'
            }
        )
    except Exception as e:
        logger.error(f"Voice preview error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Voice preview error: {str(e)}")


@router.get("/api/media/audio/{filename:path}")
def get_admin_audio(filename: str):
    """Serves audio files from TMAMedia or pending audio cache for admin preview."""
    clean_name = os.path.basename(filename)
    media = models.TMAMedia.get_or_none(
        (models.TMAMedia.filename == clean_name) &
        (models.TMAMedia.folder == 'audio')
    )
    if media and media.content:
        return Response(
            content=bytes(media.content),
            media_type="audio/mpeg",
            headers={"Cache-Control": "public, max-age=604800"}
        )

    local_path = os.path.join(_PROJECT_ROOT, "user_files", "pending_audio", clean_name)
    if os.path.exists(local_path):
        return FileResponse(local_path, media_type="audio/mpeg")

    raise HTTPException(status_code=404, detail="Audio file not found")
