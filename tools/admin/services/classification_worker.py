"""CEFR classification background worker for the Admin Panel."""
import asyncio
import datetime
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from api import models, ai_service
from api.services.cefr_metadata import (
    build_ai_cefr_payload,
    build_cleared_cefr_payload,
    build_local_cefr_payload,
    merge_cefr_metadata,
)
from tools.admin.schemas import ClassificationRequest, VALID_CEFR_LEVELS, CEFR_TAG_RE
from tools.admin.services import task_manager

logger = logging.getLogger(__name__)

# Shared task registry reference (set by server.py on startup)
regen_tasks: dict = {}


def extract_existing_cefr_level(tags_str: Optional[str]) -> Optional[str]:
    if not tags_str:
        return None
    match = CEFR_TAG_RE.search(str(tags_str))
    return match.group(1).upper() if match else None


def replace_cefr_level(tags_str: Optional[str], level: str) -> str:
    tags = str(tags_str or "")
    cleaned = CEFR_TAG_RE.sub("", tags)
    cleaned_parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    return ",".join([*cleaned_parts, level]) if cleaned_parts else level


def remove_cefr_level(tags_str: Optional[str]) -> str:
    tags = str(tags_str or "")
    cleaned = CEFR_TAG_RE.sub("", tags)
    return ",".join(part.strip() for part in cleaned.split(",") if part.strip())


def empty_classification_status() -> Dict[str, Any]:
    return {
        "status": "idle",
        "processed_cards": 0,
        "total_cards": 0,
        "processed_ai_chunks": 0,
        "total_ai_chunks": 0,
        "logs": [],
    }


def _level_counter_dict(counter: Counter) -> Dict[str, int]:
    return {level: int(counter.get(level, 0)) for level in sorted(VALID_CEFR_LEVELS)}


def _select_tma_cards_for_classification(req: ClassificationRequest) -> List[Dict[str, Any]]:
    lang = (req.lang or "de").lower().strip()
    query = (
        models.TMA_Card
        .select(models.TMA_Card.id, models.TMA_Card.front_text, models.TMA_Card.tags, models.TMA_Card.metadata)
        .join(models.TMA_Deck)
        .where(models.TMA_Card.is_deleted == False)
        .order_by(models.TMA_Card.id.asc())
    )
    if lang == "de":
        query = query.where(
            (models.TMA_Deck.target_language == "de") |
            (models.TMA_Deck.target_language.is_null())
        )
    else:
        query = query.where(models.TMA_Deck.target_language == lang)
    if req.limit and req.limit > 0:
        query = query.limit(req.limit)
    return list(query.dicts())


def _append_classification_log(task_info: Dict[str, Any], message: str) -> None:
    task_info.setdefault("logs", []).append(message)
    if len(task_info["logs"]) > 180:
        task_info["logs"] = task_info["logs"][-180:]


def _save_classification_task(task_id: str, task_info: Dict[str, Any]) -> None:
    regen_tasks[task_id] = task_info
    task_manager.save_task_checkpoint(task_info)


async def run_classification_task(task_id: str, req: ClassificationRequest):
    """Classifies CEFR tags for TMA cards using local rules first and AI for uncertain phrases."""
    task_info = regen_tasks.get(task_id) or task_manager.get_task(task_id)
    if not task_info:
        return

    mode = (req.mode or "audit").lower().strip()
    lang = (req.lang or "de").lower().strip()
    vocab_profile = (req.vocab_profile or "medium").lower().strip()
    if mode not in {"audit", "dry_run", "run"}:
        task_info["status"] = "failed"
        _append_classification_log(task_info, f"Invalid classification mode: {mode}")
        _save_classification_task(task_id, task_info)
        return
    if vocab_profile not in {"base", "medium", "max"}:
        vocab_profile = "medium"

    task_info.update({
        "status": "running", "control": "run", "mode": mode, "lang": lang,
        "vocab_profile": vocab_profile, "overwrite": req.overwrite,
        "clear_uncertain_local": req.clear_uncertain_local,
        "include_library": req.include_library, "options": req.dict(), "task_type": "classification",
    })
    _append_classification_log(task_info, f"CEFR classification started: mode={mode}, lang={lang}, vocab={vocab_profile}.")
    _save_classification_task(task_id, task_info)

    try:
        import os
        os.environ["DE_VOCAB_PROFILE"] = vocab_profile

        all_cards = _select_tma_cards_for_classification(req)
        existing_level_counts = Counter()
        cards_to_process: List[Dict[str, Any]] = []
        skipped_tagged = 0

        for card in all_cards:
            existing_level = extract_existing_cefr_level(card.get("tags"))
            if existing_level:
                existing_level_counts[existing_level] += 1
            if existing_level and not req.overwrite:
                skipped_tagged += 1
                continue
            cards_to_process.append(card)

        phrase_to_card_ids = defaultdict(list)
        phrase_to_card_tags: Dict[int, Optional[str]] = {}
        phrase_to_card_metadata: Dict[int, Optional[str]] = {}
        for card in cards_to_process:
            phrase = (card.get("front_text") or "").strip()
            if phrase:
                phrase_to_card_ids[phrase].append(card["id"])
                phrase_to_card_tags[card["id"]] = card.get("tags")
                phrase_to_card_metadata[card["id"]] = card.get("metadata")

        unique_phrases = list(phrase_to_card_ids.keys())
        duplicate_saved = len(cards_to_process) - len(unique_phrases)
        task_info.update({
            "total_cards": len(cards_to_process), "cards_scanned": len(all_cards),
            "skipped_tagged": skipped_tagged, "unique_phrases": len(unique_phrases),
            "duplicate_saved": duplicate_saved, "processed_cards": 0,
            "existing_level_counts": _level_counter_dict(existing_level_counts),
        })
        _append_classification_log(task_info, f"Scanned {len(all_cards)} cards, processing {len(cards_to_process)} cards, {len(unique_phrases)} unique phrases.")
        _save_classification_task(task_id, task_info)

        if not unique_phrases:
            task_info["status"] = "completed"
            _append_classification_log(task_info, "No cards require classification.")
            _save_classification_task(task_id, task_info)
            return

        from api.services.classifier import classify_sentence_fast

        phrase_to_level: Dict[str, Optional[str]] = {}
        phrase_to_source: Dict[str, str] = {}
        phrase_to_confidence: Dict[str, float] = {}
        phrase_to_cefr_payload: Dict[str, Dict[str, Any]] = {}
        phrase_to_local_fallback: Dict[str, str] = {}
        local_level_counts = Counter()
        local_fallback_counts = Counter()
        cleared_local_counts = Counter()
        phrases_for_ai: List[str] = []

        for idx, phrase in enumerate(unique_phrases, 1):
            if task_info.get("control") == "stop":
                task_info["status"] = "stopped"
                _append_classification_log(task_info, "Classification stopped during local pass.")
                _save_classification_task(task_id, task_info)
                return
            while task_info.get("control") == "pause":
                task_info["status"] = "paused"
                _save_classification_task(task_id, task_info)
                await asyncio.sleep(0.5)
            task_info["status"] = "running"

            if lang == "de":
                local = classify_sentence_fast(phrase, "de")
                local_level = local.get("level", "A1")
                local_conf = float(local.get("confidence", 0.0) or 0.0)
                phrase_to_local_fallback[phrase] = local_level
                if local_conf >= 0.80:
                    phrase_to_level[phrase] = local_level
                    phrase_to_source[phrase] = "local"
                    phrase_to_confidence[phrase] = local_conf
                    phrase_to_cefr_payload[phrase] = build_local_cefr_payload(local, source="local")
                    local_level_counts[local_level] += len(phrase_to_card_ids[phrase])
                else:
                    local_fallback_counts[local_level] += len(phrase_to_card_ids[phrase])
                    if req.clear_uncertain_local:
                        phrase_to_level[phrase] = None
                        phrase_to_source[phrase] = "cleared"
                        phrase_to_confidence[phrase] = local_conf
                        phrase_to_cefr_payload[phrase] = build_cleared_cefr_payload(local)
                        cleared_local_counts["NO_LEVEL"] += len(phrase_to_card_ids[phrase])
                    else:
                        phrases_for_ai.append(phrase)
            else:
                phrases_for_ai.append(phrase)

            if idx % 200 == 0 or idx == len(unique_phrases):
                task_info["processed_cards"] = sum(len(phrase_to_card_ids[p]) for p in phrase_to_level)
                task_info["local_unique"] = sum(1 for p in phrase_to_source if phrase_to_source[p] == "local")
                task_info["ai_unique"] = len(phrases_for_ai)
                _save_classification_task(task_id, task_info)

        uncertain_cards = sum(len(phrase_to_card_ids[p]) for p in phrases_for_ai)
        task_info.update({
            "local_unique": sum(1 for p in phrase_to_source if phrase_to_source[p] == "local"),
            "ai_unique": len(phrases_for_ai), "ai_cards": uncertain_cards,
            "cleared_unique": sum(1 for p in phrase_to_source if phrase_to_source[p] == "cleared"),
            "cleared_cards": int(cleared_local_counts.get("NO_LEVEL", 0)),
            "local_level_counts": _level_counter_dict(local_level_counts),
            "local_fallback_counts": _level_counter_dict(local_fallback_counts),
            "cleared_local_counts": dict(cleared_local_counts),
        })
        local_processed_cards = sum(
            len(phrase_to_card_ids[phrase])
            for phrase, source in phrase_to_source.items()
            if source in {"local", "cleared"}
        )
        _append_classification_log(
            task_info,
            f"Local pass completed: {task_info['local_unique']} confident phrases, "
            f"{task_info['cleared_unique']} cleared, {len(phrases_for_ai)} phrases need AI."
        )

        if mode == "audit":
            audit_counts = Counter(local_level_counts)
            audit_counts["NO_LEVEL"] = int(cleared_local_counts.get("NO_LEVEL", 0))
            task_info["level_counts"] = {
                **_level_counter_dict(audit_counts),
                "NO_LEVEL": int(audit_counts.get("NO_LEVEL", 0)),
            }
            task_info["source_counts"] = {
                "local": int(local_processed_cards - cleared_local_counts.get("NO_LEVEL", 0)),
                "cleared": int(cleared_local_counts.get("NO_LEVEL", 0)),
                "ai_pending": int(uncertain_cards),
            }
            task_info["status"] = "completed"
            task_info["processed_cards"] = len(cards_to_process)
            _append_classification_log(task_info, "Audit completed. AI calls and DB writes were skipped.")
            _save_classification_task(task_id, task_info)
            return

        if phrases_for_ai:
            chunk_size = 30
            chunks = [phrases_for_ai[i:i + chunk_size] for i in range(0, len(phrases_for_ai), chunk_size)]
            task_info["total_ai_chunks"] = len(chunks)
            task_info["processed_ai_chunks"] = 0
            _append_classification_log(task_info, f"AI fallback started: {len(chunks)} chunks.")
            _save_classification_task(task_id, task_info)

            for idx, chunk in enumerate(chunks, 1):
                if task_info.get("control") == "stop":
                    task_info["status"] = "stopped"
                    _append_classification_log(task_info, "Classification stopped during AI fallback.")
                    _save_classification_task(task_id, task_info)
                    return
                while task_info.get("control") == "pause":
                    task_info["status"] = "paused"
                    _save_classification_task(task_id, task_info)
                    await asyncio.sleep(0.5)
                task_info["status"] = "running"
                task_info["current_card"] = f"AI chunk {idx}/{len(chunks)}"

                try:
                    levels = await ai_service.classify_phrases_batch(chunk, target_language=lang)
                    for phrase, level in zip(chunk, levels):
                        valid_level = level if level in VALID_CEFR_LEVELS else phrase_to_local_fallback.get(phrase, "A1")
                        phrase_to_level[phrase] = valid_level
                        phrase_to_source[phrase] = "ai"
                        phrase_to_confidence[phrase] = 1.0
                        phrase_to_cefr_payload[phrase] = build_ai_cefr_payload(valid_level)
                except Exception as err:
                    _append_classification_log(task_info, f"AI chunk {idx} failed, local fallback used: {str(err)[:80]}")
                    for phrase in chunk:
                        fallback_level = phrase_to_local_fallback.get(phrase, "A1")
                        phrase_to_level[phrase] = fallback_level
                        phrase_to_source[phrase] = "fallback"
                        phrase_to_confidence[phrase] = 0.0
                        phrase_to_cefr_payload[phrase] = build_ai_cefr_payload(fallback_level, source="fallback")

                task_info["processed_ai_chunks"] = idx
                task_info["processed_cards"] = min(
                    len(cards_to_process),
                    local_processed_cards + sum(len(phrase_to_card_ids[p]) for p in phrases_for_ai[:idx * chunk_size])
                )
                _save_classification_task(task_id, task_info)
                if req.delay > 0 and idx < len(chunks):
                    await asyncio.sleep(req.delay)

        level_counts = Counter()
        source_counts = Counter()
        classification_results = []
        for phrase, card_ids in phrase_to_card_ids.items():
            level = phrase_to_level.get(phrase, phrase_to_local_fallback.get(phrase, "A1"))
            source = phrase_to_source.get(phrase, "fallback")
            cefr_payload = phrase_to_cefr_payload.get(phrase)
            if not cefr_payload:
                cefr_payload = build_ai_cefr_payload(level, source=source) if level else build_cleared_cefr_payload()
            if level:
                level_counts[level] += len(card_ids)
            else:
                level_counts["NO_LEVEL"] += len(card_ids)
            source_counts[source] += len(card_ids)
            if len(classification_results) < 80:
                first_id = card_ids[0]
                classification_results.append({
                    "phrase": phrase, "card_id": first_id, "card_count": len(card_ids),
                    "old_level": extract_existing_cefr_level(phrase_to_card_tags.get(first_id)),
                    "new_level": level, "source": source,
                    "confidence": round(phrase_to_confidence.get(phrase, 0.0), 2),
                    "reason": cefr_payload.get("reason"), "reason_short": cefr_payload.get("reason_short"),
                })

        task_info.update({
            "level_counts": {**_level_counter_dict(level_counts), "NO_LEVEL": int(level_counts.get("NO_LEVEL", 0))},
            "source_counts": dict(source_counts),
            "classification_results": classification_results,
            "processed_cards": len(cards_to_process),
        })

        if mode == "dry_run":
            task_info["status"] = "completed"
            _append_classification_log(task_info, "Dry-run completed. No database changes were written.")
            _save_classification_task(task_id, task_info)
            return

        # Create backup before DB write
        from tools.admin.services.regen_worker import _create_full_db_backup
        try:
            backup = _create_full_db_backup()
            task_info["backup_filename"] = backup.get("filename")
            _append_classification_log(task_info, f"Backup created before DB update: {backup.get('filename')}.")
        except Exception as backup_err:
            task_info["status"] = "failed"
            _append_classification_log(task_info, f"Backup failed, DB update cancelled: {backup_err}")
            _save_classification_task(task_id, task_info)
            return

        card_update_groups = defaultdict(list)
        for phrase, card_ids in phrase_to_card_ids.items():
            level = phrase_to_level.get(phrase, phrase_to_local_fallback.get(phrase, "A1"))
            source = phrase_to_source.get(phrase, "fallback")
            cefr_payload = phrase_to_cefr_payload.get(phrase)
            if not cefr_payload:
                cefr_payload = build_ai_cefr_payload(level, source=source) if level else build_cleared_cefr_payload()
            for card_id in card_ids:
                if level:
                    new_tags = replace_cefr_level(phrase_to_card_tags.get(card_id), level)
                else:
                    new_tags = remove_cefr_level(phrase_to_card_tags.get(card_id))
                new_metadata = merge_cefr_metadata(phrase_to_card_metadata.get(card_id), cefr_payload)
                card_update_groups[(new_tags, new_metadata)].append(card_id)

        updated_total = 0
        now = datetime.datetime.now()
        with models.tma_db.atomic():
            for (new_tags, new_metadata), card_ids in card_update_groups.items():
                for idx in range(0, len(card_ids), 500):
                    chunk = card_ids[idx:idx + 500]
                    updated_total += (
                        models.TMA_Card
                        .update(tags=new_tags, metadata=new_metadata, updated_at=now)
                        .where(models.TMA_Card.id << chunk)
                        .execute()
                    )

        task_info["updated_cards"] = updated_total
        task_info["status"] = "completed"
        _append_classification_log(task_info, f"DB update completed: {updated_total} cards updated.")
        _save_classification_task(task_id, task_info)
    except Exception as err:
        logger.error(f"Classification task failed: {err}", exc_info=True)
        task_info["status"] = "failed"
        _append_classification_log(task_info, f"Classification failed: {str(err)[:160]}")
        _save_classification_task(task_id, task_info)
