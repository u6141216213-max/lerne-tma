from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
import logging

from api import services
from api.dependencies.auth import get_user_id

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/decks",
    tags=["decks"],
)

@router.get("")
def get_decks(user_id: int = Depends(get_user_id)):
    logger.info(f"GET /api/decks - authenticated account: {user_id}")
    return services.get_active_decks(user_id)
    
@router.post("")
def create_deck(data: dict, user_id: int = Depends(get_user_id)):
    from api import models
    user = models.TMAUser.get_or_none(models.TMAUser.user_id == user_id)
    if user and user.is_guest:
        raise HTTPException(status_code=403, detail="Для создания колод требуется авторизация через Telegram.")
    deck_type = data.get('deck_type', 'standard')
    try:
        deck = services.create_deck(data.get('name'), user_id, data.get('folder_id'), data.get('target_language', 'de'), deck_type=deck_type)
        return {"status": "success", "id": deck.id}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.post("/{deck_id}/move")
def move_deck(deck_id: int, data: dict, user_id: int = Depends(get_user_id)):
    folder_id = data.get('folder_id')
    try:
        updated = services.move_deck_to_folder(deck_id, folder_id, user_id)
        if updated:
            return {"status": "success"}
        raise HTTPException(status_code=404, detail="Deck not found or access denied")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{deck_id}/copy")
def copy_deck(deck_id: int, data: dict, user_id: int = Depends(get_user_id)):
    folder_id = data.get('folder_id')
    try:
        copied = services.copy_deck_to_folder(deck_id, folder_id, user_id)
        if copied:
            return {"status": "success", "id": copied.id}
        raise HTTPException(status_code=404, detail="Deck not found or access denied")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{deck_id}")
def delete_deck(deck_id: int, user_id: int = Depends(get_user_id)):
    if services.delete_deck(deck_id, user_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Deck not found or access denied")

@router.post("/{deck_id}/rename")
def rename_deck(deck_id: int, data: dict, user_id: int = Depends(get_user_id)):
    name = data.get('name')
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    
    try:
        updated_deck = services.rename_deck(deck_id, name.strip(), user_id)
        if updated_deck:
            return {"status": "success", "name": updated_deck.name}
        raise HTTPException(status_code=404, detail="Deck not found or access denied")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{deck_id}/toggle-learning")
def toggle_deck_learning(deck_id: int, data: dict = None, user_id: int = Depends(get_user_id)):
    incoming_status = data.get('is_learning') if data else None
    new_status = services.toggle_deck_learning(deck_id, user_id, is_learning=incoming_status)
    if new_status is not None:
        return {"status": "success", "is_learning": new_status}
    raise HTTPException(status_code=404, detail="Deck not found or access denied")

@router.post("/import-json")
def import_json_deck(data: dict, user_id: int = Depends(get_user_id)):
    logger.info(f"POST /api/decks/import-json - authenticated account: {user_id}")
    result = services.import_deck_from_json(data, user_id)
    if result:
        return {"status": "success", "deck_id": result.id}
    raise HTTPException(status_code=400, detail="Import failed: invalid data or empty deck")

@router.post("/{deck_id}/reset")
def reset_deck(deck_id: int, user_id: int = Depends(get_user_id)):
    if services.reset_deck_progress(user_id, deck_id):
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to reset progress")

@router.get("/{deck_id}/cards")
def get_deck_cards(deck_id: int, user_id: int = Depends(get_user_id)):
    return services.get_cards_for_study(deck_id, user_id)

from pydantic import BaseModel

class SyncRequest(BaseModel):
    mode: str = 'merge'

@router.post("/{deck_id}/sync")
def sync_deck(deck_id: int, request: SyncRequest = None, user_id: int = Depends(get_user_id)):
    mode = request.mode if request else 'merge'
    if services.sync_deck_with_library(user_id, deck_id, mode=mode):
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to sync deck")

# External/Library decks
@router.get("/external")
def get_external_decks(target_language: str = None):
    return services.get_external_decks(target_language)

@router.get("/external/categories")
def get_library_categories():
    return services.get_library_categories()

class BatchImportRequest(BaseModel):
    deck_ids: list[int]
    mode: Optional[str] = 'merge'
    force_trash: Optional[bool] = False

@router.post("/external/import/{deck_id}")
def import_external_deck(deck_id: int, mode: Optional[str] = 'merge', force_trash: bool = False, user_id: int = Depends(get_user_id)):
    logger.info(f"POST /api/decks/external/import/{deck_id} (mode={mode}, force_trash={force_trash}) - authenticated account: {user_id}")
    result = services.import_deck(deck_id, user_id, mode=mode or 'merge', force_trash=force_trash)
    if isinstance(result, dict) and result.get("status") == "in_trash":
        return result
    if result:
        return {"status": "success", "deck_id": getattr(result, 'id', result)}
    raise HTTPException(status_code=404, detail="External deck not found or import failed")

@router.post("/external/import-batch")
def import_external_decks_batch(body: BatchImportRequest, user_id: int = Depends(get_user_id)):
    logger.info(f"POST /api/decks/external/import-batch - authenticated account: {user_id}, count={len(body.deck_ids)}, mode={body.mode}, force_trash={body.force_trash}")
    imported_ids = services.import_decks_batch(body.deck_ids, user_id, mode=body.mode or 'merge', force_trash=body.force_trash or False)
    return {"status": "success", "imported_deck_ids": imported_ids}


@router.post("/external/{deck_id}/toggle-default")
def toggle_default_deck(deck_id: int, user_id: int = Depends(get_user_id)):
    logger.info(f"POST /api/decks/external/{deck_id}/toggle-default - authenticated account: {user_id}")
    ADMIN_USER_ID = 642478257
    if user_id != ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail="Only admins can toggle default decks")
    try:
        is_default = services.toggle_default_deck(deck_id)
        return {"status": "success", "is_default": is_default}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{deck_id}/pin")
def toggle_pin_deck(deck_id: int, user_id: int = Depends(get_user_id)):
    from api import models
    import datetime
    try:
        deck = models.TMA_Deck.get_or_none((models.TMA_Deck.id == deck_id) & (models.TMA_Deck.user_id == user_id))
        if not deck:
            raise HTTPException(status_code=404, detail="Deck not found or access denied")
        deck.is_pinned = not deck.is_pinned
        deck.updated_at = datetime.datetime.now()
        deck.save()
        return {"status": "success", "is_pinned": deck.is_pinned}
    except Exception as e:
        logger.error(f"Error toggling pin: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reorder")
def reorder_decks(data: dict, user_id: int = Depends(get_user_id)):
    from api import models
    deck_ids = data.get('deck_ids', [])
    try:
        with models.tma_db.atomic():
            for idx, deck_id in enumerate(deck_ids):
                models.TMA_Deck.update(position=idx).where(
                    (models.TMA_Deck.id == deck_id) & (models.TMA_Deck.user_id == user_id)
                ).execute()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error reordering decks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{deck_id}/metadata")
def update_deck_metadata(deck_id: int, data: dict, user_id: int = Depends(get_user_id)):
    try:
        updated_deck = services.update_deck_metadata(deck_id, data, user_id)
        if updated_deck:
            import json
            raw_meta = updated_deck.metadata
            parsed_meta = {"resources": []}
            if raw_meta:
                try:
                    parsed_meta = json.loads(raw_meta)
                except Exception: pass
            
            resolved_resources = []
            for res in parsed_meta.get('resources', []):
                res_type = res.get('type')
                path = res.get('path')
                url = res.get('url')
                if path:
                    if res_type == 'image':
                        url = services.resolve_media_url(path, 'images')
                    elif res_type == 'audio':
                        url = services.resolve_media_url(path, 'audio')
                    elif res_type == 'video':
                        url = services.resolve_media_url(path, 'videos')
                item = {**res}
                if url:
                    item['url'] = url
                resolved_resources.append(item)
            parsed_meta['resources'] = resolved_resources
            return {"status": "success", "metadata": parsed_meta}
        raise HTTPException(status_code=404, detail="Deck not found or access denied")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


