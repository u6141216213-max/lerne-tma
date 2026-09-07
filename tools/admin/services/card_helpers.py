"""Card-level helper utilities for the Admin Panel."""
import os
import logging
from typing import Optional

from api import models

logger = logging.getLogger(__name__)


def save_audio_to_db_or_cloud(res_audio: str) -> Optional[str]:
    """Ensures audio file bytes are stored in TMAMedia or returns cloud URL."""
    if not res_audio:
        return None
    if res_audio.startswith("http"):
        return res_audio

    filename = os.path.basename(res_audio)
    if os.path.exists(res_audio):
        try:
            with open(res_audio, "rb") as f:
                content = f.read()
            models.TMAMedia.get_or_create(
                filename=filename,
                folder='audio',
                defaults={'content': content}
            )
            try:
                os.remove(res_audio)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error saving audio to TMAMedia: {e}")
    return filename


def card_has_valid_audio(card) -> bool:
    if not card.audio_path or not str(card.audio_path).strip():
        return False
    if card.audio_path.startswith("http"):
        return True
    filename = os.path.basename(card.audio_path)
    try:
        return models.TMAMedia.select(models.TMAMedia.id).where(
            (models.TMAMedia.filename == filename) &
            (models.TMAMedia.folder == 'audio')
        ).exists()
    except Exception:
        return False


def card_is_fully_completed(card) -> bool:
    """Returns True if card has non-empty front, non-empty back, complete rich context
    (with 🎯 and 📖 for quiz/exam), and valid audio."""
    front = (getattr(card, 'front_text', '') or '').strip()
    back = (getattr(card, 'back_text', '') or '').strip()
    ctx = (getattr(card, 'context', '') or '').strip()
    has_audio = card_has_valid_audio(card)

    if not (front and back and ctx and has_audio):
        return False

    card_type = (getattr(card, 'card_type', '') or '').lower()
    is_quiz = card_type == 'quiz' or '?' in front or '\n*' in front or len(front.split('\n')) >= 3
    if is_quiz:
        if not ('🎯' in ctx and '📖' in ctx):
            return False

    return True
