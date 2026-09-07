from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
import logging

from api import models
from api import services
from api.dependencies.auth import get_user_id

import os

ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "642478257"))

router = APIRouter(
    tags=["settings"],
)

# User Settings (Custom Prompts Manager)
from api import ai_service



@router.get("/user/settings")
def get_user_settings_endpoint(user_id: int = Depends(get_user_id)):
    import json
    try:
        setting = models.TMASetting.get_or_none(models.TMASetting.key == f"USER_SETTINGS_{user_id}")
        if setting and setting.value:
            return json.loads(setting.value)
    except Exception as e:
        logger.error(f"Error fetching user settings for {user_id}: {e}")
    return {}

@router.post("/user/settings")
def save_user_settings_endpoint(data: dict, user_id: int = Depends(get_user_id)):
    import json, datetime
    try:
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Settings must be a dictionary")
        setting, _ = models.TMASetting.get_or_create(
            key=f"USER_SETTINGS_{user_id}",
            defaults={"value": "{}", "updated_at": datetime.datetime.now()}
        )
        current = {}
        if setting.value:
            try:
                current = json.loads(setting.value)
            except Exception:
                current = {}
        current.update(data)
        setting.value = json.dumps(current, ensure_ascii=False)
        setting.updated_at = datetime.datetime.now()
        setting.save()
        return {"status": "success", "settings": current}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving user settings for {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save user settings")

@router.get("/user/reminder-settings")
def get_user_reminder_settings_endpoint(user_id: int = Depends(get_user_id)):
    return services.get_user_reminder_settings(user_id)

@router.post("/user/reminder-settings")
def save_user_reminder_settings_endpoint(data: dict, user_id: int = Depends(get_user_id)):
    success = services.save_user_reminder_settings(user_id, data)
    if success:
        return {"status": "success", "settings": services.get_user_reminder_settings(user_id)}
    raise HTTPException(status_code=500, detail="Failed to save reminder settings")

@router.get("/user/prompts")
def get_user_prompts(target_language: str = "de", native_language: str = None, user_id: int = Depends(get_user_id)):
    custom_prompts = []
    active_standard_prompt_id = None
    active_trainer_prompt_id = None
    active_exam_prompt_id = None
    active_standard_preset_id = None
    active_trainer_preset_id = None
    active_exam_preset_id = None
    target_lang = (target_language or "de").lower().strip()
    
    from api.services.language_service import get_language_config, get_system_presets, get_prompt_for_phrase
    if not native_language:
        native_rec = models.TMASetting.get_or_none(models.TMASetting.key == "NATIVE_LANGUAGE")
        native_language = native_rec.value if native_rec else "ru"
        
    native_lang = (native_language or "ru").lower().strip()
    lang_cfg = get_language_config(target_lang, native_lang)
    presets = get_system_presets(target_lang, native_lang)
    
    try:
        query = models.TMACustomPrompt.select().where(
            (models.TMACustomPrompt.user_id == user_id) & 
            ((models.TMACustomPrompt.target_language == target_lang) | (models.TMACustomPrompt.target_language.is_null() if target_lang == 'de' else False))
        ).order_by(models.TMACustomPrompt.id.asc())

        for p in query:
            ptype = p.prompt_type or 'standard'
            if p.is_active:
                if ptype == 'trainer':
                    active_trainer_prompt_id = p.id
                    matched_preset = next((pr for pr in presets if pr["name"] == p.name), None)
                    if matched_preset:
                        active_trainer_preset_id = matched_preset["id"]
                elif ptype == 'exam':
                    active_exam_prompt_id = p.id
                    matched_preset = next((pr for pr in presets if pr["name"] == p.name), None)
                    if matched_preset:
                        active_exam_preset_id = matched_preset["id"]
                else:
                    active_standard_prompt_id = p.id
                    matched_preset = next((pr for pr in presets if pr["name"] == p.name), None)
                    if matched_preset:
                        active_standard_preset_id = matched_preset["id"]

            custom_prompts.append({
                "id": p.id,
                "name": p.name,
                "instruction": p.translation_prompt or p.context_prompt or "",
                "translation_prompt": p.translation_prompt or "",
                "context_prompt": p.context_prompt or "",
                "target_language": p.target_language or "de",
                "prompt_type": ptype,
                "is_active": p.is_active
            })
            
        if not active_standard_prompt_id and not active_standard_preset_id:
            active_standard_preset_id = "preset_b1"
        if not active_trainer_prompt_id and not active_trainer_preset_id:
            active_trainer_preset_id = "preset_trainer"
        if not active_exam_prompt_id and not active_exam_preset_id:
            active_exam_preset_id = "preset_exam"
    except Exception as e:
        logger.error(f"Error fetching custom prompts: {e}")

    return {
        "custom_prompts": custom_prompts,
        "active_prompt_id": active_standard_prompt_id,
        "active_preset_id": active_standard_preset_id or "preset_b1",
        "active_standard_prompt_id": active_standard_prompt_id,
        "active_trainer_prompt_id": active_trainer_prompt_id,
        "active_exam_prompt_id": active_exam_prompt_id,
        "active_standard_preset_id": active_standard_preset_id or "preset_b1",
        "active_trainer_preset_id": active_trainer_preset_id or "preset_trainer",
        "active_exam_preset_id": active_exam_preset_id or "preset_exam",
        "target_language": target_lang,
        "native_language": native_lang,
        "language_name": lang_cfg["name"],
        "language_flag": lang_cfg["flag"],
        "system_presets": presets,
        "defaults": {
            "translation": get_prompt_for_phrase("{phrase}", target_lang, native_lang),
            "analysis": get_prompt_for_phrase("phrase", target_lang, native_lang)
        }
    }

@router.post("/user/prompts")
def save_user_prompt(data: dict, user_id: int = Depends(get_user_id)):
    prompt_id = data.get('id')
    name = data.get('name', 'Мой промпт')
    translation_prompt = data.get('translation_prompt', '')
    context_prompt = data.get('context_prompt', '')
    target_language = (data.get('target_language') or 'de').lower().strip()
    prompt_type = data.get('prompt_type', 'standard')
    
    single_prompt = data.get('prompt')
    if single_prompt:
        if not translation_prompt:
            translation_prompt = single_prompt
        if not context_prompt:
            context_prompt = single_prompt
    
    if prompt_id:
        p = models.TMACustomPrompt.get_or_none((models.TMACustomPrompt.id == prompt_id) & (models.TMACustomPrompt.user_id == user_id))
        if not p:
            raise HTTPException(status_code=404, detail="Prompt not found")
        p.name = name
        p.translation_prompt = translation_prompt
        p.context_prompt = context_prompt
        p.target_language = target_language
        p.prompt_type = prompt_type
        p.save()
    else:
        p = models.TMACustomPrompt.create(
            user_id=user_id,
            name=name,
            translation_prompt=translation_prompt,
            context_prompt=context_prompt,
            target_language=target_language,
            prompt_type=prompt_type,
            is_active=False
        )
    return {"status": "ok", "id": p.id}

@router.delete("/user/prompts/{prompt_id}")
def delete_user_prompt(prompt_id: int, user_id: int = Depends(get_user_id)):
    p = models.TMACustomPrompt.get_or_none((models.TMACustomPrompt.id == prompt_id) & (models.TMACustomPrompt.user_id == user_id))
    if not p:
        raise HTTPException(status_code=404, detail="Prompt not found")
    p.delete_instance()
    return {"status": "ok"}

@router.post("/user/prompts/{prompt_id}/activate")
def activate_user_prompt(prompt_id: int, data: dict = None, user_id: int = Depends(get_user_id)):
    target_lang = (data.get('target_language') if data else 'de') or 'de'
    target_type = (data.get('prompt_type') if data else None)
    
    p_target = models.TMACustomPrompt.get_or_none((models.TMACustomPrompt.id == prompt_id) & (models.TMACustomPrompt.user_id == user_id))
    if not p_target:
        raise HTTPException(status_code=404, detail="Prompt not found")
    
    ptype = target_type or p_target.prompt_type or 'standard'

    # Deactivate all for target_language and matching prompt_type
    models.TMACustomPrompt.update(is_active=False).where(
        (models.TMACustomPrompt.user_id == user_id) & 
        (models.TMACustomPrompt.prompt_type == ptype) &
        ((models.TMACustomPrompt.target_language == target_lang) | (models.TMACustomPrompt.target_language.is_null() if target_lang == 'de' else False))
    ).execute()
    
    p_target.is_active = True
    p_target.save()
    return {"status": "ok"}

@router.post("/user/prompts/deactivate")
def deactivate_user_prompts(data: dict = None, user_id: int = Depends(get_user_id)):
    target_lang = (data.get('target_language') if data else 'de') or 'de'
    ptype = (data.get('prompt_type') if data else 'standard') or 'standard'
    models.TMACustomPrompt.update(is_active=False).where(
        (models.TMACustomPrompt.user_id == user_id) & 
        (models.TMACustomPrompt.prompt_type == ptype) &
        ((models.TMACustomPrompt.target_language == target_lang) | (models.TMACustomPrompt.target_language.is_null() if target_lang == 'de' else False))
    ).execute()
    return {"status": "ok"}

@router.post("/user/prompts/preset/{preset_id}/activate")
def activate_system_preset(preset_id: str, data: dict = None, user_id: int = Depends(get_user_id)):
    target_lang = (data.get('target_language') if data else 'de') or 'de'
    native_lang = (data.get('native_language') if data else None)
    if not native_lang:
        native_rec = models.TMASetting.get_or_none(models.TMASetting.key == "NATIVE_LANGUAGE")
        native_lang = native_rec.value if native_rec else "ru"
    from api.services.language_service import get_system_presets
    presets = get_system_presets(target_lang, native_lang)
    preset = next((p for p in presets if p["id"] == preset_id), None)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    
    ptype = preset.get('prompt_type', 'standard')

    models.TMACustomPrompt.update(is_active=False).where(
        (models.TMACustomPrompt.user_id == user_id) &
        (models.TMACustomPrompt.prompt_type == ptype) &
        ((models.TMACustomPrompt.target_language == target_lang) | (models.TMACustomPrompt.target_language.is_null() if target_lang == 'de' else False))
    ).execute()
    
    p = models.TMACustomPrompt.get_or_none(
        (models.TMACustomPrompt.user_id == user_id) & 
        (models.TMACustomPrompt.name == preset["name"]) &
        ((models.TMACustomPrompt.target_language == target_lang) | (models.TMACustomPrompt.target_language.is_null() if target_lang == 'de' else False))
    )
    if not p:
        p = models.TMACustomPrompt.create(
            user_id=user_id,
            name=preset["name"],
            translation_prompt=preset["instruction"],
            context_prompt=preset["instruction"],
            target_language=target_lang,
            prompt_type=ptype,
            is_active=True
        )
    else:
        p.translation_prompt = preset["instruction"]
        p.context_prompt = preset["instruction"]
        p.target_language = target_lang
        p.prompt_type = ptype
        p.is_active = True
        p.save()
    return {"status": "ok", "id": p.id}

# Admin Settings
@router.get("/admin/settings")
def get_admin_settings(user_id: int = Depends(get_user_id)):
    if user_id != ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail="Only admins can access settings")
    settings = {}
    for s in models.TMASetting.select():
        settings[s.key] = s.value
    return settings

@router.post("/admin/settings")
def save_admin_settings(data: dict, user_id: int = Depends(get_user_id)):
    if user_id != ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail="Only admins can access settings")
    import datetime
    try:
        now = datetime.datetime.now()
        for k, v in data.items():
            key = k.upper()
            # Убеждаемся, что передаем начальные значения для новых записей
            s, created = models.TMASetting.get_or_create(
                key=key, 
                defaults={'value': str(v), 'updated_at': now}
            )
            if not created:
                s.value = str(v)
                s.updated_at = now
                s.save()
        try:
            from api import ai_service
            ai_service.invalidate_ai_config_cache()
        except Exception:
            pass
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/settings/models")
async def list_models(provider: str = None, url: str = None, user_id: int = Depends(get_user_id)):
    """Unified endpoint for model listing, used by AITab."""
    if user_id != ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail="Only admins can access settings")
    import ai_service
    if not provider:
        provider_rec = models.TMASetting.get_or_none(models.TMASetting.key == "AI_PROVIDER")
        provider = provider_rec.value if provider_rec and provider_rec.value != "default" else "google"
    
    if not url:
        url_rec = models.TMASetting.get_or_none(models.TMASetting.key == "OLLAMA_URL")
        url = url_rec.value if url_rec else None
    
    return await ai_service.get_provider_models(provider, url)

@router.get("/settings/test-ai")
async def test_ai_connection(user_id: int = Depends(get_user_id)):
    """Unified test endpoint for AITab: tests all configured keys individually."""
    if user_id != ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail="Only admins can access settings")
    import ai_service
    import ai_clients
    provider, ai_key, model = ai_service.get_ai_config()
    
    if not model:
        model = "gemini-2.0-flash" if provider == "google" else "llama3-8b-8192"
        
    client = ai_clients.AIService(provider=provider, api_key=ai_key)
    keys = client.api_keys if client.api_keys else [""]
    
    key_results = []
    
    for idx, k in enumerate(keys):
        single_client = ai_clients.AIService(provider=provider, api_key=k)
        response, success = await single_client.chat_completion(
            system_prompt="Return 'OK'.",
            user_message="Test.",
            model=model
        )
        masked_key = f"...{k[-4:]}" if len(k) > 6 else (k or "Пустой ключ")
        key_results.append({
            "index": idx + 1,
            "key_raw": k,
            "key_masked": masked_key,
            "status": "ok" if success else "error",
            "message": "OK (Соединение установлено)" if success else str(response)
        })

    all_ok = all(r["status"] == "ok" for r in key_results)
    any_ok = any(r["status"] == "ok" for r in key_results)
    overall_status = "ok" if all_ok else ("warning" if any_ok else "error")

    return {
        "status": overall_status,
        "provider": provider,
        "keys": key_results,
        "total_keys": len(keys),
        "error": None if any_ok else (key_results[0]["message"] if key_results else "No keys")
    }

@router.get("/admin/presets")
def get_admin_presets(user_id: int = Depends(get_user_id)):
    if user_id != ADMIN_USER_ID: raise HTTPException(403, 'Admin only')
    return []

# Community Moderation
@router.get("/admin/community/decks")
def get_community_decks(user_id: int = Depends(get_user_id)):
    if user_id != ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail="Only admins can view community decks")
    return services.get_community_content(user_id)

@router.post("/admin/community/promote/{deck_id}")
def promote_deck(deck_id: int, user_id: int = Depends(get_user_id)):
    if user_id != ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail="Only admins can promote decks")
    result = services.promote_to_library(deck_id)
    if result:
        return {"status": "success", "new_library_id": result.id}
    raise HTTPException(status_code=500, detail="Failed to promote deck")

# Admin Prompt Management Endpoints
@router.get("/admin/prompts/all")
def get_all_prompts(user_id: int = Depends(get_user_id)):
    if user_id != ADMIN_USER_ID: raise HTTPException(403, 'Admin only')
    """Returns all custom prompts from all users along with author user details."""
    prompts_data = []
    try:
        user_map = {}
        for u in models.TMAUser.select():
            name = f"{u.first_name or ''} {u.last_name or ''}".strip() or f"User {u.user_id}"
            user_map[u.user_id] = {
                "name": name,
                "username": u.username or "",
                "user_id": u.user_id
            }

        global_setting = models.TMASetting.get_or_none(models.TMASetting.key == "GLOBAL_SYSTEM_PROMPT_ID")
        global_prompt_id = int(global_setting.value) if (global_setting and global_setting.value) else None

        for p in models.TMACustomPrompt.select().order_by(models.TMACustomPrompt.id.desc()):
            user_info = user_map.get(p.user_id, {
                "name": f"User {p.user_id}",
                "username": "",
                "user_id": p.user_id
            })
            prompts_data.append({
                "id": p.id,
                "user_id": p.user_id,
                "author_name": user_info["name"],
                "author_username": user_info["username"],
                "name": p.name,
                "translation_prompt": p.translation_prompt or "",
                "context_prompt": p.context_prompt or "",
                "is_active": p.is_active,
                "is_global_default": p.id == global_prompt_id,
                "created_at": str(p.created_at) if hasattr(p, 'created_at') else ""
            })
    except Exception as e:
        logger.error(f"Error fetching all prompts: {e}")
        
    return {
        "prompts": prompts_data,
        "global_prompt_id": global_prompt_id
    }

@router.post("/admin/prompts/set-global/{prompt_id}")
def set_global_default_prompt(prompt_id: int, user_id: int = Depends(get_user_id)):
    if user_id != ADMIN_USER_ID: raise HTTPException(403, 'Admin only')
    """Sets a specific prompt ID as the global default system prompt for all users."""
    import datetime
    try:
        if prompt_id != 0:
            prompt = models.TMACustomPrompt.get_or_none(models.TMACustomPrompt.id == prompt_id)
            if not prompt:
                raise HTTPException(status_code=404, detail="Prompt not found")
        
        now = datetime.datetime.now()
        s, _ = models.TMASetting.get_or_create(key="GLOBAL_SYSTEM_PROMPT_ID", defaults={'value': str(prompt_id), 'updated_at': now})
        s.value = str(prompt_id)
        s.updated_at = now
        s.save()
        
        return {"status": "ok", "global_prompt_id": prompt_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting global default prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

