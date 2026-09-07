"""Admin decks router — deck list, CRUD, regen control, set-default, assign, deduplicate."""
import datetime
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from peewee import fn

from api import models
from tools.admin.schemas import (
    AssignDeckRequest,
    BatchDeleteDecksRequest,
    BatchSummaryRequest,
    ControlRegenRequest,
    CreateDeckRequest,
    SetDefaultDeckRequest,
)
from tools.admin.services.card_helpers import card_has_valid_audio, card_is_fully_completed
from tools.admin.services.deck_helpers import get_deck_and_cards, sync_card_updates_to_matching_decks
from tools.admin.services.regen_worker import (
    regen_tasks,
    run_ai_regeneration,
    run_audio_regeneration,
    run_batch_ai_regeneration,
    run_batch_audio_regeneration,
)
from tools.admin.schemas import (
    BatchRegenerateDeckRequest,
    BatchRegenerateAudioRequest,
    RegenerateDeckRequest,
    RegenerateAudioRequest,
    BatchControlRequest,
)
from tools.admin.services import task_manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/admin/decks")
def get_all_decks(search: Optional[str] = None, user_id: Optional[int] = None):
    """Returns all active decks in the database with health statistics (missing audio/context)."""
    result = []

    if not user_id:
        lib_query = models.Deck.select().where(models.Deck.is_deleted == False)
        if search:
            s = search.strip()
            if s.isdigit():
                lib_query = lib_query.where((models.Deck.id == int(s)) | (models.Deck.name.contains(s)))
            else:
                lib_query = lib_query.where(
                    (models.Deck.name.contains(s)) |
                    (models.Deck.target_language.contains(s)) |
                    (models.Deck.level.contains(s)) |
                    (models.Deck.topic.contains(s))
                )
        lib_decks = list(lib_query.order_by(models.Deck.id.desc()))

        lib_card_counts = dict(
            models.Card.select(models.Card.deck_id, fn.COUNT(models.Card.id))
            .where(models.Card.is_deleted == False)
            .group_by(models.Card.deck_id).tuples()
        )
        lib_missing_audio = dict(
            models.Card.select(models.Card.deck_id, fn.COUNT(models.Card.id))
            .where((models.Card.is_deleted == False) & ((models.Card.audio_path.is_null(True)) | (models.Card.audio_path == '')))
            .group_by(models.Card.deck_id).tuples()
        )
        lib_missing_context = dict(
            models.Card.select(models.Card.deck_id, fn.COUNT(models.Card.id))
            .where((models.Card.is_deleted == False) & ((models.Card.context.is_null(True)) | (models.Card.context == '')))
            .group_by(models.Card.deck_id).tuples()
        )

        for d in lib_decks:
            c_count = lib_card_counts.get(d.id, 0)
            m_audio = lib_missing_audio.get(d.id, 0)
            m_ctx = lib_missing_context.get(d.id, 0)
            is_def = bool(getattr(d, 'is_default', False))

            if c_count == 0:
                h_status = "empty"
            elif m_ctx > 0:
                h_status = "needs_ai"
            elif m_audio > 0:
                h_status = "needs_audio"
            else:
                h_status = "ready"

            result.append({
                "id": f"lib_{d.id}", "user_id": "Библиотека ⭐", "user_name": "Библиотека ⭐",
                "user_username": "", "user_photo": "", "user_is_guest": False,
                "name": d.name, "level": d.level, "topic": d.topic,
                "target_language": d.target_language or "de",
                "card_count": c_count, "missing_audio_count": m_audio, "missing_context_count": m_ctx,
                "health_status": h_status,
                "created_at": str(d.created_at) if d.created_at else None,
                "is_default": is_def, "is_library": True
            })

    user_query = models.TMA_Deck.select().where(models.TMA_Deck.is_deleted == False)
    if user_id:
        user_query = user_query.where(models.TMA_Deck.user_id == user_id)
    if search:
        s = search.strip()
        if s.isdigit():
            user_query = user_query.where(
                (models.TMA_Deck.id == int(s)) | (models.TMA_Deck.user_id == int(s)) | (models.TMA_Deck.name.contains(s))
            )
        else:
            user_query = user_query.where(
                (models.TMA_Deck.name.contains(s)) | (models.TMA_Deck.target_language.contains(s)) |
                (models.TMA_Deck.level.contains(s)) | (models.TMA_Deck.topic.contains(s))
            )
    tma_decks = list(user_query.order_by(models.TMA_Deck.id.desc()))

    tma_card_counts = dict(
        models.TMA_Card.select(models.TMA_Card.deck_id, fn.COUNT(models.TMA_Card.id))
        .where(models.TMA_Card.is_deleted == False).group_by(models.TMA_Card.deck_id).tuples()
    )
    tma_missing_audio = dict(
        models.TMA_Card.select(models.TMA_Card.deck_id, fn.COUNT(models.TMA_Card.id))
        .where((models.TMA_Card.is_deleted == False) & ((models.TMA_Card.audio_path.is_null(True)) | (models.TMA_Card.audio_path == '')))
        .group_by(models.TMA_Card.deck_id).tuples()
    )
    tma_missing_context = dict(
        models.TMA_Card.select(models.TMA_Card.deck_id, fn.COUNT(models.TMA_Card.id))
        .where((models.TMA_Card.is_deleted == False) & ((models.TMA_Card.context.is_null(True)) | (models.TMA_Card.context == '')))
        .group_by(models.TMA_Card.deck_id).tuples()
    )

    tma_uids = list(set([d.user_id for d in tma_decks if d.user_id]))
    user_map = {}
    if tma_uids:
        for u in models.TMAUser.select().where(models.TMAUser.user_id << tma_uids):
            user_map[u.user_id] = u

    for d in tma_decks:
        c_count = tma_card_counts.get(d.id, 0)
        m_audio = tma_missing_audio.get(d.id, 0)
        m_ctx = tma_missing_context.get(d.id, 0)
        meta = {}
        try:
            meta = json.loads(d.metadata or "{}")
        except Exception:
            pass
        is_def = bool(meta.get("is_default", False))

        if c_count == 0:
            h_status = "empty"
        elif m_ctx > 0:
            h_status = "needs_ai"
        elif m_audio > 0:
            h_status = "needs_audio"
        else:
            h_status = "ready"

        u_obj = user_map.get(d.user_id)
        if u_obj:
            raw_name = f"{u_obj.first_name or ''} {u_obj.last_name or ''}".strip()
            user_display_name = raw_name or (f"@{u_obj.username}" if u_obj.username else f"User {d.user_id}")
            user_photo = getattr(u_obj, 'photo_url', '') or ""
            user_username = u_obj.username or ""
            user_is_guest = bool(getattr(u_obj, 'is_guest', False))
        else:
            user_display_name = f"User {d.user_id}"
            user_photo = ""
            user_username = ""
            user_is_guest = True

        result.append({
            "id": d.id, "user_id": d.user_id, "user_name": user_display_name,
            "user_username": user_username, "user_photo": user_photo, "user_is_guest": user_is_guest,
            "name": d.name, "level": d.level, "topic": d.topic,
            "target_language": d.target_language or "de",
            "card_count": c_count, "missing_audio_count": m_audio, "missing_context_count": m_ctx,
            "health_status": h_status,
            "created_at": str(d.created_at) if d.created_at else None,
            "is_default": is_def, "is_library": False, "share_id": d.share_id
        })

    return {"decks": result}


@router.get("/api/admin/users/{user_id}/decks")
def get_user_decks(user_id: int):
    """Returns list of decks belonging to a specific user."""
    decks = list(
        models.TMA_Deck.select()
        .where((models.TMA_Deck.user_id == user_id) & (models.TMA_Deck.is_deleted == False))
        .order_by(models.TMA_Deck.position.asc(), models.TMA_Deck.id.desc())
    )
    card_counts = dict(
        models.TMA_Card.select(models.TMA_Card.deck_id, fn.COUNT(models.TMA_Card.id))
        .where(models.TMA_Card.is_deleted == False).group_by(models.TMA_Card.deck_id).tuples()
    )
    result = []
    for d in decks:
        result.append({
            "id": d.id, "user_id": d.user_id, "name": d.name, "level": d.level,
            "target_language": d.target_language or "de",
            "card_count": card_counts.get(d.id, 0),
            "created_at": str(d.created_at) if d.created_at else None
        })
    return {"decks": result}


@router.post("/api/admin/decks/add")
def create_deck(req: CreateDeckRequest):
    """Creates a new deck for a specified user or as a default deck."""
    meta = {}
    if req.is_default:
        meta["is_default"] = True

    deck = models.TMA_Deck.create(
        user_id=req.user_id, name=req.name, target_language=req.target_language,
        level=req.level, topic=req.topic, metadata=json.dumps(meta)
    )

    if req.is_default:
        all_users = models.TMAUser.select()
        for u in all_users:
            if u.user_id != req.user_id:
                models.TMA_Deck.create(
                    user_id=u.user_id, name=req.name, target_language=req.target_language,
                    level=req.level, topic=req.topic,
                    metadata=json.dumps({"source_deck_id": deck.id, "is_default": True})
                )

    return {"status": "ok", "deck_id": deck.id, "name": deck.name}


@router.get("/api/admin/decks/{deck_id}/cards")
def get_deck_cards(deck_id: str):
    """Returns list of all active cards in a deck with health status and deck summary for preview."""
    deck, cards, is_lib = get_deck_and_cards(deck_id)
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")

    result = []
    total = len(cards)
    with_audio = 0
    with_context = 0
    fully_completed = 0

    for idx, c in enumerate(cards, 1):
        has_aud = card_has_valid_audio(c)
        has_ctx = bool(c.context and str(c.context).strip())
        is_comp = card_is_fully_completed(c)

        if has_aud:
            with_audio += 1
        if has_ctx:
            with_context += 1
        if is_comp:
            fully_completed += 1

        result.append({
            "id": c.id, "position": idx,
            "front": c.front_text or "", "back": c.back_text or "",
            "context": c.context or "", "tags": c.tags or "",
            "has_context": has_ctx, "has_audio": has_aud, "is_complete": is_comp,
            "audio_path": c.audio_path or ""
        })

    audio_cov = round((with_audio / total * 100)) if total > 0 else 0
    ctx_cov = round((with_context / total * 100)) if total > 0 else 0
    comp_cov = round((fully_completed / total * 100)) if total > 0 else 0

    meta = {}
    try:
        meta = json.loads(getattr(deck, 'metadata', '{}') or '{}')
    except Exception:
        pass

    deck_info = {
        "id": str(deck_id), "name": deck.name,
        "target_language": getattr(deck, 'target_language', 'de') or 'de',
        "level": getattr(deck, 'level', None), "topic": getattr(deck, 'topic', None),
        "is_library": is_lib,
        "user_id": getattr(deck, 'user_id', 0) if hasattr(deck, 'user_id') else "Библиотека ⭐",
        "is_default": bool(meta.get("is_default", False)) if not is_lib else bool(getattr(deck, 'is_default', False)),
        "card_count": total, "with_audio_count": with_audio,
        "missing_audio_count": total - with_audio, "with_context_count": with_context,
        "missing_context_count": total - with_context, "fully_completed_count": fully_completed,
        "audio_coverage_pct": audio_cov, "context_coverage_pct": ctx_cov, "completion_pct": comp_cov
    }

    return {"cards": result, "deck": deck_info, "total": total}


@router.delete("/api/admin/decks/{deck_id}")
def delete_deck(deck_id: str):
    """Soft deletes a deck and all cards within it."""
    deck, _, is_lib = get_deck_and_cards(deck_id)
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")
    now = datetime.datetime.now()
    deck.is_deleted = True
    deck.updated_at = now
    deck.save()

    if is_lib:
        models.Card.update(is_deleted=True, updated_at=now).where(models.Card.deck == deck).execute()
    else:
        models.TMA_Card.update(is_deleted=True, updated_at=now).where(models.TMA_Card.deck_id == deck.id).execute()
        models.TMA_Collaborator.delete().where(
            (models.TMA_Collaborator.target_type == 'deck') & (models.TMA_Collaborator.target_id == deck.id)
        ).execute()

    return {"status": "ok", "deleted_deck_id": deck_id}


@router.post("/api/admin/decks/batch-delete")
def batch_delete_decks(req: BatchDeleteDecksRequest):
    """Soft deletes multiple decks and their cards atomically in one click."""
    if not req.deck_ids:
        return {"status": "ok", "deleted_count": 0, "deleted_deck_ids": []}

    now = datetime.datetime.now()
    deleted_ids = []
    lib_deck_ids = []
    tma_deck_ids = []

    with models.tma_db.atomic():
        for d_id in req.deck_ids:
            deck, _, is_lib = get_deck_and_cards(d_id)
            if not deck:
                continue
            deck.is_deleted = True
            deck.updated_at = now
            deck.save()
            deleted_ids.append(str(d_id))
            if is_lib:
                lib_deck_ids.append(deck.id)
            else:
                tma_deck_ids.append(deck.id)

        if lib_deck_ids:
            models.Card.update(is_deleted=True, updated_at=now).where(models.Card.deck_id << lib_deck_ids).execute()
        if tma_deck_ids:
            models.TMA_Card.update(is_deleted=True, updated_at=now).where(models.TMA_Card.deck_id << tma_deck_ids).execute()
            models.TMA_Collaborator.delete().where(
                (models.TMA_Collaborator.target_type == 'deck') & (models.TMA_Collaborator.target_id << tma_deck_ids)
            ).execute()

    logger.info(f"Admin bulk deleted {len(deleted_ids)} decks: {deleted_ids}")
    return {"status": "ok", "deleted_count": len(deleted_ids), "deleted_deck_ids": deleted_ids}


@router.post("/api/admin/decks/batch/summary")
def get_batch_summary(req: BatchSummaryRequest):
    """Returns aggregated statistics and breakdown for a list of staged deck IDs."""
    deck_list = []
    total_cards = 0
    total_missing_audio = 0
    languages = set()

    for d_id in req.deck_ids:
        deck, cards, is_lib = get_deck_and_cards(d_id)
        if not deck:
            continue
        c_count = len(cards)
        m_audio = sum(1 for c in cards if not card_has_valid_audio(c))
        lang = getattr(deck, 'target_language', 'de') or 'de'
        languages.add(lang)
        total_cards += c_count
        total_missing_audio += m_audio
        deck_list.append({
            "id": str(d_id), "name": deck.name, "target_language": lang,
            "level": getattr(deck, 'level', None), "card_count": c_count,
            "missing_audio_count": m_audio, "is_library": is_lib
        })

    return {
        "decks": deck_list, "total_decks": len(deck_list), "total_cards": total_cards,
        "total_missing_audio": total_missing_audio, "languages": list(languages)
    }


@router.post("/api/admin/decks/{deck_id}/deduplicate")
def deduplicate_deck_cards(deck_id: str):
    """Removes duplicate cards (matching front_text) from a deck, keeping the first/newest card."""
    deck, cards, is_lib = get_deck_and_cards(deck_id)
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")

    seen_fronts = set()
    removed_count = 0
    now = datetime.datetime.now()

    for c in cards:
        front = (c.front_text or "").strip().lower()
        if not front:
            continue
        if front in seen_fronts:
            c.is_deleted = True
            c.updated_at = now
            c.save()
            removed_count += 1
        else:
            seen_fronts.add(front)

    return {
        "status": "ok", "deck_id": deck_id, "removed_duplicates": removed_count,
        "remaining_cards": len(seen_fronts),
        "message": f"Удалено {removed_count} дубликатов карточек! Осталось {len(seen_fronts)} уникальных."
    }


@router.post("/api/admin/decks/{deck_id}/set-default")
def set_default_deck(deck_id: str, req: SetDefaultDeckRequest):
    """Marks deck as default (starter for new users) and optionally distributes copies to all existing users."""
    deck, cards, is_lib = get_deck_and_cards(deck_id)
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")

    clean_name = deck.name.replace("⭐ ", "").strip()
    now = datetime.datetime.now()

    if is_lib:
        deck.is_default = req.is_default
        deck.save()
    else:
        meta = {}
        try:
            meta = json.loads(deck.metadata or "{}")
        except Exception:
            pass
        meta["is_default"] = req.is_default
        deck.metadata = json.dumps(meta)
        deck.save()

        lib_deck = models.Deck.get_or_none(
            (models.Deck.is_deleted == False) &
            ((models.Deck.name == deck.name) | (models.Deck.name == clean_name) | (models.Deck.name == f"⭐ {clean_name}"))
        )
        if req.is_default:
            if not lib_deck:
                lib_deck = models.Deck.create(
                    name=clean_name,
                    target_language=getattr(deck, 'target_language', 'de') or 'de',
                    level=getattr(deck, 'level', None),
                    topic=getattr(deck, 'topic', None),
                    is_default=True, created_at=now, updated_at=now
                )
                for c in cards:
                    models.Card.create(
                        deck=lib_deck, front_text=c.front_text, back_text=c.back_text,
                        context=c.context, tags=c.tags, audio_path=c.audio_path,
                        audio_back_path=c.audio_back_path,
                        card_type=getattr(c, 'card_type', 'translation'),
                        source=getattr(c, 'source', '') or "",
                        created_at=now, updated_at=now
                    )
            else:
                lib_deck.is_default = True
                lib_deck.updated_at = now
                lib_deck.save()
        else:
            if lib_deck:
                lib_deck.is_default = False
                lib_deck.save()

    copied_count = 0
    if req.is_default and req.copy_to_existing:
        all_users = list(models.TMAUser.select())
        for u in all_users:
            if not hasattr(deck, 'user_id') or u.user_id != getattr(deck, 'user_id', None):
                existing = models.TMA_Deck.get_or_none(
                    (models.TMA_Deck.user_id == u.user_id) &
                    (models.TMA_Deck.is_deleted == False) &
                    ((models.TMA_Deck.name == deck.name) | (models.TMA_Deck.name == clean_name))
                )
                if not existing:
                    new_deck = models.TMA_Deck.create(
                        user_id=u.user_id,
                        name=clean_name if not is_lib else deck.name,
                        target_language=getattr(deck, 'target_language', 'de') or 'de',
                        level=getattr(deck, 'level', None), topic=getattr(deck, 'topic', None),
                        metadata=json.dumps({"source_deck_id": deck.id, "is_default": True})
                    )
                    card_objs = [
                        models.TMA_Card(
                            deck=new_deck, front_text=c.front_text or "", back_text=c.back_text or "",
                            context=c.context or "", tags=c.tags or "[]", audio_path=c.audio_path or "",
                            audio_back_path=c.audio_back_path or "",
                            card_type=getattr(c, 'card_type', 'translation') or 'translation',
                            source=getattr(c, 'source', 'default') or 'default',
                            creator_id=getattr(deck, 'user_id', 0),
                            created_at=now, updated_at=now
                        )
                        for c in cards
                    ]
                    if card_objs:
                        models.TMA_Card.bulk_create(card_objs, batch_size=200)
                    copied_count += 1

    msg = f"Колода '{clean_name}' теперь дефолтная (добавлена в Библиотеку для всех новых пользователей)!"
    if req.copy_to_existing:
        msg += f" И успешно добавлена {copied_count} существующим пользователям."
    elif not req.is_default:
        msg = f"Отметка дефолтной снята с колоды '{clean_name}'."

    return {"status": "ok", "is_default": req.is_default, "copied_to_users": copied_count, "message": msg}


@router.post("/api/admin/decks/{deck_id}/assign")
def assign_deck(deck_id: str, req: AssignDeckRequest):
    """Assigns deck to a specific list of user IDs, saves to Master Library, or sets as default for all."""
    deck, cards, is_lib = get_deck_and_cards(deck_id)
    if not deck:
        raise HTTPException(status_code=404, detail="Deck not found")

    processed_users = 0
    clean_name = deck.name.replace("⭐ ", "").strip()
    now = datetime.datetime.now()

    if req.mode == "default_all":
        res = set_default_deck(deck_id, SetDefaultDeckRequest(is_default=True, copy_to_existing=True))
        return {
            "status": "ok", "mode": "default_all",
            "message": f"Колода '{clean_name}' сделана дефолтной для ВСЕХ пользователей! "
                       f"Добавлена в Библиотеку (для будущих новых пользователей) и скопирована {res.get('copied_to_users', 0)} существующим пользователям."
        }

    if req.mode == "library":
        target_library_deck = models.Deck.get_or_none(
            (models.Deck.is_deleted == False) &
            ((models.Deck.name == deck.name) | (models.Deck.name == clean_name) | (models.Deck.name == f"⭐ {clean_name}"))
        )
        if target_library_deck:
            models.Card.update(is_deleted=True).where(models.Card.deck == target_library_deck).execute()
            for c in cards:
                models.Card.create(
                    deck=target_library_deck, front_text=c.front_text, back_text=c.back_text,
                    context=c.context, tags=c.tags, audio_path=c.audio_path, audio_back_path=c.audio_back_path,
                    card_type=getattr(c, 'card_type', 'translation'), source=getattr(c, 'source', '') or "",
                    created_at=now, updated_at=now
                )
            target_library_deck.is_default = True
            target_library_deck.updated_at = now
            target_library_deck.save()
            return {"status": "ok", "mode": "library", "action": "updated",
                    "message": f"Колода '{deck.name}' успешно обновлена в Библиотеке (заменено карточек: {len(cards)})!"}
        else:
            new_lib_deck = models.Deck.create(
                name=clean_name, target_language=deck.target_language or 'de',
                level=deck.level, topic=deck.topic, is_default=True, created_at=now, updated_at=now
            )
            for c in cards:
                models.Card.create(
                    deck=new_lib_deck, front_text=c.front_text, back_text=c.back_text,
                    context=c.context, tags=c.tags, audio_path=c.audio_path, audio_back_path=c.audio_back_path,
                    card_type=getattr(c, 'card_type', 'translation'), source=getattr(c, 'source', '') or "",
                    created_at=now, updated_at=now
                )
            return {"status": "ok", "mode": "library", "action": "created",
                    "message": f"Колода '{clean_name}' добавлена в Библиотеку как мастер-дефолтная!"}

    for target_user_id in req.user_ids:
        if req.mode == "collaborate":
            models.TMA_Collaborator.get_or_create(
                target_type="deck", target_id=deck.id, user_id=target_user_id,
                defaults={"role": "editor", "added_by": deck.user_id}
            )
            processed_users += 1
        else:
            new_deck = models.TMA_Deck.create(
                user_id=target_user_id, name=deck.name,
                target_language=deck.target_language, level=deck.level, topic=deck.topic,
                metadata=json.dumps({"assigned_from_deck_id": deck.id})
            )
            card_objs = [
                models.TMA_Card(
                    deck=new_deck, front_text=c.front_text or "", back_text=c.back_text or "",
                    context=c.context or "", tags=c.tags or "[]", audio_path=c.audio_path or "",
                    audio_back_path=c.audio_back_path or "",
                    card_type=getattr(c, 'card_type', 'translation') or 'translation',
                    source=getattr(c, 'source', 'assigned') or 'assigned',
                    creator_id=getattr(deck, 'user_id', 0),
                    created_at=now, updated_at=now
                )
                for c in cards
            ]
            if card_objs:
                models.TMA_Card.bulk_create(card_objs, batch_size=200)
            processed_users += 1

    return {"status": "ok", "mode": req.mode, "users_processed": processed_users}


# ─── Single-deck regen endpoints ─────────────────────────────────────────────

@router.post("/api/admin/decks/{deck_id}/regenerate-audio")
def start_audio_regeneration_endpoint(deck_id: str, req: RegenerateAudioRequest, background_tasks: BackgroundTasks):
    """Starts background audio regeneration for cards in the deck."""
    task_data = {
        "deck_id": deck_id, "task_id": deck_id, "task_type": "single_audio",
        "status": "pending", "control": "run", "processed": 0, "total": 0,
        "current_card": "", "logs": [], "dry_run_results": [], "is_dry_run": False,
        "is_audio_only": True, "voice": req.voice, "options": req.dict(),
        "start_time": time.time()
    }
    regen_tasks[deck_id] = task_data
    task_manager.register_task(deck_id, task_data)
    background_tasks.add_task(run_audio_regeneration, deck_id, req)
    return {"status": "ok", "message": f"Audio regeneration queued for deck {deck_id}"}


@router.post("/api/admin/decks/{deck_id}/regenerate")
def start_regeneration(deck_id: str, req: RegenerateDeckRequest, background_tasks: BackgroundTasks):
    """Starts background AI regeneration of cards for the deck."""
    task_data = {
        "deck_id": deck_id, "task_id": deck_id, "task_type": "single_ai",
        "status": "pending", "control": "run", "processed": 0, "total": 0,
        "current_card": "", "logs": [], "dry_run_results": [], "is_dry_run": req.dry_run,
        "options": req.dict(), "start_time": time.time()
    }
    regen_tasks[deck_id] = task_data
    task_manager.register_task(deck_id, task_data)
    background_tasks.add_task(run_ai_regeneration, deck_id, req)
    return {"status": "ok", "message": f"Regeneration queued for deck {deck_id}"}


@router.get("/api/admin/decks/{deck_id}/regen-status")
def get_regen_status(deck_id: str):
    """Returns progress and logs of an active or recent regeneration task."""
    status_info = regen_tasks.get(deck_id) or task_manager.get_task(deck_id)
    if not status_info:
        return {"status": "idle", "processed": 0, "total": 0, "logs": []}
    return status_info


@router.post("/api/admin/decks/{deck_id}/regen-control")
def control_regeneration(deck_id: str, req: ControlRegenRequest):
    """Controls running regeneration (pause, resume, stop) or commits dry-run results to DB."""
    task_info = regen_tasks.get(deck_id) or task_manager.get_task(deck_id)
    if not task_info:
        raise HTTPException(status_code=404, detail="Regeneration task not found")

    action = req.action.lower()
    if action == "pause":
        task_info["control"] = "pause"
        task_info["status"] = "paused"
        task_info["logs"].append("⏸ Перегенерация приостановлена.")
        task_manager.update_task_progress(deck_id, status="paused")
    elif action == "resume":
        task_info["control"] = "run"
        task_info["status"] = "running"
        task_info["logs"].append("▶ Перегенерация возобновлена.")
        task_manager.update_task_progress(deck_id, status="running")
    elif action == "stop":
        task_info["control"] = "stop"
        task_info["status"] = "stopped"
        task_info["logs"].append("🛑 Сигнал остановки отправлен.")
        task_manager.update_task_progress(deck_id, status="stopped")
    elif action == "commit_dry_run":
        results = task_info.get("dry_run_results", [])
        if not results:
            return {"status": "ok", "committed_count": 0, "committed_card_ids": [], "message": "Нет результатов Dry-Run для сохранения"}

        target_card_ids = set(req.card_ids) if getattr(req, "card_ids", None) else None
        deck, _, is_lib = get_deck_and_cards(deck_id)
        count = 0
        sync_total = 0
        now = datetime.datetime.now()
        committed_card_ids = []
        card_model = models.Card if is_lib else models.TMA_Card
        for item in results:
            if target_card_ids is not None and item["card_id"] not in target_card_ids:
                continue
            card = card_model.get_or_none(card_model.id == item["card_id"])
            if card:
                orig_front = card.front_text
                card.front_text = item["front"]
                card.back_text = item["back"]
                card.context = item["context"]
                if item.get("audio_path"):
                    card.audio_path = item["audio_path"]
                if item.get("level"):
                    curr_tags = card.tags or ""
                    cleaned = ",".join([t for t in curr_tags.split(",") if t and t.upper() not in {"A1", "A2", "B1", "B2", "C1", "C2"}])
                    card.tags = f"{cleaned},{item['level']}".strip(",") if cleaned else item['level']
                card.updated_at = now
                card.save()
                count += 1
                committed_card_ids.append(item["card_id"])

                if task_info.get("sync_copies") and deck:
                    _, sc = sync_card_updates_to_matching_decks(deck, orig_front, item["front"], item["back"], item["context"], item.get("level"), card.audio_path)
                    sync_total += sc

        existing_committed = set(task_info.get("committed_dry_run_card_ids", []))
        existing_committed.update(committed_card_ids)
        task_info["committed_dry_run_card_ids"] = list(existing_committed)

        sync_msg = f" (и синхронизировано {sync_total} карточек у других колод)" if sync_total > 0 else ""
        task_info["logs"].append(f"💾 Результаты Dry-Run успешно записаны в БД для {count} карточек{sync_msg}! Они сняты с последующей генерации.")
        task_manager.save_task_checkpoint(task_info)
        return {"status": "ok", "committed_count": count, "synced_count": sync_total, "committed_card_ids": committed_card_ids}

    return {"status": "ok", "current_control": task_info.get("control"), "task_status": task_info.get("status")}


# ─── Batch regen endpoints ────────────────────────────────────────────────────

@router.post("/api/admin/decks/batch/regenerate")
def start_batch_regeneration(req: BatchRegenerateDeckRequest, background_tasks: BackgroundTasks):
    """Starts background AI regeneration for multiple staged decks."""
    if not req.deck_ids:
        raise HTTPException(status_code=400, detail="No decks provided for batch regeneration")

    task_id = f"batch_ai_{int(time.time())}"
    task_data = {
        "task_id": task_id, "is_batch": True, "task_type": "batch_ai",
        "status": "pending", "control": "run", "total_decks": len(req.deck_ids),
        "processed_decks": 0, "current_deck_id": "", "current_deck_name": "",
        "total_cards": 0, "processed_cards": 0, "current_card": "", "logs": [],
        "dry_run_results": [], "is_dry_run": req.dry_run, "is_audio_only": False,
        "voice": req.voice, "options": req.dict(), "start_time": time.time()
    }
    regen_tasks[task_id] = task_data
    task_manager.register_task(task_id, task_data)
    background_tasks.add_task(run_batch_ai_regeneration, task_id, req)
    return {"status": "ok", "task_id": task_id, "total_decks": len(req.deck_ids), "message": "Batch AI regeneration started"}


@router.post("/api/admin/decks/batch/regenerate-audio")
def start_batch_audio_regeneration(req: BatchRegenerateAudioRequest, background_tasks: BackgroundTasks):
    """Starts background audio regeneration for multiple staged decks."""
    if not req.deck_ids:
        raise HTTPException(status_code=400, detail="No decks provided for batch regeneration")

    task_id = f"batch_audio_{int(time.time())}"
    task_data = {
        "task_id": task_id, "is_batch": True, "task_type": "batch_audio",
        "status": "pending", "control": "run", "total_decks": len(req.deck_ids),
        "processed_decks": 0, "current_deck_id": "", "current_deck_name": "",
        "total_cards": 0, "processed_cards": 0, "current_card": "", "logs": [],
        "dry_run_results": [], "is_dry_run": False, "is_audio_only": True,
        "voice": req.voice, "options": req.dict(), "start_time": time.time()
    }
    regen_tasks[task_id] = task_data
    task_manager.register_task(task_id, task_data)
    background_tasks.add_task(run_batch_audio_regeneration, task_id, req)
    return {"status": "ok", "task_id": task_id, "total_decks": len(req.deck_ids), "message": "Batch audio regeneration started"}


@router.get("/api/admin/decks/batch/{task_id}/status")
def get_batch_regen_status(task_id: str):
    """Returns progress and logs of a batch regeneration task."""
    status_info = regen_tasks.get(task_id) or task_manager.get_task(task_id)
    if not status_info:
        return {"status": "idle", "processed_decks": 0, "total_decks": 0, "processed_cards": 0, "total_cards": 0, "logs": []}
    return status_info


@router.post("/api/admin/decks/batch/{task_id}/control")
def control_batch_regeneration(task_id: str, req: BatchControlRequest):
    """Controls running batch regeneration (pause, resume, stop) or commits dry-run results to DB."""
    task_info = regen_tasks.get(task_id) or task_manager.get_task(task_id)
    if not task_info:
        raise HTTPException(status_code=404, detail="Batch regeneration task not found")

    action = req.action.lower()
    if action == "pause":
        task_info["control"] = "pause"
        task_info["status"] = "paused"
        task_info["logs"].append("⏸ Пакетная перегенерация приостановлена.")
        task_manager.update_task_progress(task_id, status="paused")
    elif action == "resume":
        task_info["control"] = "run"
        task_info["status"] = "running"
        task_info["logs"].append("▶ Пакетная перегенерация возобновлена.")
        task_manager.update_task_progress(task_id, status="running")
    elif action == "stop":
        task_info["control"] = "stop"
        task_info["status"] = "stopped"
        task_info["logs"].append("🛑 Сигнал остановки отправлен.")
        task_manager.update_task_progress(task_id, status="stopped")
    elif action == "commit_dry_run":
        results = task_info.get("dry_run_results", [])
        if not results:
            return {"status": "ok", "committed_count": 0, "committed_card_ids": [], "message": "Нет результатов Dry-Run для сохранения"}

        target_card_ids = set(req.card_ids) if getattr(req, "card_ids", None) else None
        count = 0
        sync_total = 0
        now = datetime.datetime.now()
        committed_card_ids = []
        for item in results:
            if target_card_ids is not None and item["card_id"] not in target_card_ids:
                continue
            deck_id_item = item["deck_id"]
            deck, _, is_lib = get_deck_and_cards(deck_id_item)
            card_model = models.Card if is_lib else models.TMA_Card
            card = card_model.get_or_none(card_model.id == item["card_id"])
            if card:
                orig_front = card.front_text
                card.front_text = item["front"]
                card.back_text = item["back"]
                card.context = item["context"]
                if item.get("audio_path"):
                    card.audio_path = item["audio_path"]
                if item.get("level"):
                    curr_tags = card.tags or ""
                    cleaned = ",".join([t for t in curr_tags.split(",") if t and t.upper() not in {"A1", "A2", "B1", "B2", "C1", "C2"}])
                    card.tags = f"{cleaned},{item['level']}".strip(",") if cleaned else item['level']
                card.updated_at = now
                card.save()
                count += 1
                committed_card_ids.append(item["card_id"])

                if task_info.get("sync_copies") and deck:
                    _, sc = sync_card_updates_to_matching_decks(deck, orig_front, item["front"], item["back"], item["context"], item.get("level"), card.audio_path)
                    sync_total += sc

        existing_committed = set(task_info.get("committed_dry_run_card_ids", []))
        existing_committed.update(committed_card_ids)
        task_info["committed_dry_run_card_ids"] = list(existing_committed)

        sync_msg = f" (и синхронизировано {sync_total} карточек у других колод)" if sync_total > 0 else ""
        task_info["logs"].append(f"💾 Результаты Dry-Run успешно записаны в БД для {count} карточек{sync_msg}! Они сняты с последующей генерации.")
        task_manager.save_task_checkpoint(task_info)
        return {"status": "ok", "committed_count": count, "synced_count": sync_total, "committed_card_ids": committed_card_ids}

    return {"status": "ok", "current_control": task_info.get("control"), "task_status": task_info.get("status")}
