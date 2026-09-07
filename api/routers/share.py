import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Body, Request, Header
from fastapi.responses import HTMLResponse, Response

from api.models import TMAUser, TMA_Deck, TMA_Card, TMA_Folder, TMAMedia
from api.dependencies.auth import get_user_id
from api.auth.dependencies import get_bearer_token
from api.auth.errors import AuthError
from api.auth.service import authenticate
from api.services.sharing_service import SharingService
from api.templates.share_templates import get_share_html, get_share_error_html

router = APIRouter(tags=["share"])
logger = logging.getLogger(__name__)

@router.post("/share/generate/{type}/{item_id}")
def generate_share(type: str, item_id: int, data: dict = Body(None), user_id: int = Depends(get_user_id)):
    """Generates or retrieves a unique share_id for a deck, folder, or card."""
    if type == "deck":
        prefix = "d_"
        item = TMA_Deck.get_or_none((TMA_Deck.id == item_id) & (TMA_Deck.is_deleted == False))
        if not item:
            raise HTTPException(status_code=404, detail="Deck not found")
        if item.user_id != user_id:
            from api.models import TMA_Collaborator
            collab = TMA_Collaborator.get_or_none(
                (TMA_Collaborator.target_type == 'deck') & 
                (TMA_Collaborator.target_id == item_id) & 
                (TMA_Collaborator.user_id == user_id)
            )
            if not collab:
                raise HTTPException(status_code=403, detail="Access denied")
    elif type == "folder":
        prefix = "f_"
        item = TMA_Folder.get_or_none((TMA_Folder.id == item_id) & (TMA_Folder.is_deleted == False))
        if not item:
            raise HTTPException(status_code=404, detail="Folder not found")
        if item.user_id != user_id:
            from api.models import TMA_Collaborator
            collab = TMA_Collaborator.get_or_none(
                (TMA_Collaborator.target_type == 'folder') & 
                (TMA_Collaborator.target_id == item_id) & 
                (TMA_Collaborator.user_id == user_id)
            )
            if not collab:
                raise HTTPException(status_code=403, detail="Access denied")
    elif type == "card":
        prefix = "c_"
        item = TMA_Card.get_or_none((TMA_Card.id == item_id) & (TMA_Card.is_deleted == False))
        if not item:
            raise HTTPException(status_code=404, detail="Card not found")
    else:
        raise HTTPException(status_code=400, detail="Invalid item type")
        
    if not item.share_id:
        item.share_id = SharingService.generate_unique_share_id(prefix)
        item.save()
    
    share_id = item.share_id

    # Handle Screenshot if provided
    if data and "screenshot" in data:
        SharingService.save_screenshot(share_id, data["screenshot"])

    return {"status": "ok", "share_id": share_id}


@router.get("/share/info/{share_id}")
def get_share_info(share_id: str, authorization: Optional[str] = Header(None)):
    """Gets public info about a shared item with fast user comparison."""
    is_collab = share_id.startswith("collab_")
    clean_id = share_id.replace("collab_", "").strip()

    uid = None
    if authorization:
        try:
            uid = authenticate(get_bearer_token(authorization)).account_id
        except (AuthError, HTTPException):
            # Shared material remains public; a bad optional credential never
            # selects an arbitrary account for comparison.
            pass

    if clean_id.startswith("d_"):
        deck = TMA_Deck.get_or_none((TMA_Deck.share_id == clean_id) & (TMA_Deck.is_deleted == False))
        if not deck:
            raise HTTPException(status_code=404, detail="Shared deck not found")
            
        creator = TMAUser.get_or_none(TMAUser.user_id == deck.user_id)
        cards_count = TMA_Card.select().where((TMA_Card.deck == deck) & (TMA_Card.is_deleted == False)).count()

        comparison = {
            "already_exists": False,
            "existing_id": None,
            "has_updates": False,
            "new_cards_count": 0,
            "my_cards_count": 0
        }
        if uid:
            clean_deck_name = deck.name.replace("⭐ ", "").strip()
            existing_deck = TMA_Deck.get_or_none(
                (TMA_Deck.user_id == uid) &
                (TMA_Deck.is_deleted == False) &
                ((TMA_Deck.name == deck.name) | (TMA_Deck.name == clean_deck_name))
            )
            if existing_deck:
                my_cards_count = TMA_Card.select().where((TMA_Card.deck == existing_deck) & (TMA_Card.is_deleted == False)).count()
                my_pairs = set(
                    (c.front_text or "", c.back_text or "") for c in TMA_Card.select(TMA_Card.front_text, TMA_Card.back_text).where(
                        (TMA_Card.deck == existing_deck) & (TMA_Card.is_deleted == False)
                    )
                )
                src_cards = list(TMA_Card.select(TMA_Card.front_text, TMA_Card.back_text).where(
                    (TMA_Card.deck == deck) & (TMA_Card.is_deleted == False)
                ))
                missing_cards = sum(1 for c in src_cards if (c.front_text or "", c.back_text or "") not in my_pairs)
                comparison = {
                    "already_exists": True,
                    "existing_id": existing_deck.id,
                    "has_updates": missing_cards > 0,
                    "new_cards_count": missing_cards,
                    "my_cards_count": my_cards_count
                }

        return {
            "type": "deck",
            "id": deck.id,
            "name": deck.name,
            "level": deck.level,
            "topic": deck.topic,
            "target_language": deck.target_language or "de",
            "cards_count": cards_count,
            "creator_name": creator.username or creator.first_name if creator else "Unknown",
            "creator_avatar": creator.photo_url if creator else None,
            "is_collab": is_collab,
            "comparison": comparison
        }
    elif clean_id.startswith("f_"):
        folder = TMA_Folder.get_or_none((TMA_Folder.share_id == clean_id) & (TMA_Folder.is_deleted == False))
        if not folder:
            raise HTTPException(status_code=404, detail="Shared folder not found")
            
        creator = TMAUser.get_or_none(TMAUser.user_id == folder.user_id)
        decks_count = TMA_Deck.select().where((TMA_Deck.folder == folder) & (TMA_Deck.is_deleted == False)).count()
        cards_count = TMA_Card.select().join(TMA_Deck).where((TMA_Deck.folder == folder) & (TMA_Card.is_deleted == False) & (TMA_Deck.is_deleted == False)).count()

        comparison = {
            "already_exists": False,
            "existing_id": None,
            "has_updates": False,
            "new_decks_count": 0,
            "new_cards_count": 0,
            "my_decks_count": 0,
            "my_cards_count": 0
        }
        if uid:
            clean_f_name = folder.name.replace("⭐ ", "").strip()
            existing_folder = TMA_Folder.get_or_none(
                (TMA_Folder.user_id == uid) &
                (TMA_Folder.parent.is_null()) &
                (TMA_Folder.is_deleted == False) &
                ((TMA_Folder.name == folder.name) | (TMA_Folder.name == clean_f_name))
            )
            if existing_folder:
                my_decks = list(TMA_Deck.select().where((TMA_Deck.folder == existing_folder) & (TMA_Deck.is_deleted == False)))
                my_deck_names = {d.name.replace("⭐ ", "").strip() for d in my_decks}
                my_deck_ids = [d.id for d in my_decks]
                my_cards_count = 0
                if my_deck_ids:
                    my_cards_count = TMA_Card.select().where((TMA_Card.deck_id << my_deck_ids) & (TMA_Card.is_deleted == False)).count()

                src_decks = list(TMA_Deck.select().where((TMA_Deck.folder == folder) & (TMA_Deck.is_deleted == False)))
                src_deck_names = {d.name.replace("⭐ ", "").strip() for d in src_decks}
                missing_decks = [name for name in src_deck_names if name not in my_deck_names]
                new_cards_count = max(0, cards_count - my_cards_count)

                comparison = {
                    "already_exists": True,
                    "existing_id": existing_folder.id,
                    "has_updates": len(missing_decks) > 0 or new_cards_count > 0,
                    "new_decks_count": len(missing_decks),
                    "new_cards_count": new_cards_count,
                    "my_decks_count": len(my_decks),
                    "my_cards_count": my_cards_count
                }

        return {
            "type": "folder",
            "id": folder.id,
            "name": folder.name,
            "color": folder.color,
            "target_language": folder.target_language or "de",
            "decks_count": decks_count,
            "cards_count": cards_count,
            "creator_name": creator.username or creator.first_name if creator else "Unknown",
            "creator_avatar": creator.photo_url if creator else None,
            "is_collab": is_collab,
            "comparison": comparison
        }
    elif clean_id.startswith("c_"):
        card = TMA_Card.get_or_none((TMA_Card.share_id == clean_id) & (TMA_Card.is_deleted == False))
        if not card:
            raise HTTPException(status_code=404, detail="Shared card not found")
            
        creator_id = card.creator_id or (card.deck.user_id if card.deck else None)
        creator = TMAUser.get_or_none(TMAUser.user_id == creator_id)
        card_lang = (card.deck.target_language if card.deck else "de") or "de"
        return {
            "type": "card",
            "id": card.id,
            "front_text": card.front_text,
            "back_text": card.back_text,
            "image_path": card.image_path,
            "target_language": card_lang,
            "creator_name": creator.username or creator.first_name if creator else "Unknown",
            "creator_avatar": creator.photo_url if creator else None,
            "is_collab": is_collab
        }

    else:
        raise HTTPException(status_code=400, detail="Invalid share link format")

@router.post("/share/import")
def import_shared_item(payload: dict = Body(...), user_id: int = Depends(get_user_id)):
    """Imports a shared card into Inbox or a shared deck as a new standalone deck."""
    share_id = payload.get("share_id")
    if not share_id:
        raise HTTPException(status_code=400, detail="share_id is required")

    logger.info(f"IMPORT: User {user_id} is importing item {share_id}")
    try:
        resolution = payload.get("resolution")
        result = SharingService.import_item(share_id, user_id, resolution=resolution)
        logger.info(f"IMPORT SUCCESS: {share_id} for user {user_id}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"IMPORT CRITICAL ERROR: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error during import: {str(e)}")


@router.get("/share/v/{share_id}", response_class=HTMLResponse)
def view_shared_item(share_id: str, request: Request):
    """Returns a page with OpenGraph tags for beautiful link preview in Telegram/Socials."""
    host = request.headers.get("host", "tma-amber.vercel.app")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    domain = f"{scheme}://{host}"
    if "localhost" in host or "127.0.0.1" in host or scheme == "http":
        domain = "https://tma-amber.vercel.app"

    try:
        info = get_share_info(share_id)
    except HTTPException:
        return HTMLResponse(
            status_code=404,
            content=get_share_error_html(
                "Материал не найден",
                "Похоже, ссылка устарела или автор удалил эту колоду/карточку.",
                home_url=f"{domain}/"
            )
        )
    except Exception as e:
        logger.error(f"Error fetching share info for {share_id}: {e}")
        return HTMLResponse(
            status_code=500,
            content=get_share_error_html(
                "Ошибка загрузки",
                "Не удалось загрузить данные предпросмотра. Попробуйте открыть приложение напрямую.",
                home_url=f"{domain}/"
            )
        )

    title = info.get("name") or info.get("front_text") or "Lerne TMA"
    creator = info.get('creator_name', 'Пользователь Lerne')
    if info.get("type") == "deck":
        description = f"Колода от {creator} | Уровень: {info.get('level', '—')} | Тема: {info.get('topic', '—')}"
    elif info.get("type") == "folder":
        description = f"Папка от {creator} | Колоды: {info.get('decks_count', 0)} | Карточки: {info.get('cards_count', 0)}"
    else:
        description = f"Карточка от {creator} для изучения в Lerne"

    v_param = abs(hash(f"{title}_{info.get('type')}_{info.get('id')}")) % 1000000
    preview_url = f"{domain}/api/preview/{share_id}.jpg?v={v_param}"
    app_url = f"https://t.me/LerneDeutsch287_bot?startapp={share_id}"
    tg_scheme_url = f"tg://resolve?domain=LerneDeutsch287_bot&startapp={share_id}"
    web_url = f"{domain}/?share_id={share_id}"

    return get_share_html(title, description, preview_url, app_url, web_url, tg_scheme_url=tg_scheme_url)


@router.get("/preview/{share_id}.jpg")
@router.get("/share/v/{share_id}/preview.png")
@router.get("/share/v/{share_id}/preview.jpg")
def get_share_preview_image(share_id: str):
    """Returns a stored screenshot or generates a beautiful premium preview image."""
    filename = f"preview_{share_id}.png"
    media = TMAMedia.get_or_none(TMAMedia.filename == filename, TMAMedia.folder == 'previews')
    
    headers = {
        "Cache-Control": "public, max-age=31536000",
        "Access-Control-Allow-Origin": "*"
    }
    
    if media:
        return Response(content=bytes(media.content), media_type="image/jpeg", headers=headers)

    info = get_share_info(share_id)
    img_data = SharingService.get_preview_image(info, share_id)
    return Response(content=img_data, media_type="image/jpeg", headers=headers)



