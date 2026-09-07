"""Admin classification router."""
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException

from tools.admin.schemas import BatchControlRequest, ClassificationRequest
from tools.admin.services.classification_worker import (
    _append_classification_log,
    _save_classification_task,
    empty_classification_status,
    regen_tasks as classification_regen_tasks,
    run_classification_task,
)
from tools.admin.services import task_manager
from tools.admin.services.regen_worker import regen_tasks

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/admin/classification/start")
def start_classification(req: ClassificationRequest, background_tasks: BackgroundTasks):
    """Starts a background CEFR classification audit, dry-run, or DB update."""
    mode = (req.mode or "audit").lower().strip()
    if mode not in {"audit", "dry_run", "run"}:
        raise HTTPException(status_code=400, detail="Mode must be audit, dry_run, or run")
    if (req.lang or "de").lower().strip() != "de" and mode == "audit":
        raise HTTPException(status_code=400, detail="Only German local audit is supported right now")

    task_id = f"classification_{int(time.time())}"
    task_data = {
        "task_id": task_id,
        "is_batch": True,
        "task_type": "classification",
        "status": "pending",
        "control": "run",
        "mode": mode,
        "lang": req.lang,
        "vocab_profile": req.vocab_profile,
        "overwrite": req.overwrite,
        "clear_uncertain_local": req.clear_uncertain_local,
        "include_library": req.include_library,
        "cards_scanned": 0,
        "total_cards": 0,
        "processed_cards": 0,
        "unique_phrases": 0,
        "duplicate_saved": 0,
        "local_unique": 0,
        "ai_unique": 0,
        "ai_cards": 0,
        "processed_ai_chunks": 0,
        "total_ai_chunks": 0,
        "updated_cards": 0,
        "cleared_unique": 0,
        "cleared_cards": 0,
        "current_card": "",
        "logs": [],
        "classification_results": [],
        "level_counts": {},
        "existing_level_counts": {},
        "local_level_counts": {},
        "local_fallback_counts": {},
        "source_counts": {},
        "options": req.dict(),
        "start_time": time.time(),
    }
    regen_tasks[task_id] = task_data
    classification_regen_tasks[task_id] = task_data
    task_manager.register_task(task_id, task_data)
    background_tasks.add_task(run_classification_task, task_id, req)
    return {"status": "ok", "task_id": task_id, "message": "Classification task started"}


@router.get("/api/admin/classification/{task_id}/status")
def get_classification_status(task_id: str):
    """Returns progress and logs of a CEFR classification task."""
    status_info = regen_tasks.get(task_id) or task_manager.get_task(task_id)
    return status_info or empty_classification_status()


@router.post("/api/admin/classification/{task_id}/control")
def control_classification(task_id: str, req: BatchControlRequest):
    """Pauses, resumes, or stops a CEFR classification task."""
    task_info = regen_tasks.get(task_id) or task_manager.get_task(task_id)
    if not task_info:
        raise HTTPException(status_code=404, detail="Classification task not found")

    action = (req.action or "").lower().strip()
    if action == "pause":
        task_info["control"] = "pause"
        task_info["status"] = "paused"
        _append_classification_log(task_info, "Classification paused.")
    elif action == "resume":
        task_info["control"] = "run"
        task_info["status"] = "running"
        _append_classification_log(task_info, "Classification resumed.")
    elif action == "stop":
        task_info["control"] = "stop"
        task_info["status"] = "stopped"
        _append_classification_log(task_info, "Stop signal sent.")
    else:
        raise HTTPException(status_code=400, detail="Action must be pause, resume, or stop")

    _save_classification_task(task_id, task_info)
    return {"status": "ok", "current_control": task_info.get("control"), "task_status": task_info.get("status")}
