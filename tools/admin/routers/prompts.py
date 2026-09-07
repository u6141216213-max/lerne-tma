"""Admin prompts router."""
from typing import Optional

from fastapi import APIRouter
from api import models

router = APIRouter()


@router.get("/api/admin/prompts")
def get_prompts(native_lang: Optional[str] = "uk", target_lang: Optional[str] = "de"):
    """Returns list of all available system and custom prompts with full instruction texts."""
    from api.services.language_service import get_system_presets
    n_lang = (native_lang or "uk").lower().strip()
    t_lang = (target_lang or "de").lower().strip()
    system_presets = get_system_presets(target_lang=t_lang, native_lang=n_lang)

    prompts_list = []
    for p in system_presets:
        prompts_list.append({
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "instruction": p.get("instruction", ""),
            "target_lang": t_lang,
            "is_default": p["id"] == "preset_b1"
        })

    try:
        customs = list(models.TMACustomPrompt.select().where(models.TMACustomPrompt.is_active == True))
        for c in customs:
            prompts_list.append({
                "id": f"custom_{c.id}",
                "name": f"⭐ {c.name or 'Кастомный промпт #' + str(c.id)}",
                "description": c.description or "Пользовательский промпт из настроек",
                "instruction": c.translation_prompt or c.context_prompt or "",
                "target_lang": getattr(c, 'target_language', 'de') or t_lang,
                "is_default": False
            })
    except Exception:
        pass

    return {"prompts": prompts_list, "native_lang": n_lang, "target_lang": t_lang}
