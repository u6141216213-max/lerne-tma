from fastapi import APIRouter, HTTPException, Depends
import logging

from api import services
from api.dependencies.auth import get_user_id

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/trash",
    tags=["trash"],
)

@router.get("")
def get_trash(user_id: int = Depends(get_user_id)):
    logger.info(f"GET /api/trash - authenticated account: {user_id}")
    return services.trash.get_trash_items(user_id)

@router.post("/deck/{deck_id}/restore")
def restore_trash_deck(deck_id: int, user_id: int = Depends(get_user_id)):
    logger.info(f"POST /api/trash/deck/{deck_id}/restore - authenticated account: {user_id}")
    if services.trash.restore_deck(deck_id, user_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Deck not found or restore failed")

@router.post("/card/{card_id}/restore")
def restore_trash_card(card_id: int, user_id: int = Depends(get_user_id)):
    logger.info(f"POST /api/trash/card/{card_id}/restore - authenticated account: {user_id}")
    if services.trash.restore_card(card_id, user_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Card not found or restore failed")

@router.delete("/clear")
def clear_trash(user_id: int = Depends(get_user_id)):
    logger.info(f"DELETE /api/trash/clear - authenticated account: {user_id}")
    if services.trash.clear_trash(user_id):
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to clear trash")
