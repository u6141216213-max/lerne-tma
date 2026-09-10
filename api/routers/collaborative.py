import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import Optional

from api.models import TMAUser, TMA_Folder, TMA_Deck
from api.dependencies.auth import get_user_id
from api.services import collaborative_service

router = APIRouter(
    prefix="/collaborative",
    tags=["collaborative"]
)

logger = logging.getLogger(__name__)


class AddCollaboratorRequest(BaseModel):
    user_identifier: str  # @username or user_id string
    role: str = 'viewer'  # 'editor' or 'viewer'
    can_edit_audio: bool = False


class UpdateRoleRequest(BaseModel):
    user_id_to_update: int
    role: str  # 'editor' or 'viewer'
    can_edit_audio: bool = False


class AdminBulkDeleteFoldersRequest(BaseModel):
    folder_ids: list[int]
    user_ids: list[int]


@router.get("/check-access")
def check_access(type: str, id: int, user_id: int = Depends(get_user_id)):
    """Returns effective user role on deck or folder."""
    if type not in ['deck', 'folder']:
        raise HTTPException(status_code=400, detail="Invalid target type")
    
    role = collaborative_service.get_effective_user_role(user_id, type, id)
    collaborators = collaborative_service.get_collaborators(type, id)
    is_shared = len(collaborators) > 1
    
    return {
        "target_type": type,
        "target_id": id,
        "role": role,
        "can_edit": role in ['owner', 'editor'],
        "can_edit_audio": collaborative_service.can_edit_audio(user_id, type, id),
        "is_shared": is_shared,
        "is_owner": role == 'owner',
        "collaborators_count": len(collaborators)
    }


@router.post("/admin/folders/bulk-delete")
def admin_bulk_delete_folders(req: AdminBulkDeleteFoldersRequest, user_id: int = Depends(get_user_id)):
    """Soft-deletes selected users' folders and their contents."""
    admin_id = int(os.environ.get("ADMIN_USER_ID", "642478257"))
    if user_id != admin_id:
        raise HTTPException(status_code=403, detail="Only admins can delete user folders")
    if not req.folder_ids or not req.user_ids:
        raise HTTPException(status_code=422, detail="folder_ids and user_ids must not be empty")
    if len(req.folder_ids) > 100 or len(req.user_ids) > 100:
        raise HTTPException(status_code=422, detail="Too many folders or users in one request")
    try:
        return collaborative_service.admin_bulk_delete_folders(req.folder_ids, req.user_ids)
    except Exception:
        logger.exception("Admin bulk folder deletion failed")
        raise HTTPException(status_code=500, detail="Failed to delete selected folders")


@router.get("/{target_type}/{target_id}/collaborators")
def get_collaborators(target_type: str, target_id: int, user_id: int = Depends(get_user_id)):
    """Gets list of all collaborators for folder or deck."""
    role = collaborative_service.get_effective_user_role(user_id, target_type, target_id)
    if not role:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return {
        "collaborators": collaborative_service.get_collaborators(target_type, target_id),
        "user_role": role
    }


@router.post("/{target_type}/{target_id}/add")
def add_collaborator(target_type: str, target_id: int, req: AddCollaboratorRequest, user_id: int = Depends(get_user_id)):
    """Adds a collaborator by username or ID."""
    identifier = req.user_identifier.strip()
    if identifier.startswith("@"):
        identifier = identifier[1:]

    user_to_add = None
    if identifier.isdigit():
        user_to_add = TMAUser.get_or_none(TMAUser.user_id == int(identifier))
    else:
        user_to_add = TMAUser.get_or_none(TMAUser.username == identifier)

    if not user_to_add:
        raise HTTPException(status_code=404, detail=f"User '{req.user_identifier}' not found in app database")

    try:
        res = collaborative_service.add_collaborator(target_type, target_id, user_to_add.user_id, req.role, added_by=user_id, can_edit_audio=req.can_edit_audio)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{target_type}/{target_id}/role")
def update_collaborator_role(target_type: str, target_id: int, req: UpdateRoleRequest, user_id: int = Depends(get_user_id)):
    """Updates role for a collaborator."""
    try:
        res = collaborative_service.update_collaborator_role(target_type, target_id, req.user_id_to_update, req.role, requester_id=user_id, can_edit_audio=req.can_edit_audio)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/join/{share_id}")
def join_by_share_id(share_id: str, user_id: int = Depends(get_user_id)):
    """Allows a user to join a shared item as a viewer via share link."""
    try:
        res = collaborative_service.join_by_share_id(share_id, user_id)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{target_type}/{target_id}/remove/{user_id_to_remove}")
def remove_collaborator(target_type: str, target_id: int, user_id_to_remove: int, user_id: int = Depends(get_user_id)):
    """Removes a collaborator from folder or deck."""
    try:
        success = collaborative_service.remove_collaborator(target_type, target_id, user_id_to_remove, requester_id=user_id)
        if success:
            return {"status": "ok"}
        else:
            raise HTTPException(status_code=404, detail="Collaborator not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{target_type}/{target_id}/remove-all")
def remove_all_collaborators(target_type: str, target_id: int, user_id: int = Depends(get_user_id)):
    """Completely closes shared access for a folder or deck by removing all collaborators."""
    try:
        count = collaborative_service.remove_all_collaborators(target_type, target_id, requester_id=user_id)
        return {"status": "ok", "removed_count": count}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/folder/{folder_id}/group-progress")
def get_group_progress(folder_id: int, user_id: int = Depends(get_user_id)):
    """Gets dashboard and leaderboard progress for all collaborators of a folder."""
    try:
        return collaborative_service.get_group_progress(folder_id, requester_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{target_type}/{target_id}/presence")
def get_presence(target_type: str, target_id: int, user_id: int = Depends(get_user_id)):
    """Records caller's presence heartbeat and returns online status of all collaborators + target updated_at timestamp."""
    if target_type not in ['deck', 'folder']:
        raise HTTPException(status_code=400, detail="Invalid target type")
    
    role = collaborative_service.get_effective_user_role(user_id, target_type, target_id)
    if not role:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return collaborative_service.record_and_get_presence(user_id, target_type, target_id)
