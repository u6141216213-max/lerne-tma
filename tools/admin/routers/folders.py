"""Admin folders router."""
import datetime
import json
import logging
from collections import defaultdict
from typing import List
from peewee import fn

from fastapi import APIRouter, HTTPException

from api import models
from tools.admin.schemas import AssignFolderRequest, BatchDeleteFoldersRequest, SetDefaultFolderRequest

logger = logging.getLogger(__name__)
router = APIRouter()


def _collect_folder_descendants(folder_ids: List[int]) -> List[int]:
    all_ids = set(folder_ids)
    to_check = list(folder_ids)
    while to_check:
        children = list(models.TMA_Folder.select(models.TMA_Folder.id).where(
            (models.TMA_Folder.parent_id << to_check) & (models.TMA_Folder.is_deleted == False)
        ))
        new_ids = [c.id for c in children if c.id not in all_ids]
        if not new_ids:
            break
        all_ids.update(new_ids)
        to_check = new_ids
    return list(all_ids)


@router.get("/api/admin/folders")
def get_admin_folders():
    """Returns all TMA folders with user info, deck counts, and default status."""
    folders = list(models.TMA_Folder.select().where(models.TMA_Folder.is_deleted == False))
    users_map = {u.user_id: u for u in models.TMAUser.select()}

    card_counts = dict(
        models.TMA_Card.select(models.TMA_Card.deck_id, fn.COUNT(models.TMA_Card.id))
        .where(models.TMA_Card.is_deleted == False)
        .group_by(models.TMA_Card.deck_id).tuples()
    )

    all_decks = list(models.TMA_Deck.select().where(
        (models.TMA_Deck.folder.is_null(False)) & (models.TMA_Deck.is_deleted == False)
    ))
    decks_by_folder = defaultdict(list)
    for d in all_decks:
        decks_by_folder[d.folder_id].append(d)

    result = []
    for f in folders:
        if f.name == "📥 Входящие":
            continue
        u = users_map.get(f.user_id)
        user_name = f"User #{f.user_id}"
        user_photo = None
        user_is_guest = False
        if u:
            user_name = f"{u.first_name or ''} {u.last_name or ''}".strip() or (f"@{u.username}" if u.username else f"User #{f.user_id}")
            user_photo = u.photo_url
            user_is_guest = bool(getattr(u, 'is_guest', False) or str(f.user_id).startswith('999999'))

        decks = decks_by_folder.get(f.id, [])
        total_cards = 0
        all_default = len(decks) > 0
        for d in decks:
            total_cards += card_counts.get(d.id, 0)
            meta = {}
            try:
                meta = json.loads(d.metadata or "{}")
            except Exception:
                pass
            if not meta.get("is_default", False):
                all_default = False

        result.append({
            "id": f.id, "name": f.name, "color": f.color or "#6366f1",
            "target_language": f.target_language or "de",
            "user_id": f.user_id, "user_name": user_name, "user_photo": user_photo,
            "user_is_guest": user_is_guest,
            "deck_count": len(decks), "cards_count": total_cards, "is_default": all_default,
            "decks": [{"id": d.id, "name": d.name} for d in decks]
        })
    return {"folders": result}


@router.delete("/api/admin/folders/{folder_id}")
def delete_admin_folder(folder_id: int):
    """Soft deletes a folder, its subfolders, and all decks/cards inside them."""
    folder = models.TMA_Folder.get_or_none(models.TMA_Folder.id == folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    now = datetime.datetime.now()
    all_folder_ids = _collect_folder_descendants([folder_id])

    decks_in_folders = list(models.TMA_Deck.select(models.TMA_Deck.id).where(
        (models.TMA_Deck.folder_id << all_folder_ids) & (models.TMA_Deck.is_deleted == False)
    ))
    deck_ids = [d.id for d in decks_in_folders]

    with models.tma_db.atomic():
        if deck_ids:
            models.TMA_Card.update(is_deleted=True, updated_at=now).where(models.TMA_Card.deck_id << deck_ids).execute()
            models.TMA_Deck.update(is_deleted=True, updated_at=now).where(models.TMA_Deck.id << deck_ids).execute()
            models.TMA_Collaborator.delete().where(
                (models.TMA_Collaborator.target_type == 'deck') & (models.TMA_Collaborator.target_id << deck_ids)
            ).execute()
        models.TMA_Collaborator.delete().where(
            (models.TMA_Collaborator.target_type == 'folder') & (models.TMA_Collaborator.target_id << all_folder_ids)
        ).execute()
        models.TMA_Folder.update(is_deleted=True, updated_at=now).where(models.TMA_Folder.id << all_folder_ids).execute()

    logger.info(f"Admin deleted folder {folder_id} ({len(all_folder_ids)} folders, {len(deck_ids)} decks)")
    return {
        "status": "ok", "deleted_folder_id": folder_id,
        "deleted_folders_count": len(all_folder_ids), "deleted_decks_count": len(deck_ids)
    }


@router.post("/api/admin/folders/batch-delete")
def batch_delete_folders(req: BatchDeleteFoldersRequest):
    """Soft deletes multiple folders, their subfolders, and all decks/cards inside them."""
    if not req.folder_ids:
        return {"status": "ok", "deleted_folders_count": 0, "deleted_decks_count": 0}

    now = datetime.datetime.now()
    all_folder_ids = _collect_folder_descendants(req.folder_ids)

    decks_in_folders = list(models.TMA_Deck.select(models.TMA_Deck.id).where(
        (models.TMA_Deck.folder_id << all_folder_ids) & (models.TMA_Deck.is_deleted == False)
    ))
    deck_ids = [d.id for d in decks_in_folders]

    with models.tma_db.atomic():
        if deck_ids:
            models.TMA_Card.update(is_deleted=True, updated_at=now).where(models.TMA_Card.deck_id << deck_ids).execute()
            models.TMA_Deck.update(is_deleted=True, updated_at=now).where(models.TMA_Deck.id << deck_ids).execute()
            models.TMA_Collaborator.delete().where(
                (models.TMA_Collaborator.target_type == 'deck') & (models.TMA_Collaborator.target_id << deck_ids)
            ).execute()
        models.TMA_Collaborator.delete().where(
            (models.TMA_Collaborator.target_type == 'folder') & (models.TMA_Collaborator.target_id << all_folder_ids)
        ).execute()
        models.TMA_Folder.update(is_deleted=True, updated_at=now).where(models.TMA_Folder.id << all_folder_ids).execute()

    logger.info(f"Admin bulk deleted folders {req.folder_ids} ({len(all_folder_ids)} folders, {len(deck_ids)} decks)")
    return {"status": "ok", "deleted_folders_count": len(all_folder_ids), "deleted_decks_count": len(deck_ids)}


@router.post("/api/admin/folders/{folder_id}/set-default")
def set_default_folder(folder_id: int, req: SetDefaultFolderRequest):
    """Marks all decks in a folder as default, syncs them to Master Library, and optionally copies to all existing users."""
    folder = models.TMA_Folder.get_or_none(
        (models.TMA_Folder.id == folder_id) & (models.TMA_Folder.is_deleted == False)
    )
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    decks = list(models.TMA_Deck.select().where((models.TMA_Deck.folder == folder) & (models.TMA_Deck.is_deleted == False)))
    if not decks and req.is_default:
        raise HTTPException(status_code=400, detail="В этой папке нет активных колод!")

    now = datetime.datetime.now()
    all_users = list(models.TMAUser.select())
    copied_users_count = 0

    for d in decks:
        cards = list(models.TMA_Card.select().where((models.TMA_Card.deck == d) & (models.TMA_Card.is_deleted == False)))
        clean_name = d.name.replace("⭐ ", "").strip()

        meta = {}
        try:
            meta = json.loads(d.metadata or "{}")
        except Exception:
            pass
        meta["is_default"] = req.is_default
        meta["folder_name"] = folder.name
        meta["folder_color"] = folder.color or "#6366f1"
        d.metadata = json.dumps(meta)
        d.save()

        lib_deck = models.Deck.get_or_none(
            (models.Deck.is_deleted == False) &
            ((models.Deck.name == d.name) | (models.Deck.name == clean_name) | (models.Deck.name == f"⭐ {clean_name}"))
        )
        if req.is_default:
            lib_meta = {"folder_name": folder.name, "folder_color": folder.color or "#6366f1"}
            if not lib_deck:
                lib_deck = models.Deck.create(
                    name=clean_name, target_language=d.target_language or 'de',
                    level=d.level, topic=d.topic, is_default=True,
                    metadata=json.dumps(lib_meta), created_at=now, updated_at=now
                )
                card_objs = [
                    models.Card(
                        deck=lib_deck, front_text=c.front_text or "", back_text=c.back_text or "",
                        context=c.context or "", tags=c.tags or "[]", audio_path=c.audio_path or "",
                        audio_back_path=c.audio_back_path or "",
                        card_type=getattr(c, 'card_type', 'translation') or 'translation',
                        source=getattr(c, 'source', 'library') or 'library',
                        created_at=now, updated_at=now
                    )
                    for c in cards
                ]
                if card_objs:
                    models.Card.bulk_create(card_objs, batch_size=200)
            else:
                lib_deck.is_default = True
                lib_deck.metadata = json.dumps(lib_meta)
                lib_deck.updated_at = now
                lib_deck.save()
        else:
            if lib_deck:
                lib_deck.is_default = False
                lib_deck.save()

    if req.is_default and req.copy_to_existing:
        for u in all_users:
            if u.user_id == folder.user_id:
                continue
            user_folder, _ = models.TMA_Folder.get_or_create(
                user_id=u.user_id, name=folder.name,
                defaults={"color": folder.color or "#6366f1", "target_language": folder.target_language or "de",
                          "created_at": now, "updated_at": now}
            )
            user_got_new_decks = False
            for d in decks:
                clean_name = d.name.replace("⭐ ", "").strip()
                cards = list(models.TMA_Card.select().where((models.TMA_Card.deck == d) & (models.TMA_Card.is_deleted == False)))
                existing = models.TMA_Deck.get_or_none(
                    (models.TMA_Deck.user_id == u.user_id) & (models.TMA_Deck.is_deleted == False) &
                    ((models.TMA_Deck.name == d.name) | (models.TMA_Deck.name == clean_name))
                )
                if not existing:
                    new_d = models.TMA_Deck.create(
                        user_id=u.user_id, folder=user_folder, name=clean_name,
                        target_language=d.target_language, level=d.level, topic=d.topic,
                        metadata=json.dumps({"source_deck_id": d.id, "folder_name": folder.name, "is_default": True}),
                        created_at=now, updated_at=now
                    )
                    card_objs = [
                        models.TMA_Card(
                            deck=new_d, front_text=c.front_text or "", back_text=c.back_text or "",
                            context=c.context or "", tags=c.tags or "[]", audio_path=c.audio_path or "",
                            audio_back_path=c.audio_back_path or "",
                            card_type=getattr(c, 'card_type', 'translation') or 'translation',
                            source=getattr(c, 'source', 'default') or 'default',
                            creator_id=folder.user_id, created_at=now, updated_at=now
                        )
                        for c in cards
                    ]
                    if card_objs:
                        models.TMA_Card.bulk_create(card_objs, batch_size=200)
                    user_got_new_decks = True
            if user_got_new_decks:
                copied_users_count += 1

    action_word = "сделана дефолтной" if req.is_default else "снята с дефолтных"
    msg = f"Папка '{folder.name}' ({len(decks)} колод) {action_word}!"
    if req.copy_to_existing and req.is_default:
        msg += f" Добавлена в Библиотеку для новых пользователей и скопирована {copied_users_count} существующим пользователям."

    return {
        "status": "ok", "is_default": req.is_default, "folder_name": folder.name,
        "decks_count": len(decks), "copied_to_users": copied_users_count, "message": msg
    }


@router.post("/api/admin/folders/{folder_id}/assign")
def assign_folder(folder_id: int, req: AssignFolderRequest):
    """Copies entire folder and all its decks to specific users."""
    folder = models.TMA_Folder.get_or_none(
        (models.TMA_Folder.id == folder_id) & (models.TMA_Folder.is_deleted == False)
    )
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if req.mode == "default_all":
        return set_default_folder(folder_id, SetDefaultFolderRequest(is_default=True, copy_to_existing=True))

    decks = list(models.TMA_Deck.select().where((models.TMA_Deck.folder == folder) & (models.TMA_Deck.is_deleted == False)))
    now = datetime.datetime.now()
    processed_users = 0

    for uid in req.user_ids:
        user_folder, _ = models.TMA_Folder.get_or_create(
            user_id=uid, name=folder.name,
            defaults={"color": folder.color or "#6366f1", "target_language": folder.target_language or "de",
                      "created_at": now, "updated_at": now}
        )
        for d in decks:
            clean_d_name = d.name.replace("⭐ ", "").strip()
            existing_d = models.TMA_Deck.get_or_none(
                (models.TMA_Deck.user_id == uid) & (models.TMA_Deck.folder == user_folder) &
                (models.TMA_Deck.is_deleted == False) &
                ((models.TMA_Deck.name == d.name) | (models.TMA_Deck.name == clean_d_name))
            )
            if existing_d:
                continue

            cards = list(models.TMA_Card.select().where((models.TMA_Card.deck == d) & (models.TMA_Card.is_deleted == False)))
            new_d = models.TMA_Deck.create(
                user_id=uid, folder=user_folder, name=d.name,
                target_language=d.target_language, level=d.level, topic=d.topic,
                metadata=json.dumps({"assigned_from_deck_id": d.id}),
                created_at=now, updated_at=now
            )
            card_objs = [
                models.TMA_Card(
                    deck=new_d, front_text=c.front_text or '', back_text=c.back_text or '',
                    context=c.context or '', tags=c.tags or '[]', audio_path=c.audio_path or '',
                    audio_back_path=c.audio_back_path or '',
                    card_type=getattr(c, 'card_type', 'translation') or 'translation',
                    source=getattr(c, 'source', 'assigned') or 'assigned',
                    creator_id=folder.user_id, created_at=now, updated_at=now
                )
                for c in cards
            ]
            if card_objs:
                models.TMA_Card.bulk_create(card_objs, batch_size=200)
        processed_users += 1

    return {
        "status": "ok", "users_processed": processed_users,
        "message": f"Папка '{folder.name}' ({len(decks)} колод) успешно скопирована {processed_users} пользователям!"
    }
