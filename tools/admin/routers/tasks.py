"""Admin tasks router — checkpoint, resume, retry-failed."""
import logging
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api import models
from tools.admin.schemas import (
    BatchRegenerateAudioRequest,
    BatchRegenerateDeckRequest,
    BulkCreateCardsRequest,
    ClassificationRequest,
    RegenerateAudioRequest,
    RegenerateDeckRequest,
    RetryFailedCardsRequest,
    ResumeTaskRequest,
)
from tools.admin.services.card_helpers import card_is_fully_completed
from tools.admin.services.deck_helpers import get_deck_and_cards
from tools.admin.services.regen_worker import (
    regen_tasks,
    run_ai_regeneration,
    run_audio_regeneration,
    run_batch_ai_regeneration,
    run_batch_audio_regeneration,
    run_bulk_card_creation,
)
from tools.admin.services.classification_worker import run_classification_task
from tools.admin.services import task_manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/admin/tasks/checkpoint")
def get_task_checkpoint():
    """Returns the last saved checkpoint state and whether it can be resumed or retried."""
    ckpt = task_manager.load_task_checkpoint()
    if not ckpt:
        return {"has_checkpoint": False, "checkpoint": None, "can_resume": False, "has_failed": False, "failed_count": 0}

    status = ckpt.get("status", "")
    failed_ids = ckpt.get("failed_card_ids") or []
    has_failed = len(failed_ids) > 0 or (
        int(ckpt.get("total_cards", 0) or 0) > int(ckpt.get("processed_cards", 0) or 0) and status != "completed"
    )
    can_resume = status in ("paused", "stopped", "failed", "running")
    return {
        "has_checkpoint": True, "can_resume": can_resume,
        "has_failed": has_failed, "failed_count": len(failed_ids), "checkpoint": ckpt
    }


@router.post("/api/admin/tasks/clear-checkpoint")
def clear_task_checkpoint_endpoint():
    """Clears saved checkpoint from disk."""
    task_manager.clear_task_checkpoint()
    return {"status": "ok", "message": "Checkpoint cleared"}


@router.post("/api/admin/tasks/resume")
def resume_task_endpoint(background_tasks: BackgroundTasks, req: Optional[ResumeTaskRequest] = None):
    """Resumes interrupted/stopped task from checkpoint or requested indices."""
    ckpt = task_manager.load_task_checkpoint()
    if not ckpt:
        raise HTTPException(status_code=400, detail="No task checkpoint found to resume")

    task_type = ckpt.get("task_type", "")
    orig_options = ckpt.get("options", {})
    start_d_idx = (req.start_deck_idx if req else None) or ckpt.get("current_deck_idx") or 1
    start_c_idx = (req.start_card_idx if req else None) or ckpt.get("current_card_idx") or 1

    task_id = f"resumed_{int(time.time())}"
    task_info = dict(ckpt)
    task_info["task_id"] = task_id
    task_info["status"] = "running"
    task_info["control"] = "run"
    task_info["logs"] = list(ckpt.get("logs", []))
    task_info["logs"].append(f"▶ Возобновление задачи с колоды #{start_d_idx} / карточки #{start_c_idx}...")
    regen_tasks[task_id] = task_info
    task_manager.register_task(task_id, task_info)

    if task_type == "batch_ai":
        opt_req = BatchRegenerateDeckRequest(**orig_options)
        opt_req.start_deck_idx = start_d_idx
        opt_req.start_card_idx = start_c_idx
        background_tasks.add_task(run_batch_ai_regeneration, task_id, opt_req)
    elif task_type == "batch_audio":
        opt_req = BatchRegenerateAudioRequest(**orig_options)
        opt_req.start_deck_idx = start_d_idx
        opt_req.start_card_idx = start_c_idx
        background_tasks.add_task(run_batch_audio_regeneration, task_id, opt_req)
    elif task_type == "single_ai":
        deck_id = ckpt.get("deck_id") or ckpt.get("current_deck_id")
        opt_req = RegenerateDeckRequest(**orig_options)
        opt_req.start_card_idx = start_c_idx
        regen_tasks[deck_id] = task_info
        background_tasks.add_task(run_ai_regeneration, deck_id, opt_req)
    elif task_type == "single_audio":
        deck_id = ckpt.get("deck_id") or ckpt.get("current_deck_id")
        opt_req = RegenerateAudioRequest(**orig_options)
        opt_req.start_card_idx = start_c_idx
        regen_tasks[deck_id] = task_info
        background_tasks.add_task(run_audio_regeneration, deck_id, opt_req)
    elif task_type == "bulk_create":
        opt_req = BulkCreateCardsRequest(**orig_options)
        opt_req.start_card_idx = start_c_idx
        background_tasks.add_task(run_bulk_card_creation, task_id, opt_req)
    elif task_type == "classification":
        opt_req = ClassificationRequest(**orig_options)
        background_tasks.add_task(run_classification_task, task_id, opt_req)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported task type for resume: {task_type}")

    return {
        "status": "resumed", "task_id": task_id, "task_type": task_type,
        "resumed_from_deck": start_d_idx, "resumed_from_card": start_c_idx,
        "message": "Task resumed successfully"
    }


@router.post("/api/admin/tasks/retry-failed")
def retry_failed_cards_endpoint(req: RetryFailedCardsRequest, background_tasks: BackgroundTasks):
    """Retries regeneration specifically for failed or incomplete cards based on task logs/checkpoint or deck audit."""
    ckpt = task_manager.load_task_checkpoint() or {}

    target_card_ids = list(req.card_ids or [])
    deck_ids = list(req.deck_ids or [])

    if not target_card_ids:
        task_data = regen_tasks.get(req.task_id) if req.task_id else ckpt
        if task_data:
            target_card_ids = list(task_data.get("failed_card_ids") or [])
            if not deck_ids:
                deck_ids = list(task_data.get("options", {}).get("deck_ids") or [])
                if not deck_ids and task_data.get("current_deck_id"):
                    deck_ids = [str(task_data.get("current_deck_id"))]

    if not deck_ids:
        lid_folders = list(models.TMA_Folder.select().where(
            models.TMA_Folder.name.contains("Leben in Deutschland"),
            models.TMA_Folder.is_deleted == False
        ))
        folder_ids = [f.id for f in lid_folders]
        decks = list(models.TMA_Deck.select().where(
            models.TMA_Deck.folder_id.in_(folder_ids),
            models.TMA_Deck.is_deleted == False
        ))
        deck_ids = [str(d.id) for d in decks]

    if not target_card_ids and deck_ids:
        for d_id in deck_ids:
            d_obj, d_cards, _ = get_deck_and_cards(d_id)
            if d_cards:
                incomplete = [c.id for c in d_cards if not card_is_fully_completed(c)]
                target_card_ids.extend(incomplete)

    target_card_ids = list(set(target_card_ids))
    if not target_card_ids:
        raise HTTPException(
            status_code=400,
            detail="Все карточки в выбранных колодах уже полностью и успешно заполнены! Ошибок не найдено."
        )

    matched_deck_ids = set(deck_ids)
    if not matched_deck_ids:
        cards_objs = list(models.TMA_Card.select(models.TMA_Card.deck_id).where(models.TMA_Card.id.in_(target_card_ids)))
        matched_deck_ids = set([str(c.deck_id) for c in cards_objs])

    task_id = f"retry_failed_{int(time.time())}"
    voice_choice = req.voice or ckpt.get("voice") or "de-DE-SeraphinaMultilingualNeural"
    prompt_choice = req.prompt_id or ckpt.get("options", {}).get("prompt_id") or "preset_exam"

    opt_req = BatchRegenerateDeckRequest(
        deck_ids=list(matched_deck_ids), target_card_ids=target_card_ids,
        voice=voice_choice, prompt_id=prompt_choice,
        sync_copies=req.sync_copies if req.sync_copies is not None else True,
        native_lang=req.native_lang or "ru", target_lang=req.target_lang or "de",
        delay=req.delay or 1.0, skip_completed=False, dry_run=False
    )

    task_data = {
        "task_id": task_id, "is_batch": True, "task_type": "batch_ai",
        "status": "pending", "control": "run", "total_decks": len(matched_deck_ids),
        "processed_decks": 0, "current_deck_id": "", "current_deck_name": "",
        "total_cards": len(target_card_ids), "processed_cards": 0, "current_card": "",
        "logs": [f"🔄 Запуск повторной генерации {len(target_card_ids)} карточек с ошибками/неполных (голос: {voice_choice})..."],
        "dry_run_results": [], "is_dry_run": False, "is_audio_only": False,
        "voice": voice_choice, "options": opt_req.dict(),
        "failed_card_ids": [], "failed_cards": [], "start_time": time.time()
    }
    regen_tasks[task_id] = task_data
    task_manager.register_task(task_id, task_data)
    background_tasks.add_task(run_batch_ai_regeneration, task_id, opt_req)

    return {
        "status": "ok", "task_id": task_id,
        "cards_count": len(target_card_ids), "decks_count": len(matched_deck_ids),
        "message": f"Запущена повторная генерация {len(target_card_ids)} карточек с ошибками"
    }
