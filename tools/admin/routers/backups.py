"""Admin backups router."""
import datetime
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from api import models
from tools.admin.schemas import BackupSettingsRequest, BatchDeleteBackupsRequest
from tools.admin.services.backup_service import (
    get_all_backup_search_dirs,
    get_effective_backup_dir,
    load_admin_config,
    save_admin_config,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/admin/backups")
def get_backups():
    """Lists all backup files in system backup folders and custom folder."""
    cfg = load_admin_config()
    custom_dir = cfg.get("custom_backup_dir", "").strip()
    search_dirs = get_all_backup_search_dirs()

    backup_files = []
    seen_paths = set()
    total_bytes = 0

    for d in search_dirs:
        try:
            for fname in os.listdir(d):
                if fname.endswith((".json", ".db", ".sql")):
                    fpath = os.path.normpath(os.path.join(d, fname))
                    if fpath in seen_paths or not os.path.isfile(fpath):
                        continue
                    seen_paths.add(fpath)

                    try:
                        stat = os.stat(fpath)
                        size = stat.st_size
                        total_bytes += size
                        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)

                        if fname.startswith("deck_"):
                            b_type = "Снимок колоды"
                        elif fname.startswith("cards_backup_"):
                            b_type = "Снимок карточек"
                        elif "supabase" in fname:
                            b_type = "Supabase дамп"
                        elif fname.endswith(".db"):
                            b_type = "SQLite БД"
                        else:
                            b_type = "Полная БД"

                        card_count = None
                        deck_name = None

                        if fname.endswith(".json") and size < 15000000:
                            try:
                                with open(fpath, "r", encoding="utf-8") as jf:
                                    jdata = json.load(jf)
                                    if isinstance(jdata, dict):
                                        if "cards_count" in jdata:
                                            card_count = jdata["cards_count"]
                                        if "deck" in jdata and "name" in jdata["deck"]:
                                            deck_name = jdata["deck"]["name"]
                                        elif "cards" in jdata and isinstance(jdata["cards"], list):
                                            card_count = len(jdata["cards"])
                                    elif isinstance(jdata, list):
                                        card_count = len(jdata)
                            except Exception:
                                pass

                        backup_files.append({
                            "filename": fname,
                            "folder": d,
                            "filepath": fpath,
                            "size_kb": round(size / 1024, 1),
                            "size_mb": round(size / (1024 * 1024), 2),
                            "type": b_type,
                            "card_count": card_count,
                            "deck_name": deck_name,
                            "created_at": mtime.strftime("%Y-%m-%d %H:%M:%S")
                        })
                    except Exception:
                        pass
        except Exception:
            pass

    backup_files.sort(key=lambda x: x["created_at"], reverse=True)
    return {
        "backups": backup_files,
        "total_count": len(backup_files),
        "total_size_mb": round(total_bytes / (1024 * 1024), 2),
        "custom_dir": custom_dir
    }


@router.post("/api/admin/backups/settings")
def save_backup_settings(req: BackupSettingsRequest):
    """Saves custom local backup directory path."""
    target_dir = req.custom_dir.strip()
    if target_dir and not os.path.exists(target_dir):
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot create directory: {str(e)}")

    cfg = load_admin_config()
    cfg["custom_backup_dir"] = target_dir
    save_admin_config(cfg)
    return {"status": "ok", "custom_backup_dir": target_dir}


@router.post("/api/admin/backups/create")
def create_full_db_backup():
    """Creates a full JSON snapshot of all database tables in the configured backup directory."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"full_db_backup_{timestamp}.json"
    target_dir = get_effective_backup_dir()
    fpath = os.path.join(target_dir, fname)

    users = [u.__data__ for u in models.TMAUser.select()]
    for u in users:
        for k in ("created_at", "updated_at"):
            if isinstance(u.get(k), datetime.datetime):
                u[k] = str(u[k])

    decks = [d.__data__ for d in models.TMA_Deck.select() if not d.is_deleted]
    for d in decks:
        for k in ("created_at", "updated_at"):
            if isinstance(d.get(k), datetime.datetime):
                d[k] = str(d[k])

    cards = [c.__data__ for c in models.TMA_Card.select() if not c.is_deleted]
    for c in cards:
        for k in ("created_at", "updated_at"):
            if isinstance(c.get(k), datetime.datetime):
                c[k] = str(c[k])
        c.pop("image_data", None)

    dump_data = {
        "timestamp": timestamp,
        "users_count": len(users),
        "decks_count": len(decks),
        "cards_count": len(cards),
        "users": users,
        "decks": decks,
        "cards": cards
    }

    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(dump_data, f, ensure_ascii=False, indent=2)

    return {
        "status": "ok",
        "filename": fname,
        "filepath": fpath,
        "folder": target_dir,
        "users_count": len(users),
        "decks_count": len(decks),
        "cards_count": len(cards)
    }


@router.get("/api/admin/backups/download/{filename}")
def download_backup_file(filename: str, folder: Optional[str] = Query(None)):
    """Serves a backup file for downloading."""
    search_dirs = [folder] if folder else get_all_backup_search_dirs()
    for d in search_dirs:
        if not d or not os.path.exists(d):
            continue
        fp = os.path.normpath(os.path.join(d, filename))
        if os.path.exists(fp) and os.path.isfile(fp):
            return FileResponse(fp, filename=filename)
    raise HTTPException(status_code=404, detail="Backup file not found")


@router.delete("/api/admin/backups/{filename}")
def delete_backup_file(filename: str, folder: Optional[str] = Query(None)):
    """Deletes a specified backup file from backup directory."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid backup filename")

    search_dirs = [folder] if folder else get_all_backup_search_dirs()
    deleted = []

    for d in search_dirs:
        if not d or not os.path.exists(d):
            continue
        fp = os.path.normpath(os.path.join(d, filename))
        norm_dir = os.path.normpath(d)
        if not fp.startswith(norm_dir):
            continue
        if os.path.exists(fp) and os.path.isfile(fp):
            try:
                os.remove(fp)
                deleted.append(fp)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to delete {filename}: {str(e)}")

    if not deleted:
        raise HTTPException(status_code=404, detail="Backup file not found to delete")

    return {"status": "ok", "deleted_filename": filename, "deleted_paths": deleted, "count": len(deleted)}


@router.post("/api/admin/backups/batch-delete")
def batch_delete_backups(req: BatchDeleteBackupsRequest):
    """Deletes multiple selected backup files."""
    if not req.backups:
        raise HTTPException(status_code=400, detail="No backup files provided for deletion")

    search_dirs = get_all_backup_search_dirs()
    deleted = []
    errors = []

    for item in req.backups:
        fn = item.filename
        if not fn or ".." in fn or "/" in fn or "\\" in fn:
            errors.append(f"Invalid filename: {fn}")
            continue

        dirs_to_check = [item.folder] if item.folder else search_dirs
        for d in dirs_to_check:
            if not d or not os.path.exists(d):
                continue
            fp = os.path.normpath(os.path.join(d, fn))
            norm_dir = os.path.normpath(d)
            if not fp.startswith(norm_dir):
                continue
            if os.path.exists(fp) and os.path.isfile(fp):
                try:
                    os.remove(fp)
                    deleted.append(fp)
                except Exception as e:
                    errors.append(f"Failed to delete {fn}: {str(e)}")

    return {"status": "ok", "deleted_count": len(deleted), "deleted_paths": deleted, "errors": errors}
