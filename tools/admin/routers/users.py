"""Admin users router."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from peewee import fn

from api import models
from tools.admin.schemas import BatchDeleteUsersRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/admin/users")
def get_users(search: Optional[str] = None):
    """Returns list of users with their deck counts, sorted so registered users are first."""
    query = models.TMAUser.select()
    if search:
        s = search.strip()
        if s.isdigit():
            query = query.where(models.TMAUser.user_id == int(s))
        else:
            query = query.where(
                (models.TMAUser.username.contains(s)) |
                (models.TMAUser.first_name.contains(s)) |
                (models.TMAUser.last_name.contains(s))
            )

    users = list(query)

    user_deck_counts = dict(
        models.TMA_Deck.select(models.TMA_Deck.user_id, fn.COUNT(models.TMA_Deck.id))
        .where(models.TMA_Deck.is_deleted == False)
        .group_by(models.TMA_Deck.user_id)
        .tuples()
    )

    deck_user_ids = set(user_deck_counts.keys())
    existing_uids = set([u.user_id for u in users])
    missing_uids = deck_user_ids - existing_uids

    registered_users = []
    guest_users = []

    for u in users:
        deck_count = user_deck_counts.get(u.user_id, 0)
        last_act = u.updated_at or u.created_at
        is_guest = bool(getattr(u, 'is_guest', False))
        is_registered = (not is_guest) and bool((u.first_name or u.username) and u.user_id > 0)

        user_data = {
            "id": u.user_id,
            "user_id": u.user_id,
            "username": u.username or "",
            "first_name": u.first_name or "",
            "last_name": u.last_name or "",
            "photo_url": getattr(u, 'photo_url', '') or "",
            "is_guest": is_guest,
            "is_registered": is_registered,
            "created_at": str(u.created_at) if u.created_at else None,
            "last_activity": str(last_act) if last_act else None,
            "deck_count": deck_count
        }

        if is_registered:
            registered_users.append(user_data)
        else:
            guest_users.append(user_data)

    for m_uid in missing_uids:
        if search and search.strip().isdigit() and int(search.strip()) != m_uid:
            continue
        deck_count = user_deck_counts.get(m_uid, 0)
        guest_users.append({
            "id": m_uid, "user_id": m_uid, "username": f"user_{m_uid}",
            "first_name": f"User {m_uid}", "last_name": "", "photo_url": "",
            "is_guest": True, "is_registered": False,
            "created_at": None, "last_activity": None, "deck_count": deck_count
        })

    registered_users.sort(key=lambda x: x["last_activity"] or x["created_at"] or "", reverse=True)
    guest_users.sort(key=lambda x: x["last_activity"] or x["created_at"] or "", reverse=True)
    sorted_result = registered_users + guest_users

    return {
        "users": sorted_result,
        "total_count": len(sorted_result),
        "registered_count": len(registered_users),
        "guest_count": len(guest_users)
    }


@router.delete("/api/admin/users/guests")
@router.post("/api/admin/users/cleanup-guests")
def cleanup_guest_accounts():
    """Removes all guest and anonymous accounts along with their temporary decks, cards and progress."""
    try:
        guest_users = list(models.TMAUser.select().where(models.TMAUser.is_guest == True))
        guest_user_ids = [u.user_id for u in guest_users]

        guest_sessions = list(models.TMALinkedSession.select().where(models.TMALinkedSession.guest_id.is_null(False)))
        for s in guest_sessions:
            if s.guest_id not in guest_user_ids:
                guest_user_ids.append(s.guest_id)

        if not guest_user_ids:
            return {"status": "success", "message": "Гостевые аккаунты не найдены", "deleted_users_count": 0, "deleted_decks_count": 0}

        deleted_decks_count = 0
        deleted_cards_count = 0

        with models.tma_db.atomic():
            guest_deck_ids = [d.id for d in models.TMA_Deck.select(models.TMA_Deck.id).where(models.TMA_Deck.user_id << guest_user_ids)]
            if guest_deck_ids:
                deleted_cards_count = models.TMA_Card.delete().where(models.TMA_Card.deck_id << guest_deck_ids).execute()
                deleted_decks_count = models.TMA_Deck.delete().where(models.TMA_Deck.id << guest_deck_ids).execute()

            models.TMAProgress.delete().where(models.TMAProgress.user_id << guest_user_ids).execute()
            models.TMAReviewHistory.delete().where(models.TMAReviewHistory.user_id << guest_user_ids).execute()
            models.TMACustomPrompt.delete().where(models.TMACustomPrompt.user_id << guest_user_ids).execute()
            models.TMAUserPrompt.delete().where(models.TMAUserPrompt.user_id << guest_user_ids).execute()
            models.TMALinkedSession.delete().where(
                (models.TMALinkedSession.guest_id << guest_user_ids) | (models.TMALinkedSession.telegram_id << guest_user_ids)
            ).execute()
            deleted_users_count = models.TMAUser.delete().where(models.TMAUser.user_id << guest_user_ids).execute()

        logger.info(f"Cleaned up {deleted_users_count} guest users and {deleted_decks_count} decks")
        return {
            "status": "success",
            "message": f"Удалено {deleted_users_count} гостевых аккаунтов, {deleted_decks_count} колод и {deleted_cards_count} карточек",
            "deleted_users_count": deleted_users_count,
            "deleted_decks_count": deleted_decks_count,
            "deleted_cards_count": deleted_cards_count
        }
    except Exception as e:
        logger.error(f"Failed to cleanup guest accounts: {e}", exc_info=True)


@router.post("/api/admin/purge-deleted")
def purge_deleted_items():
    """Permanently purges all soft-deleted items (is_deleted == True) across folders, decks, and cards."""
    try:
        purged_cards = 0
        purged_decks = 0
        purged_folders = 0
        purged_lib_cards = 0
        purged_lib_decks = 0

        with models.tma_db.atomic():
            del_card_ids = [c.id for c in models.TMA_Card.select(models.TMA_Card.id).where(models.TMA_Card.is_deleted == True)]
            if del_card_ids:
                models.TMAProgress.delete().where(models.TMAProgress.card_id << del_card_ids).execute()
                models.TMAReviewHistory.delete().where(models.TMAReviewHistory.card_id << del_card_ids).execute()
                purged_cards = models.TMA_Card.delete().where(models.TMA_Card.id << del_card_ids).execute()

            del_deck_ids = [d.id for d in models.TMA_Deck.select(models.TMA_Deck.id).where(models.TMA_Deck.is_deleted == True)]
            if del_deck_ids:
                child_card_ids = [c.id for c in models.TMA_Card.select(models.TMA_Card.id).where(models.TMA_Card.deck_id << del_deck_ids)]
                if child_card_ids:
                    models.TMAProgress.delete().where(models.TMAProgress.card_id << child_card_ids).execute()
                    models.TMAReviewHistory.delete().where(models.TMAReviewHistory.card_id << child_card_ids).execute()
                    purged_cards += models.TMA_Card.delete().where(models.TMA_Card.id << child_card_ids).execute()
                models.TMA_Collaborator.delete().where(
                    (models.TMA_Collaborator.target_type == 'deck') & (models.TMA_Collaborator.target_id << del_deck_ids)
                ).execute()
                purged_decks = models.TMA_Deck.delete().where(models.TMA_Deck.id << del_deck_ids).execute()

            del_folder_ids = [f.id for f in models.TMA_Folder.select(models.TMA_Folder.id).where(models.TMA_Folder.is_deleted == True)]
            if del_folder_ids:
                rem_decks = [d.id for d in models.TMA_Deck.select(models.TMA_Deck.id).where(models.TMA_Deck.folder_id << del_folder_ids)]
                if rem_decks:
                    rem_cards = [c.id for c in models.TMA_Card.select(models.TMA_Card.id).where(models.TMA_Card.deck_id << rem_decks)]
                    if rem_cards:
                        models.TMAProgress.delete().where(models.TMAProgress.card_id << rem_cards).execute()
                        models.TMAReviewHistory.delete().where(models.TMAReviewHistory.card_id << rem_cards).execute()
                        purged_cards += models.TMA_Card.delete().where(models.TMA_Card.id << rem_cards).execute()
                    models.TMA_Collaborator.delete().where(
                        (models.TMA_Collaborator.target_type == 'deck') & (models.TMA_Collaborator.target_id << rem_decks)
                    ).execute()
                    purged_decks += models.TMA_Deck.delete().where(models.TMA_Deck.id << rem_decks).execute()

                models.TMA_Collaborator.delete().where(
                    (models.TMA_Collaborator.target_type == 'folder') & (models.TMA_Collaborator.target_id << del_folder_ids)
                ).execute()
                models.TMA_Folder.update(parent_id=None).where(models.TMA_Folder.id << del_folder_ids).execute()
                purged_folders = models.TMA_Folder.delete().where(models.TMA_Folder.id << del_folder_ids).execute()

            del_lib_decks = [d.id for d in models.Deck.select(models.Deck.id).where(models.Deck.is_deleted == True)]
            if del_lib_decks:
                purged_lib_cards += models.Card.delete().where(models.Card.deck_id << del_lib_decks).execute()
                purged_lib_decks += models.Deck.delete().where(models.Deck.id << del_lib_decks).execute()

            purged_lib_cards += models.Card.delete().where(models.Card.is_deleted == True).execute()

        total_cards = purged_cards + purged_lib_cards
        total_decks = purged_decks + purged_lib_decks
        logger.info(f"Purged deleted items: {purged_folders} folders, {total_decks} decks, {total_cards} cards")
        return {
            "status": "success",
            "message": f"Очищено {purged_folders} папок, {total_decks} колод и {total_cards} карточек",
            "purged_folders": purged_folders,
            "purged_decks": total_decks,
            "purged_cards": total_cards
        }
    except Exception as e:
        logger.error(f"Failed to purge deleted items: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при очистке удалённых элементов: {str(e)}")


@router.delete("/api/admin/users/{user_id}")
def delete_single_user(user_id: int):
    """Deletes a specific user and all their personal decks, cards, folders, and data."""
    try:
        deleted_cards = 0
        deleted_decks = 0
        deleted_folders = 0
        with models.tma_db.atomic():
            user_deck_ids = [d.id for d in models.TMA_Deck.select(models.TMA_Deck.id).where(models.TMA_Deck.user_id == user_id)]
            if user_deck_ids:
                deleted_cards = models.TMA_Card.delete().where(models.TMA_Card.deck_id << user_deck_ids).execute()
                deleted_decks = models.TMA_Deck.delete().where(models.TMA_Deck.id << user_deck_ids).execute()

            deleted_folders = models.TMA_Folder.delete().where(models.TMA_Folder.user_id == user_id).execute()
            models.TMAProgress.delete().where(models.TMAProgress.user_id == user_id).execute()
            models.TMAReviewHistory.delete().where(models.TMAReviewHistory.user_id == user_id).execute()
            models.TMA_Collaborator.delete().where(
                (models.TMA_Collaborator.user_id == user_id) | (models.TMA_Collaborator.added_by == user_id)
            ).execute()
            models.TMAFeedback.delete().where(models.TMAFeedback.user_id == user_id).execute()
            models.TMACustomPrompt.delete().where(models.TMACustomPrompt.user_id == user_id).execute()
            models.TMAUserPrompt.delete().where(models.TMAUserPrompt.user_id == user_id).execute()
            models.TMALinkedSession.delete().where(
                (models.TMALinkedSession.guest_id == user_id) | (models.TMALinkedSession.telegram_id == user_id)
            ).execute()
            models.TMAUser.delete().where(models.TMAUser.user_id == user_id).execute()

        return {
            "status": "success",
            "message": f"Пользователь #{user_id} удалён ({deleted_decks} колод, {deleted_folders} папок, {deleted_cards} карточек)"
        }
    except Exception as e:
        logger.error(f"Failed to delete user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при удалении пользователя: {str(e)}")


@router.post("/api/admin/users/batch-delete")
def batch_delete_users(req: BatchDeleteUsersRequest):
    """Deletes multiple selected users and all their associated decks, cards, folders, and data."""
    if not req.user_ids:
        raise HTTPException(status_code=400, detail="Не указаны ID пользователей для удаления")

    try:
        clean_user_ids = []
        for uid in req.user_ids:
            try:
                clean_user_ids.append(int(uid))
            except (ValueError, TypeError):
                pass

        user_ids = list(set(clean_user_ids))
        if not user_ids:
            raise HTTPException(status_code=400, detail="Не найдено корректных числовых ID пользователей")

        deleted_users_count = 0
        deleted_decks_count = 0
        deleted_cards_count = 0
        deleted_folders_count = 0

        with models.tma_db.atomic():
            user_deck_ids = [d.id for d in models.TMA_Deck.select(models.TMA_Deck.id).where(models.TMA_Deck.user_id << user_ids)]
            if user_deck_ids:
                deleted_cards_count = models.TMA_Card.delete().where(models.TMA_Card.deck_id << user_deck_ids).execute()
                deleted_decks_count = models.TMA_Deck.delete().where(models.TMA_Deck.id << user_deck_ids).execute()

            deleted_folders_count = models.TMA_Folder.delete().where(models.TMA_Folder.user_id << user_ids).execute()
            models.TMAProgress.delete().where(models.TMAProgress.user_id << user_ids).execute()
            models.TMAReviewHistory.delete().where(models.TMAReviewHistory.user_id << user_ids).execute()
            models.TMA_Collaborator.delete().where(
                (models.TMA_Collaborator.user_id << user_ids) | (models.TMA_Collaborator.added_by << user_ids)
            ).execute()
            models.TMAFeedback.delete().where(models.TMAFeedback.user_id << user_ids).execute()
            models.TMACustomPrompt.delete().where(models.TMACustomPrompt.user_id << user_ids).execute()
            models.TMAUserPrompt.delete().where(models.TMAUserPrompt.user_id << user_ids).execute()
            models.TMALinkedSession.delete().where(
                (models.TMALinkedSession.guest_id << user_ids) | (models.TMALinkedSession.telegram_id << user_ids)
            ).execute()
            deleted_users_count = models.TMAUser.delete().where(models.TMAUser.user_id << user_ids).execute()

        return {
            "status": "success",
            "message": f"Удалено {deleted_users_count} пользователей, {deleted_decks_count} колод, {deleted_folders_count} папок, {deleted_cards_count} карточек",
            "deleted_users_count": deleted_users_count,
            "deleted_decks_count": deleted_decks_count,
            "deleted_folders_count": deleted_folders_count,
            "deleted_cards_count": deleted_cards_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to batch delete users {req.user_ids}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при массовом удалении пользователей: {str(e)}")
