"""Admin cards router."""
import datetime
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from api import models
from tools.admin.schemas import BulkCreateCardsRequest, SuggestWordsRequest, UpdateCardRequest
from tools.admin.services.card_helpers import save_audio_to_db_or_cloud
from tools.admin.services.regen_worker import regen_tasks, run_bulk_card_creation

import time

logger = logging.getLogger(__name__)
router = APIRouter()


@router.put("/api/admin/cards/{card_id}")
def update_card_endpoint(card_id: int, req: UpdateCardRequest):
    """Updates fields of a specific card (in TMA_Card or Card)."""
    now = datetime.datetime.now()
    tma_card = models.TMA_Card.get_or_none(models.TMA_Card.id == card_id)
    if tma_card:
        if req.front_text is not None:
            tma_card.front_text = req.front_text
        if req.back_text is not None:
            tma_card.back_text = req.back_text
        if req.context is not None:
            tma_card.context = req.context
        if req.tags is not None:
            tma_card.tags = req.tags
        tma_card.updated_at = now
        tma_card.save()
        return {"status": "ok", "card_id": card_id, "type": "tma"}

    lib_card = models.Card.get_or_none(models.Card.id == card_id)
    if lib_card:
        if req.front_text is not None:
            lib_card.front_text = req.front_text
        if req.back_text is not None:
            lib_card.back_text = req.back_text
        if req.context is not None:
            lib_card.context = req.context
        if req.tags is not None:
            lib_card.tags = req.tags
        lib_card.updated_at = now
        lib_card.save()
        return {"status": "ok", "card_id": card_id, "type": "library"}

    raise HTTPException(status_code=404, detail="Card not found")


@router.delete("/api/admin/cards/{card_id}")
def delete_card_endpoint(card_id: int):
    """Soft deletes a card."""
    now = datetime.datetime.now()
    tma_card = models.TMA_Card.get_or_none(models.TMA_Card.id == card_id)
    if tma_card:
        tma_card.is_deleted = True
        tma_card.updated_at = now
        tma_card.save()
        return {"status": "ok", "card_id": card_id}

    lib_card = models.Card.get_or_none(models.Card.id == card_id)
    if lib_card:
        lib_card.is_deleted = True
        lib_card.updated_at = now
        lib_card.save()
        return {"status": "ok", "card_id": card_id}

    raise HTTPException(status_code=404, detail="Card not found")


@router.post("/api/admin/cards/{card_id}/synthesize-audio")
async def synthesize_single_card_audio(
    card_id: int,
    voice: Optional[str] = "de-DE-KatjaNeural",
    rate: Optional[str] = "+0%"
):
    """Synthesizes TTS audio for a single card on-the-fly and saves it."""
    card = models.TMA_Card.get_or_none(models.TMA_Card.id == card_id)
    if not card:
        card = models.Card.get_or_none(models.Card.id == card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    front = (card.front_text or "").strip()
    if not front:
        raise HTTPException(status_code=400, detail="Card front text is empty")

    from api.utils.audio import generate_audio
    res_audio = await generate_audio(front, voice=voice or "de-DE-KatjaNeural", rate=rate or "+0%")
    if isinstance(res_audio, tuple):
        res_audio = res_audio[0]

    if not res_audio:
        raise HTTPException(status_code=500, detail="TTS generation failed")

    saved_audio = save_audio_to_db_or_cloud(res_audio)
    if saved_audio:
        card.audio_path = saved_audio
        card.updated_at = datetime.datetime.now()
        card.save()
        return {"status": "ok", "card_id": card_id, "audio_path": saved_audio}

    raise HTTPException(status_code=500, detail="Failed to save audio file")


@router.post("/api/admin/cards/bulk-create")
def start_bulk_card_creation_endpoint(req: BulkCreateCardsRequest, background_tasks: BackgroundTasks):
    """Starts background bulk card creation + optional AI and TTS generation."""
    if not req.phrases:
        raise HTTPException(status_code=400, detail="Phrases list is empty")

    task_id = f"bulk_create_{int(time.time())}"
    task_data = {
        "task_id": task_id,
        "is_batch": True,
        "task_type": "bulk_create",
        "status": "pending",
        "control": "run",
        "total_decks": 1,
        "processed_decks": 0,
        "current_deck_id": str(req.deck_id or ""),
        "current_deck_name": req.new_deck_name or "",
        "total_cards": len(req.phrases),
        "processed_cards": 0,
        "current_card": "",
        "logs": [],
        "dry_run_results": [],
        "is_dry_run": False,
        "is_audio_only": False,
        "voice": req.voice,
        "options": req.dict(),
        "start_time": time.time()
    }
    regen_tasks[task_id] = task_data

    from tools.admin.services import task_manager
    task_manager.register_task(task_id, task_data)
    background_tasks.add_task(run_bulk_card_creation, task_id, req)
    return {"status": "ok", "task_id": task_id, "total_cards": len(req.phrases), "message": "Bulk card creation started"}


@router.post("/api/admin/cards/bulk-suggest-words")
async def suggest_topic_words_endpoint(req: SuggestWordsRequest):
    """Generates a list of words/phrases for a given topic and level using AI."""
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic cannot be empty")

    count = max(5, min(req.count or 20, 60))
    lvl = req.level or "A1"
    target_lang = req.target_lang or "de"

    system_prompt = (
        f"You are an expert language teacher. Generate a clean list of exactly {count} essential vocabulary words "
        f"and useful short phrases for the topic '{topic}' in target language '{target_lang}' appropriate for CEFR level {lvl}. "
        f"Return ONLY a valid JSON array of strings, without explanations, markdown or extra text. Example format: [\"das Wort 1\", \"die Phrase 2\"]."
    )

    try:
        from api.ai_service import get_ai_config, AIService
        provider, ai_key, ai_model = get_ai_config()
        if not ai_key and provider != "ollama":
            return {"words": [f"{topic} - слово 1", f"{topic} - слово 2"], "count": 2, "topic": topic, "warning": "AI key not configured"}

        client = AIService(provider=provider, api_key=ai_key)
        resp, success = await client.chat_completion(
            system_prompt=system_prompt,
            user_message=f"Topic: {topic}, Level: {lvl}, Count: {count}",
            model=ai_model
        )

        if success and resp:
            cleaned = resp.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

            import json as _j
            words = _j.loads(cleaned)
            if isinstance(words, list):
                clean_words = [str(w).strip() for w in words if str(w).strip()]
                return {"words": clean_words, "count": len(clean_words), "topic": topic}
    except Exception as e:
        logger.error(f"Error suggesting topic words: {e}")

    return {
        "words": [], "count": 0, "topic": topic,
        "error": "Не удалось автоматически сгенерировать слова через ИИ. Введите слова вручную."
    }
