from fastapi import APIRouter, HTTPException, Depends
import logging

from api import services
from api.dependencies.auth import get_user_id

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/folders",
    tags=["folders"],
)

@router.get("")
def get_folders(user_id: int = Depends(get_user_id)):
    logger.info(f"GET /api/folders - authenticated account: {user_id}")
    return services.get_active_folders(user_id)

@router.post("")
def create_folder(data: dict, user_id: int = Depends(get_user_id)):
    name = data.get('name')
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Название папки не может быть пустым")
    
    parent_id = data.get('parent_id')
    color = data.get('color')
    target_language = data.get('target_language', 'de')
    
    try:
        folder = services.create_folder(name.strip(), user_id, parent_id, color, target_language)
        return {"status": "success", "id": folder.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reorder")
def reorder_folders(data: dict, user_id: int = Depends(get_user_id)):
    folder_ids = data.get('folder_ids', [])
    try:
        services.reorder_folders(folder_ids, user_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error reordering folders: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{folder_id}/rename")
def rename_folder(folder_id: int, data: dict, user_id: int = Depends(get_user_id)):
    name = data.get('name')
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="Название папки не может быть пустым")
        
    try:
        updated = services.rename_folder(folder_id, name.strip(), user_id)
        if updated:
            return {"status": "success", "name": updated.name}
        raise HTTPException(status_code=404, detail="Папка не найдена или доступ ограничен")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{folder_id}/color")
def change_folder_color(folder_id: int, data: dict, user_id: int = Depends(get_user_id)):
    color = data.get('color')
    try:
        updated = services.change_folder_color(folder_id, color, user_id)
        if updated:
            return {"status": "success", "color": updated.color}
        raise HTTPException(status_code=404, detail="Папка не найдена или доступ ограничен")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{folder_id}/move")
def move_folder(folder_id: int, data: dict, user_id: int = Depends(get_user_id)):
    parent_id = data.get('parent_id') # Can be None for root
    try:
        updated = services.move_folder(folder_id, parent_id, user_id)
        if updated:
            return {"status": "success"}
        raise HTTPException(status_code=404, detail="Папка не найдена или доступ ограничен")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{folder_id}")
def delete_folder(folder_id: int, user_id: int = Depends(get_user_id)):
    if services.delete_folder(folder_id, user_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Папка не найдена или доступ ограничен")
