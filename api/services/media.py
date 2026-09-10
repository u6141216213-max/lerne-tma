import os
import datetime
import logging
import time
import json
from ..models import TMA_Deck, TMA_Card, TMAProgress, TMAReviewHistory, Deck, Card, tma_db, TMAMedia
from .. import srs
from peewee import fn, JOIN
from functools import lru_cache

logger = logging.getLogger(__name__)

_tma_settings_cache = {}
_tma_settings_cache_time = 0

def _get_cached_tma_settings():
    global _tma_settings_cache, _tma_settings_cache_time
    now = time.time()
    if now - _tma_settings_cache_time < 30 and _tma_settings_cache:
        return _tma_settings_cache
    try:
        from ..models import TMASetting
        new_cache = {}
        for s in TMASetting.select():
            new_cache[s.key] = s.value
        _tma_settings_cache = new_cache
        _tma_settings_cache_time = now
    except Exception as e:
        logger.error(f"Error fetching settings for ensure_card_audio: {e}")
    return _tma_settings_cache

def _build_media_exists_map(cards_dicts: list) -> set:
    """Собирает множество (filename, folder) существующих в TMAMedia записей.
    Один запрос вместо N проверок."""
    from ..models import TMAMedia
    
    filenames = set()
    for c in cards_dicts:
        for path_field, folder in [
            ('audio_path', 'audio'),
            ('audio_back_path', 'audio'),
            ('image_path', 'images'),
            ('video_front_path', 'videos'),
            ('video_back_path', 'videos')
        ]:
            path_str = c.get(path_field)
            if path_str and not path_str.startswith('http'):
                filenames.add(os.path.basename(path_str))
    
    if not filenames:
        return set()
    
    try:
        existing = set()
        filenames_list = list(filenames)
        for i in range(0, len(filenames_list), 500):
            chunk = filenames_list[i:i+500]
            for filename, folder in TMAMedia.select(TMAMedia.filename, TMAMedia.folder).where(TMAMedia.filename << chunk).tuples():
                existing.add((filename, folder))
        return existing
    except Exception as e:
        logger.error(f"Error in _build_media_exists_map: {e}")
        return set()


@lru_cache(maxsize=2000)
def _check_media_exists(filename: str, folder: str) -> bool:
    """Кэшированная проверка существования медиа в БД."""
    try:
        from ..models import TMAMedia
        return TMAMedia.select(TMAMedia.id).where(
            TMAMedia.filename == filename,
            TMAMedia.folder == folder
        ).exists()
    except Exception:
        return False


def resolve_media_url(path_str: str, media_type: str, exists_map: set = None) -> str | None:
    """Формирует канонический URL для медиа-ресурсов."""
    if not path_str:
        return None
    if path_str.startswith("http://") or path_str.startswith("https://"):
        return path_str
    if path_str.startswith("/api/media/"):
        return path_str
    
    filename = os.path.basename(path_str)
    if not filename:
        return None
    
    folder = "images"
    if media_type in ("audio", "audio_back"):
        folder = "audio"
    elif media_type in ("videos", "video_front", "video_back"):
        folder = "videos"
    elif media_type == "backgrounds":
        folder = "backgrounds"
    
    return f"/api/media/{folder}/{filename}"


async def ensure_card_audio(card, user_id: int):
    """Проверяет наличие озвучки для лицевой стороны карточки.
    Если файла озвучки нет в TMAMedia или он пустой/недействительный,
    генерирует новую озвучку через Edge TTS и сохраняет в БД.
    """
    import re
    from ..models import TMAMedia, TMASetting
    from ..utils.audio import generate_audio
    from .collaborative_service import can_edit_audio

    if not can_edit_audio(user_id, 'deck', card.deck_id):
        return
    
    # 1. Проверяем, есть ли уже озвучка
    has_valid_audio = False
    if card.audio_path:
        if card.audio_path.startswith("http"):
            has_valid_audio = True
        else:
            filename = os.path.basename(card.audio_path)
            # Проверяем только наличие записи по ID (без скачивания гигабайтных/мегабайтных BLOB из БД)
            has_valid_audio = TMAMedia.select(TMAMedia.id).where(
                (TMAMedia.filename == filename) & 
                (TMAMedia.folder == "audio")
            ).exists()
                
    if has_valid_audio:
        return
        
    # 2. Озвучки нет или она повреждена. Генерируем новую.
    if not card.front_text or not card.front_text.strip():
        return
        
    # Определяем язык по наличию кириллицы либо языку колоды
    if re.search(r'[а-яА-ЯёЁ]', card.front_text):
        lang = "ru"
    else:
        deck_lang = getattr(card, 'target_language', None)
        if not deck_lang and getattr(card, 'deck_id', None):
            from ..models import TMA_Deck
            deck_obj = TMA_Deck.get_or_none(TMA_Deck.id == card.deck_id)
            if deck_obj and deck_obj.target_language:
                deck_lang = deck_obj.target_language
        lang = (deck_lang or "de").lower().strip()
    
    # Загружаем настройки озвучки (с простым TTL кэшем)
    db_settings = _get_cached_tma_settings()
        
    voice = None
    rate = None
    
    # Маппинг голосов по умолчанию
    LANG_DEFAULT_VOICES = {
        "de": "de-DE-KatjaNeural",
        "ru": "ru-RU-SvetlanaNeural",
        "en": "en-US-JennyNeural",
        "no": "nb-NO-FinnNeural",
        "uk": "uk-UA-PolinaNeural",
    }
    
    clean_lang = (lang or "de").lower().strip()
    if clean_lang == "de":
        voice = db_settings.get("TTS_VOICE") or LANG_DEFAULT_VOICES["de"]
        rate = db_settings.get("TTS_SPEED") or "+0%"
    elif clean_lang == "ru":
        voice = db_settings.get("TTS_VOICE_RU") or LANG_DEFAULT_VOICES["ru"]
        rate = db_settings.get("TTS_SPEED_RU") or db_settings.get("TTS_SPEED") or "+0%"
    elif clean_lang in LANG_DEFAULT_VOICES:
        voice = db_settings.get(f"TTS_VOICE_{clean_lang.upper()}") or LANG_DEFAULT_VOICES[clean_lang]
        rate = db_settings.get("TTS_SPEED") or "+0%"
    else:
        voice = db_settings.get("TTS_VOICE") or LANG_DEFAULT_VOICES["de"]
        rate = db_settings.get("TTS_SPEED") or "+0%"
        
    try:
        # Генерируем аудио
        result = await generate_audio(card.front_text, voice=voice, rate=rate)
        if isinstance(result, tuple):
            result = result[0]
            
        if not result:
            logger.error(f"Failed to generate audio for card {card.id}")
            return
            
        if result.startswith("http"):
            # Облачная ссылка
            card.audio_path = result
            card.save()
            logger.info(f"Generated cloud audio for card {card.id}: {result}")
        else:
            # Локальный файл, сохраняем контент в БД
            filename = os.path.basename(result)
            with open(result, "rb") as f:
                content = f.read()
                
            TMAMedia.get_or_create(
                filename=filename,
                folder='audio',
                defaults={'content': content}
            )
            
            card.audio_path = filename
            card.save()
            
            try: os.remove(result)
            except Exception: pass
            
            logger.info(f"Generated local audio for card {card.id} and saved to TMAMedia: {filename}")
    except Exception as e:
        logger.error(f"Failed to ensure audio for card {card.id}: {e}", exc_info=True)
