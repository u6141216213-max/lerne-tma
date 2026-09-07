import logging
import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.models import TMAUser, TMALinkedSession, TMAAuthCode
from api.dependencies.auth import get_user_id

router = APIRouter(tags=["auth"])
logger = logging.getLogger(__name__)

class UserSyncSchema(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    phone: Optional[str] = None
    is_guest: bool = False
    guest_id: Optional[int] = None
    active_language: Optional[str] = None
    native_language: Optional[str] = None
    has_selected_language: Optional[bool] = None

class UserLanguageSchema(BaseModel):
    active_language: Optional[str] = None
    native_language: Optional[str] = None
    has_selected_language: bool = True

class CodeVerifySchema(BaseModel):
    code: str
    guest_id: Optional[int] = None

class CodeGenerateSchema(BaseModel):
    user_id: int

@router.post("/auth/sync")
def sync_user(data: UserSyncSchema, user_id: int = Depends(get_user_id)):
    """
    Silently registers or updates user info from Telegram or guest session.
    """
    try:
        user = TMAUser.get_or_none(TMAUser.user_id == user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="invalid_session")
        created = False
        
        # Merge guest data if guest_id was provided and differs from user_id
        if data.guest_id and data.guest_id != user_id:
            raise HTTPException(status_code=410, detail="legacy_guest_merge_retired")
        
        # Update info if provided in request (usually from Telegram WebApp)
        if data.first_name and data.first_name != "Пользователь": 
            user.first_name = data.first_name
        if data.last_name: user.last_name = data.last_name
        if data.username: user.username = data.username
        if data.photo_url: user.photo_url = data.photo_url
        if data.phone: user.phone = data.phone
        if data.active_language: user.active_language = data.active_language
        if data.native_language: user.native_language = data.native_language
        
        # Clean up legacy "Пользователь" if present
        if user.first_name == "Пользователь":
            user.first_name = user.username or None
        
        # Protect has_selected_language: if DB is already True, do NOT overwrite with False from cold start
        if data.has_selected_language is True:
            user.has_selected_language = True
        
        # Logic: If we have a name (from this request or already in DB), it's NOT a guest.
        has_identifying_info = (user.first_name and user.first_name != "Пользователь") or user.username
        
        if has_identifying_info:
            user.is_guest = False
            logger.info(f"User {user_id} identified as REAL USER (is_guest=False)")
        else:
            user.is_guest = data.is_guest
            logger.info(f"User {user_id} remains GUEST (is_guest={user.is_guest})")
            
        user.updated_at = datetime.datetime.now()
        user.save()
        
        clean_first_name = user.first_name if (user.first_name and user.first_name != "Пользователь") else (user.username or None)

        status = "created" if created else "updated"
        return {
            "status": "ok",
            "action": status,
            "user": {
                "user_id": user.user_id,
                "first_name": clean_first_name,
                "last_name": user.last_name,
                "username": user.username,
                "photo_url": user.photo_url,
                "phone": user.phone,
                "is_guest": user.is_guest,
                "active_language": user.active_language or "de",
                "native_language": getattr(user, 'native_language', None) or "uk",
                "has_selected_language": bool(user.has_selected_language)
            }
        }
    except Exception as e:
        logger.error(f"Sync error for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during sync")

@router.get("/auth/me")
def get_me(user_id: int = Depends(get_user_id)):
    """Returns current user info."""
    user = TMAUser.get_or_none(TMAUser.user_id == user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "user_id": user.user_id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "photo_url": user.photo_url,
        "phone": user.phone,
        "is_guest": user.is_guest,
        "active_language": user.active_language or "de",
        "native_language": getattr(user, 'native_language', None) or "uk",
        "has_selected_language": bool(user.has_selected_language)
    }

@router.post("/user/language")
def update_user_language(data: UserLanguageSchema, user_id: int = Depends(get_user_id)):
    user = TMAUser.get_or_none(TMAUser.user_id == user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid_session")
    if data.active_language:
        lang = (data.active_language or "de").lower().strip()
        if lang in ["de", "en", "no"]:
            user.active_language = lang
    if data.native_language:
        nlang = (data.native_language or "uk").lower().strip()
        if nlang in ["uk", "ru", "en"]:
            user.native_language = nlang
    if data.has_selected_language is True:
        user.has_selected_language = True
    user.updated_at = datetime.datetime.now()
    user.save()
    return {
        "status": "ok",
        "active_language": user.active_language or "de",
        "native_language": getattr(user, 'native_language', None) or "uk",
        "has_selected_language": bool(user.has_selected_language)
    }

@router.post("/auth/session")
def create_session(guest_id: int):
    """Creates a pending auth session for polling."""
    raise HTTPException(status_code=410, detail="legacy_auth_retired")
    session, created = TMALinkedSession.get_or_create(guest_id=guest_id)
    if not (session.is_confirmed and session.telegram_id):
        session.is_confirmed = False
        session.telegram_id = None
        session.created_at = datetime.datetime.now()
        session.save()
    return {"status": "ok", "guest_id": guest_id}

@router.get("/auth/session/{guest_id}")
def check_session(guest_id: int):
    """Checks if the session was confirmed by the bot."""
    raise HTTPException(status_code=410, detail="legacy_auth_retired")
    session = TMALinkedSession.get_or_none(TMALinkedSession.guest_id == guest_id)
    if not session:
        return {"status": "not_found"}
    
    if session.is_confirmed and session.telegram_id:
        from api import services
        services.merge_guest_data(guest_id, session.telegram_id)
        # Fetch full user profile for the frontend
        user = TMAUser.get_or_none(TMAUser.user_id == session.telegram_id)
        return {
            "status": "completed", 
            "user_id": session.telegram_id,
            "user": {
                "user_id": user.user_id if user else session.telegram_id,
                "first_name": (user.first_name if (user.first_name and user.first_name != "Пользователь") else (user.username or None)) if user else None,
                "last_name": user.last_name if user else None,
                "username": user.username if user else None,
                "photo_url": user.photo_url if user else None,
                "phone": user.phone if user else None,
                "is_guest": False
            }
        }
    
    return {"status": "pending"}

@router.post("/auth/code/generate")
def generate_auth_code(data: CodeGenerateSchema):
    """Generates a 6-digit one-time code for account login (valid for 15 minutes)."""
    raise HTTPException(status_code=410, detail="legacy_auth_retired")
    import random
    from api.models import TMAAuthCode, TMAUser
    
    user = TMAUser.get_or_none(TMAUser.user_id == data.user_id)
    if not user:
        user = TMAUser.create(user_id=data.user_id, is_guest=False)
    
    # Invalidate previous unused codes for this user
    TMAAuthCode.update(is_used=True).where(
        (TMAAuthCode.user_id == data.user_id) & (TMAAuthCode.is_used == False)
    ).execute()
    
    code = f"{random.randint(100000, 999999)}"
    for _ in range(10):
        if not TMAAuthCode.select().where((TMAAuthCode.code == code) & (TMAAuthCode.is_used == False)).exists():
            break
        code = f"{random.randint(100000, 999999)}"
        
    auth_code = TMAAuthCode.create(
        code=code,
        user_id=data.user_id,
        created_at=datetime.datetime.now(),
        is_used=False
    )
    return {"status": "ok", "code": auth_code.code, "expires_in_minutes": 15}

@router.post("/auth/code/verify")
def verify_auth_code(data: CodeVerifySchema):
    """Verifies a 6-digit one-time code entered by user in APK or Web."""
    raise HTTPException(status_code=410, detail="legacy_auth_retired")
    from api.models import TMAAuthCode, TMAUser
    from api import services
    
    raw_code = (data.code or "").replace(" ", "").replace("-", "").strip()
    if not raw_code.isdigit() or len(raw_code) != 6:
        raise HTTPException(status_code=400, detail="Код должен состоять из 6 цифр")
    
    record = TMAAuthCode.get_or_none(
        (TMAAuthCode.code == raw_code) & (TMAAuthCode.is_used == False)
    )
    if not record:
        raise HTTPException(status_code=400, detail="Неверный или уже использованный код")
    
    # Check expiration (15 minutes)
    now = datetime.datetime.now()
    if (now - record.created_at).total_seconds() > 900:
        record.is_used = True
        record.save()
        raise HTTPException(status_code=400, detail="Срок действия кода истек (15 минут). Запросите новый код в боте командой /code")
    
    # Mark code as used
    record.is_used = True
    record.save()
    
    target_user_id = record.user_id
    # Merge guest data if guest_id was provided and differs from target user_id
    if data.guest_id and data.guest_id != target_user_id:
        try:
            services.merge_guest_data(data.guest_id, target_user_id)
        except Exception as e:
            logger.error(f"Error merging guest data during code verify: {e}")
            
    user = TMAUser.get_or_none(TMAUser.user_id == target_user_id)
    clean_first_name = (user.first_name if (user and user.first_name and user.first_name != "Пользователь") else (user.username if user else None)) or None

    return {
        "status": "ok",
        "user_id": target_user_id,
        "user": {
            "user_id": target_user_id,
            "first_name": clean_first_name,
            "last_name": user.last_name if user else None,
            "username": user.username if user else None,
            "photo_url": user.photo_url if user else None,
            "phone": user.phone if user else None,
            "is_guest": False,
            "active_language": user.active_language if user else "de",
            "native_language": getattr(user, 'native_language', None) if user else "uk",
            "has_selected_language": bool(user.has_selected_language) if user else True
        }
    }

@router.delete("/auth/account")
def delete_account(user_id: int = Depends(get_user_id)):
    """Deletes all user decks, cards, folders, progress, and account data."""
    try:
        from api.models import (
            TMA_Folder, TMA_Deck, TMA_Card, TMAProgress, 
            TMAReviewHistory, TMA_Collaborator, TMACustomPrompt, 
            TMALinkedSession, TMAUser, tma_db
        )
        with tma_db.atomic():
            user_deck_ids = [d.id for d in TMA_Deck.select(TMA_Deck.id).where(TMA_Deck.user_id == user_id)]
            if user_deck_ids:
                user_card_ids = [c.id for c in TMA_Card.select(TMA_Card.id).where(TMA_Card.deck_id << user_deck_ids)]
                if user_card_ids:
                    TMAProgress.delete().where(TMAProgress.card_id << user_card_ids).execute()
                    TMAReviewHistory.delete().where(TMAReviewHistory.card_id << user_card_ids).execute()
                    TMA_Card.delete().where(TMA_Card.id << user_card_ids).execute()
                TMA_Deck.delete().where(TMA_Deck.id << user_deck_ids).execute()
            
            TMA_Folder.delete().where(TMA_Folder.user_id == user_id).execute()
            TMAProgress.delete().where(TMAProgress.user_id == user_id).execute()
            TMAReviewHistory.delete().where(TMAReviewHistory.user_id == user_id).execute()
            TMA_Collaborator.delete().where(TMA_Collaborator.user_id == user_id).execute()
            TMACustomPrompt.delete().where(TMACustomPrompt.user_id == user_id).execute()
            TMALinkedSession.delete().where((TMALinkedSession.guest_id == user_id) | (TMALinkedSession.telegram_id == user_id)).execute()
            TMAUser.delete().where(TMAUser.user_id == user_id).execute()
        
        logger.info(f"Account and all data deleted for user_id={user_id}")
        return {"status": "ok", "message": "Account and all associated data deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting account for user_id={user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

