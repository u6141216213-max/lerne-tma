import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from uuid import UUID
from typing import List, Optional

from api.dependencies.auth import get_user_id
from api.services.sync_service import execute_sync_push, execute_sync_pull, execute_collab_pull


router = APIRouter(
    prefix="/sync",
    tags=["sync"],
)

# --- Pydantic Request/Response Models ---

class SyncDeckItem(BaseModel):
    id: int
    name: str
    level: Optional[str] = None
    topic: Optional[str] = None
    is_deleted: bool = False
    is_pinned: bool = False
    position: int = 0
    folder_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class SyncCardItem(BaseModel):
    id: int
    deck_id: int
    front_text: str
    back_text: str
    context: Optional[str] = None
    image_path: Optional[str] = None
    audio_path: Optional[str] = None
    audio_back_path: Optional[str] = None
    video_front_path: Optional[str] = None
    video_back_path: Optional[str] = None
    is_deleted: bool = False
    flag: Optional[int] = 0
    position: Optional[int] = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class SyncProgressItem(BaseModel):
    card_id: int
    queue: str
    interval: int
    ease_factor: float
    repetitions: int
    lapses: int
    step_index: Optional[int] = None
    next_review: Optional[str] = None
    last_reviewed: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class SyncFolderItem(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    color: Optional[str] = None
    target_language: Optional[str] = 'de'
    is_deleted: bool = False
    is_pinned: bool = False
    position: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class PushRequest(BaseModel):
    folders: List[SyncFolderItem] = []
    decks: List[SyncDeckItem] = []
    cards: List[SyncCardItem] = []
    progress: List[SyncProgressItem] = []


class OfflineDeckItem(SyncDeckItem):
    target_language: str = 'de'
    metadata: Optional[str] = None


class OfflineCardItem(SyncCardItem):
    tags: Optional[str] = None
    metadata: Optional[str] = None
    card_type: str = 'standard'


class OfflinePushRequest(BaseModel):
    request_id: UUID
    folders: List[SyncFolderItem] = Field(default_factory=list)
    decks: List[OfflineDeckItem] = Field(default_factory=list)
    cards: List[OfflineCardItem] = Field(default_factory=list)
    progress: List[SyncProgressItem] = Field(default_factory=list)


@router.post('/v2/push')
def push_offline_changes(request: OfflinePushRequest, user_id: int = Depends(get_user_id)) -> dict:
    from api.services.offline_sync import push_offline
    return push_offline(request, user_id)


@router.get('/v2/pull')
def pull_offline_changes(user_id: int = Depends(get_user_id)) -> dict:
    from api.services.offline_sync import pull_offline
    return pull_offline(user_id)


# --- Endpoints ---

@router.post("/push")
def push_changes(request: PushRequest, user_id: int = Depends(get_user_id)):
    """Receives offline changes from client, inserts/updates them via service."""
    return execute_sync_push(request, user_id)


@router.get("/pull")
def pull_changes(since: Optional[str] = None, user_id: int = Depends(get_user_id)):
    """Returns all changes that happened on the server since the given timestamp for this user."""
    return execute_sync_pull(since, user_id)


@router.get("/collab-pull")
def collab_pull_changes(since: Optional[str] = None, user_id: int = Depends(get_user_id)):
    """Returns collaborative changes from ALL participants in shared folders since the given timestamp.
    Used for real-time sync polling: cards, decks, folders changed by any collaborator."""
    result = execute_collab_pull(since, user_id)
    from api.services.collaborative_service import get_access_version
    result["access_version"] = get_access_version(user_id)
    return result
