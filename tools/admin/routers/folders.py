"""Admin folders router."""
import datetime
import json
import logging
from collections import defaultdict
from typing import List, Optional
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
def get_admin_folders(user_id: Optional[int] = None):
    """Returns all TMA folders with user info, deck counts, and default status."""
    query = models.TMA_Folder.select().where(models.TMA_Folder.is_deleted == False)
    if user_id is not None:
        query = query.where(models.TMA_Folder.user_id == user_id)
    folders = list(query)
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
            "decks": [
                {
                    "id": d.id,
                    "name": d.name,
                    "card_count": card_counts.get(d.id, 0),
                    "target_language": d.target_language or "de",
                    "level": d.level or ""
                }
                for d in decks
            ]
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


@router.post("/api/admin/folders/{folder_id}/to-library")
def promote_folder_to_library_direct(folder_id: int):
    """Direct 1-click endpoint to promote folder to Master Library."""
    return assign_folder(folder_id, AssignFolderRequest(mode="library"))


@router.post("/api/admin/folders/{folder_id}/overwrite-users")
def overwrite_folder_users_direct(folder_id: int):
    """Direct 1-click endpoint to completely overwrite folder for all users who have it."""
    return assign_folder(folder_id, AssignFolderRequest(mode="overwrite_all", target_audience="existing_copies"))


@router.post("/api/admin/folders/{folder_id}/assign")
def assign_folder(folder_id: int, req: AssignFolderRequest):
    """Copies entire folder, sets as default, promotes to library, makes collaborative, or overwrites for users."""
    folder = models.TMA_Folder.get_or_none(
        (models.TMA_Folder.id == folder_id) & (models.TMA_Folder.is_deleted == False)
    )
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if req.mode == "default_all":
        return set_default_folder(folder_id, SetDefaultFolderRequest(is_default=True, copy_to_existing=True))

    decks = list(models.TMA_Deck.select().where((models.TMA_Deck.folder == folder) & (models.TMA_Deck.is_deleted == False)))
    clean_f_name = folder.name.replace("⭐ ", "").strip()
    now = datetime.datetime.now()

    # 1. Mode: Library (Save entire folder and its decks/cards to Master Library)
    if req.mode == "library":
        lib_cat, _ = models.LibraryCategory.get_or_create(
            name=clean_f_name,
            defaults={
                "description": f"Папка {clean_f_name}",
                "icon": "folder"
            }
        )
        saved_decks_count = 0
        total_cards_count = 0
        with models.tma_db.atomic():
            for d in decks:
                clean_deck_name = d.name.replace("⭐ ", "").strip()
                cards = list(models.TMA_Card.select().where((models.TMA_Card.deck == d) & (models.TMA_Card.is_deleted == False)))
                lib_deck = models.Deck.get_or_none(
                    (models.Deck.is_deleted == False) &
                    ((models.Deck.name == d.name) | (models.Deck.name == clean_deck_name) | (models.Deck.name == f"⭐ {clean_deck_name}"))
                )
                lib_meta = {"folder_name": folder.name, "folder_color": folder.color or "#6366f1"}
                if not lib_deck:
                    lib_deck = models.Deck.create(
                        name=clean_deck_name,
                        target_language=d.target_language or folder.target_language or 'de',
                        level=d.level,
                        topic=d.topic,
                        category=lib_cat,
                        metadata=json.dumps(lib_meta),
                        created_at=now,
                        updated_at=now
                    )
                else:
                    lib_deck.category = lib_cat
                    lib_deck.metadata = json.dumps(lib_meta)
                    lib_deck.target_language = d.target_language or folder.target_language or 'de'
                    lib_deck.level = d.level
                    lib_deck.topic = d.topic
                    lib_deck.updated_at = now
                    lib_deck.save()

                # Cleanly replace cards in library deck
                models.Card.delete().where(models.Card.deck == lib_deck).execute()
                card_objs = [
                    models.Card(
                        deck=lib_deck, front_text=c.front_text or "", back_text=c.back_text or "",
                        context=c.context or "", tags=c.tags or "[]", audio_path=c.audio_path or "",
                        audio_back_path=c.audio_back_path or "",
                        card_type=getattr(c, 'card_type', 'translation') or 'translation',
                        source='library', created_at=now, updated_at=now
                    )
                    for c in cards
                ]
                if card_objs:
                    models.Card.bulk_create(card_objs, batch_size=200)
                saved_decks_count += 1
                total_cards_count += len(card_objs)

        return {
            "status": "ok", "mode": "library",
            "message": f"Папка '{folder.name}' ({saved_decks_count} колод, {total_cards_count} карт) успешно сохранена в Библиотеку!"
        }

    # 2. Mode: Overwrite All / Overwrite Selected (Complete replacement of folder contents for existing users)
    if req.mode in ("overwrite_all", "overwrite_selected"):
        target_uids = set()
        if req.mode == "overwrite_all" or req.target_audience in ("existing_copies", "all"):
            matching_folders = list(models.TMA_Folder.select(models.TMA_Folder.user_id).where(
                (models.TMA_Folder.is_deleted == False) &
                (models.TMA_Folder.user_id != folder.user_id) &
                ((models.TMA_Folder.name == folder.name) | (models.TMA_Folder.name == clean_f_name) | (models.TMA_Folder.name == f"⭐ {clean_f_name}"))
            ))
            target_uids = {mf.user_id for mf in matching_folders}
            if req.target_audience == "all":
                target_uids.update(u.user_id for u in models.TMAUser.select(models.TMAUser.user_id) if u.user_id != folder.user_id)
        if req.user_ids and req.mode == "overwrite_selected":
            target_uids = set(req.user_ids) - {folder.user_id}

        if not target_uids:
            return {"status": "ok", "users_processed": 0, "message": f"Пользователей с папкой '{folder.name}' не найдено."}

        overwritten_users = 0
        with models.tma_db.atomic():
            for uid in target_uids:
                user_folder = models.TMA_Folder.get_or_none(
                    (models.TMA_Folder.user_id == uid) & (models.TMA_Folder.is_deleted == False) &
                    ((models.TMA_Folder.name == folder.name) | (models.TMA_Folder.name == clean_f_name) | (models.TMA_Folder.name == f"⭐ {clean_f_name}"))
                )
                if not user_folder:
                    user_folder = models.TMA_Folder.create(
                        user_id=uid, name=folder.name, color=folder.color or "#6366f1",
                        target_language=folder.target_language or "de", created_at=now, updated_at=now
                    )
                else:
                    # Purge old decks, cards and progress inside user_folder
                    old_decks = list(models.TMA_Deck.select(models.TMA_Deck.id).where((models.TMA_Deck.folder == user_folder) & (models.TMA_Deck.is_deleted == False)))
                    old_deck_ids = [od.id for od in old_decks]
                    if old_deck_ids:
                        old_cards = list(models.TMA_Card.select(models.TMA_Card.id).where(models.TMA_Card.deck_id << old_deck_ids))
                        old_card_ids = [oc.id for oc in old_cards]
                        if old_card_ids:
                            models.TMAProgress.delete().where(models.TMAProgress.card_id << old_card_ids).execute()
                            models.TMA_Card.delete().where(models.TMA_Card.id << old_card_ids).execute()
                        models.TMA_Deck.delete().where(models.TMA_Deck.id << old_deck_ids).execute()

                    user_folder.color = folder.color or "#6366f1"
                    user_folder.target_language = folder.target_language or "de"
                    user_folder.updated_at = now
                    user_folder.save()

                # Bulk insert fresh decks and cards from master folder
                for d in decks:
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
                overwritten_users += 1

        return {
            "status": "ok", "users_processed": overwritten_users,
            "message": f"Папка '{folder.name}' успешно перезаписана у {overwritten_users} пользователей (старые материалы удалены, свежие залиты)!"
        }

    # 3. Mode: Collaborate (Grant collaborative access, optionally delete old copies)
    if req.mode == "collaborate":
        target_uids = set()
        if req.target_audience == "existing_copies":
            matching_folders = list(models.TMA_Folder.select(models.TMA_Folder.user_id).where(
                (models.TMA_Folder.is_deleted == False) &
                (models.TMA_Folder.user_id != folder.user_id) &
                ((models.TMA_Folder.name == folder.name) | (models.TMA_Folder.name == clean_f_name) | (models.TMA_Folder.name == f"⭐ {clean_f_name}"))
            ))
            target_uids = {mf.user_id for mf in matching_folders}
        elif req.target_audience == "all":
            target_uids = {u.user_id for u in models.TMAUser.select(models.TMAUser.user_id) if u.user_id != folder.user_id}
        else:
            target_uids = set(req.user_ids) - {folder.user_id}

        if not target_uids:
            return {"status": "ok", "users_processed": 0, "message": "Не выбрано ни одного пользователя для совместного доступа."}

        role = req.collaborator_role or "viewer"
        collaborator_count = 0
        deleted_copies_count = 0

        with models.tma_db.atomic():
            for uid in target_uids:
                # Add collaborator on folder
                models.TMA_Collaborator.get_or_create(
                    target_type="folder", target_id=folder.id, user_id=uid,
                    defaults={"role": role, "added_by": folder.user_id}
                )
                # Add collaborator on all decks inside folder
                for d in decks:
                    models.TMA_Collaborator.get_or_create(
                        target_type="deck", target_id=d.id, user_id=uid,
                        defaults={"role": role, "added_by": folder.user_id}
                    )
                collaborator_count += 1

                # Clean up existing separate copies if requested
                if req.delete_existing_copies:
                    user_copies = list(models.TMA_Folder.select().where(
                        (models.TMA_Folder.user_id == uid) & (models.TMA_Folder.id != folder.id) &
                        (models.TMA_Folder.is_deleted == False) &
                        ((models.TMA_Folder.name == folder.name) | (models.TMA_Folder.name == clean_f_name) | (models.TMA_Folder.name == f"⭐ {clean_f_name}"))
                    ))
                    for uf in user_copies:
                        u_decks = list(models.TMA_Deck.select(models.TMA_Deck.id).where((models.TMA_Deck.folder == uf) & (models.TMA_Deck.is_deleted == False)))
                        u_deck_ids = [ud.id for ud in u_decks]
                        if u_deck_ids:
                            u_card_ids = [uc.id for uc in models.TMA_Card.select(models.TMA_Card.id).where(models.TMA_Card.deck_id << u_deck_ids)]
                            if u_card_ids:
                                models.TMAProgress.delete().where(models.TMAProgress.card_id << u_card_ids).execute()
                                models.TMA_Card.delete().where(models.TMA_Card.id << u_card_ids).execute()
                            models.TMA_Deck.delete().where(models.TMA_Deck.id << u_deck_ids).execute()
                        uf.delete_instance()
                        deleted_copies_count += 1

        del_msg = f" (старые копии удалены у {deleted_copies_count} пользователей)" if deleted_copies_count > 0 else ""
        return {
            "status": "ok", "mode": "collaborate", "collaborators_added": collaborator_count,
            "message": f"Папка '{folder.name}' сделана совместной для {collaborator_count} пользователей (роль: {role}){del_msg}!"
        }

    # 4. Mode: Copy (Default copy to selected user IDs)
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
