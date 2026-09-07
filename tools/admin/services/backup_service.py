"""Backup configuration and file management for the Admin Panel."""
import json
import logging
import os
from typing import List

logger = logging.getLogger(__name__)

# project_root is two levels up from tools/admin/
_THIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))


def get_admin_config_path() -> str:
    return os.path.join(_THIS_DIR, "admin_config.json")


def load_admin_config() -> dict:
    p = get_admin_config_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"custom_backup_dir": ""}


def save_admin_config(data: dict) -> None:
    p = get_admin_config_path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_effective_backup_dir() -> str:
    """Returns the effective backup directory (custom if configured and accessible, else default)."""
    cfg = load_admin_config()
    custom_dir = cfg.get("custom_backup_dir", "").strip()
    if custom_dir:
        try:
            os.makedirs(custom_dir, exist_ok=True)
            if os.path.exists(custom_dir):
                return custom_dir
        except Exception as e:
            logger.warning(f"Could not use custom backup dir '{custom_dir}': {e}")
    default_dir = os.path.join(_PROJECT_ROOT, "backups")
    os.makedirs(default_dir, exist_ok=True)
    return default_dir


def get_all_backup_search_dirs() -> List[str]:
    """Returns all directories to search for backup files without duplicates."""
    cfg = load_admin_config()
    custom_dir = cfg.get("custom_backup_dir", "").strip()

    dirs = [
        os.path.join(_PROJECT_ROOT, "backups"),
        os.path.join(_PROJECT_ROOT, "api", "data", "backups"),
        os.path.join(_PROJECT_ROOT, "..", "data"),
    ]
    if custom_dir:
        dirs.insert(0, custom_dir)
        dirs.append(os.path.join(custom_dir, "backups"))

    valid_dirs = []
    seen = set()
    for d in dirs:
        try:
            norm = os.path.normpath(os.path.abspath(d))
            if norm not in seen and os.path.exists(norm) and os.path.isdir(norm):
                seen.add(norm)
                valid_dirs.append(norm)
        except Exception:
            pass
    return valid_dirs
