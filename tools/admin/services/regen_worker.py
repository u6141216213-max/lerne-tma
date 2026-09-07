"""Background worker functions for AI and audio regeneration tasks."""
import asyncio
import datetime
import json
import logging
import time
from typing import Optional

from api import models, ai_service
from tools.admin.services.card_helpers import (
    card_has_valid_audio,
    card_is_fully_completed,
    save_audio_to_db_or_cloud,
)
from tools.admin.services.deck_helpers import get_deck_and_cards, sync_card_audio_to_matching_decks, sync_card_updates_to_matching_decks
from tools.admin.schemas import (
    BatchRegenerateDeckRequest,
    BatchRegenerateAudioRequest,
    BulkCreateCardsRequest,
    RegenerateDeckRequest,
    RegenerateAudioRequest,
)
from tools.admin.services import task_manager

logger = logging.getLogger(__name__)

# Shared in-memory task state registry (also updated by task_manager)
regen_tasks: dict = {}


def _create_full_db_backup():
    """Creates a full JSON snapshot of all DB tables. Returns backup metadata dict."""
    import os
    from tools.admin.services.backup_service import get_effective_backup_dir

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
        "cards": cards,
    }
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(dump_data, f, ensure_ascii=False, indent=2)

    return {"filename": fname, "filepath": fpath, "folder": target_dir}


async def run_ai_regeneration(deck_id: str, options: RegenerateDeckRequest):
    global regen_tasks
    task_info = regen_tasks.get(deck_id)
    if not task_info:
        return

    deck, cards, is_lib = get_deck_and_cards(deck_id)
    if not deck:
        task_info["status"] = "failed"
        task_info["logs"].append("❌ Ошибка: Колода не найдена")
        task_manager.save_task_checkpoint(task_info)
        return

    excluded_pos = set()
    if options.exclude_range_str:
        parts = options.exclude_range_str.replace(" ", "").split(",")
        for p in parts:
            if not p:
                continue
            if "-" in p:
                sub = p.split("-")
                if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                    for i in range(int(sub[0]), int(sub[1]) + 1):
                        excluded_pos.add(i)
            elif p.isdigit():
                excluded_pos.add(int(p))

    ex_ids = set(options.exclude_card_ids or [])
    if task_info.get("committed_dry_run_card_ids"):
        ex_ids.update(task_info["committed_dry_run_card_ids"])

    filtered_cards = []
    skipped_count = 0
    for idx, c in enumerate(cards, 1):
        if c.id in ex_ids or idx in excluded_pos:
            skipped_count += 1
            continue
        filtered_cards.append(c)
    cards = filtered_cards

    if skipped_count > 0:
        task_info["logs"].append(f"🛡️ Пропущено карточек по исключению: {skipped_count}")

    if options.skip_completed:
        before_len = len(cards)
        cards = [c for c in cards if not card_is_fully_completed(c)]
        skipped_comp = before_len - len(cards)
        if skipped_comp > 0:
            task_info["logs"].append(f"🛡️ Пропущено уже полностью заполненных карточек: {skipped_comp}")

    if options.only_empty:
        cards = [c for c in cards if not c.back_text or not c.context]
    if options.only_no_context:
        cards = [c for c in cards if not c.context or not str(c.context).strip() or (('?' in (c.front_text or '') or '\n*' in (c.front_text or '')) and not ('🎯' in str(c.context) and '📖' in str(c.context)))]

    if options.dry_run:
        cards = cards[:3]
    elif options.limit and options.limit > 0:
        cards = cards[:options.limit]

    start_c_idx = (options.start_card_idx - 1) if (options.start_card_idx and options.start_card_idx > 1) else 0
    if start_c_idx > 0 and start_c_idx < len(cards):
        cards = cards[start_c_idx:]

    task_info["total"] = len(cards)
    task_info["status"] = "running"
    task_info["control"] = "run"
    task_info["dry_run_results"] = []
    task_info["is_dry_run"] = options.dry_run
    task_info["sync_copies"] = options.sync_copies
    task_info["voice"] = options.voice or "de-DE-KatjaNeural"
    task_info["no_audio"] = options.no_audio
    task_info["options"] = options.dict()
    task_info["task_type"] = "single_ai"
    task_info["deck_id"] = str(deck_id)
    task_info["current_deck_id"] = str(deck_id)
    task_info["current_deck_name"] = deck.name

    mode_str = "🧪 ТЕСТ (3 карточки / Dry-Run)" if options.dry_run else f"🚀 ПОЛНАЯ ПЕРЕГЕНЕРАЦИЯ ({len(cards)} карточек)"
    task_info["logs"].append(f"Запуск: {mode_str} (Голос: {options.voice or 'Default'})...")

    target_lang = getattr(deck, 'target_language', 'de') or "de"
    native_lang = options.native_lang or "uk"
    user_id_val = getattr(deck, 'user_id', 0)

    for idx, card in enumerate(cards, start_c_idx + 1):
        while task_info.get("control") == "pause":
            task_info["status"] = "paused"
            task_manager.update_task_progress(deck_id, status="paused")
            await asyncio.sleep(0.5)

        if task_info.get("control") == "stop":
            task_info["status"] = "stopped"
            task_info["logs"].append("🛑 Процесс остановлен пользователем.")
            task_manager.update_task_progress(deck_id, status="stopped", log_msg="🛑 Остановлено")
            return

        task_info["status"] = "running"
        front = (card.front_text or "").strip()
        if not front:
            continue

        task_info["processed"] = idx
        task_info["current_card"] = front[:30]
        task_manager.update_task_progress(
            deck_id, status="running",
            current_deck_idx=1, current_deck_id=str(deck_id), current_deck_name=deck.name,
            current_card_idx=idx, current_card_id=card.id, current_card_text=front,
            processed_cards=idx, processed_decks=1
        )

        try:
            res = await ai_service.generate_card_fields(
                user_id=user_id_val, phrase=front,
                target_language=target_lang, native_language=native_lang, action_type="full_card"
            )
            if isinstance(res, dict) and "error" in res:
                task_info["logs"].append(f"[{idx}/{len(cards)}] ❌ {front[:20]}: {res['error']}")
                continue

            new_front = res.get("front") or front
            new_back = res.get("back") or ""
            new_context = res.get("context") or ""
            new_level = res.get("level")

            if options.dry_run:
                test_audio_path = None
                if not options.no_audio and options.voice and str(options.voice).lower() not in ("none", "off", "no", "disabled", ""):
                    try:
                        from api.utils.audio import generate_audio
                        res_audio = await generate_audio(new_front, voice=options.voice or "de-DE-KatjaNeural", rate=options.rate or "+0%")
                        if isinstance(res_audio, tuple):
                            res_audio = res_audio[0]
                        if res_audio:
                            test_audio_path = save_audio_to_db_or_cloud(res_audio)
                    except Exception as err:
                        task_info["logs"].append(f"  ⚠️ [TTS] Ошибка озвучки теста: {err}")
                task_info["dry_run_results"].append({
                    "card_id": card.id, "front": new_front, "back": new_back,
                    "context": new_context, "level": new_level,
                    "audio_path": test_audio_path, "voice": options.voice or "de-DE-KatjaNeural", "rate": options.rate or "+0%"
                })
            else:
                card.front_text = new_front
                card.back_text = new_back
                card.context = new_context
                if new_level:
                    curr_tags = card.tags or ""
                    cleaned = ",".join([t for t in curr_tags.split(",") if t and t.upper() not in {"A1", "A2", "B1", "B2", "C1", "C2"}])
                    card.tags = f"{cleaned},{new_level}".strip(",") if cleaned else new_level
                card.updated_at = datetime.datetime.now()
                card.save()

                if not options.no_audio:
                    try:
                        from api.utils.audio import generate_audio
                        res_audio = await generate_audio(new_front, voice=options.voice or "de-DE-KatjaNeural", rate=options.rate or "+0%")
                        if isinstance(res_audio, tuple):
                            res_audio = res_audio[0]
                        if res_audio:
                            saved_audio = save_audio_to_db_or_cloud(res_audio)
                            if saved_audio:
                                card.audio_path = saved_audio
                                card.save()
                    except Exception as err:
                        task_info["logs"].append(f"  ⚠️ [TTS] Ошибка озвучки: {err}")

                if options.sync_copies:
                    d_count, c_count = sync_card_updates_to_matching_decks(
                        deck, front, new_front, new_back, new_context, new_level, card.audio_path
                    )
                    if c_count > 0:
                        task_info["logs"].append(f"  ↪ 🔄 Синхронизировано с {d_count} другими колодами ({c_count} карточек)")

            task_info["logs"].append(f"[{idx}/{len(cards)}] ✅ {new_front[:20]} -> {new_back[:20]}")

        except Exception as e:
            task_info["logs"].append(f"[{idx}/{len(cards)}] ❌ Исключение: {str(e)}")

        if options.delay > 0:
            await asyncio.sleep(options.delay)

    task_info["status"] = "completed"
    task_info["logs"].append("🎉 Перегенерация успешно завершена!")
    task_manager.update_task_progress(deck_id, status="completed", log_msg="🎉 Завершено")


async def run_audio_regeneration(deck_id: str, options: RegenerateAudioRequest):
    global regen_tasks
    task_info = regen_tasks.get(deck_id)
    if not task_info:
        return

    deck, cards, is_lib = get_deck_and_cards(deck_id)
    if not deck:
        task_info["status"] = "failed"
        task_info["logs"].append("❌ Ошибка: Колода не найдена")
        task_manager.save_task_checkpoint(task_info)
        return

    excluded_pos = set()
    if options.exclude_range_str:
        parts = options.exclude_range_str.replace(" ", "").split(",")
        for p in parts:
            if not p:
                continue
            if "-" in p:
                sub = p.split("-")
                if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                    for i in range(int(sub[0]), int(sub[1]) + 1):
                        excluded_pos.add(i)
            elif p.isdigit():
                excluded_pos.add(int(p))

    ex_ids = set(options.exclude_card_ids or [])
    if task_info.get("committed_dry_run_card_ids"):
        ex_ids.update(task_info["committed_dry_run_card_ids"])

    filtered_cards = []
    skipped_count = 0
    for idx, c in enumerate(cards, 1):
        if c.id in ex_ids or idx in excluded_pos:
            skipped_count += 1
            continue
        if options.skip_completed and card_is_fully_completed(c):
            skipped_count += 1
            continue
        if options.only_missing_audio and card_has_valid_audio(c):
            skipped_count += 1
            continue
        filtered_cards.append(c)
    cards = filtered_cards

    if skipped_count > 0:
        task_info["logs"].append(f"🛡️ Пропущено карточек (исключения / уже с озвучкой): {skipped_count}")

    if options.limit and options.limit > 0:
        cards = cards[:options.limit]

    start_c_idx = (options.start_card_idx - 1) if (options.start_card_idx and options.start_card_idx > 1) else 0
    if start_c_idx > 0 and start_c_idx < len(cards):
        cards = cards[start_c_idx:]

    task_info["total"] = len(cards)
    task_info["status"] = "running"
    task_info["control"] = "run"
    task_info["voice"] = options.voice or "de-DE-KatjaNeural"
    task_info["rate"] = options.rate or "+0%"
    task_info["sync_copies"] = options.sync_copies
    task_info["options"] = options.dict()
    task_info["task_type"] = "single_audio"
    task_info["deck_id"] = str(deck_id)
    task_info["current_deck_id"] = str(deck_id)
    task_info["current_deck_name"] = deck.name

    voice_str = options.voice or "de-DE-KatjaNeural"
    rate_str = options.rate or "+0%"
    task_info["logs"].append(f"🎙️ Запуск генерации озвучки: {len(cards)} карточек (Голос: {voice_str}, Скорость: {rate_str})...")

    from api.utils.audio import generate_audio

    for idx, card in enumerate(cards, start_c_idx + 1):
        while task_info.get("control") == "pause":
            task_info["status"] = "paused"
            task_manager.update_task_progress(deck_id, status="paused")
            await asyncio.sleep(0.5)

        if task_info.get("control") == "stop":
            task_info["status"] = "stopped"
            task_info["logs"].append("🛑 Озвучивание остановлено пользователем.")
            task_manager.update_task_progress(deck_id, status="stopped", log_msg="🛑 Остановлено")
            return

        task_info["status"] = "running"
        front = (card.front_text or "").strip()
        if not front:
            continue

        task_info["processed"] = idx
        task_info["current_card"] = front[:30]
        task_manager.update_task_progress(
            deck_id, status="running",
            current_deck_idx=1, current_deck_id=str(deck_id), current_deck_name=deck.name,
            current_card_idx=idx, current_card_id=card.id, current_card_text=front,
            processed_cards=idx, processed_decks=1
        )

        try:
            res_audio = await generate_audio(front, voice=voice_str, rate=rate_str)
            if isinstance(res_audio, tuple):
                res_audio = res_audio[0]

            if res_audio:
                saved_audio = save_audio_to_db_or_cloud(res_audio)
                if saved_audio:
                    card.audio_path = saved_audio
                    card.updated_at = datetime.datetime.now()
                    card.save()

                    if options.sync_copies:
                        d_count, c_count = sync_card_audio_to_matching_decks(deck, front, saved_audio)
                        if c_count > 0:
                            task_info["logs"].append(f"  ↪ 🔄 Аудио синхронизировано с {d_count} другими колодами ({c_count} карточек)")

                    task_info["logs"].append(f"[{idx}/{len(cards)}] 🎙️ {front[:25]} -> {saved_audio}")
                else:
                    task_info["logs"].append(f"[{idx}/{len(cards)}] ❌ Не удалось сохранить аудио: {front[:20]}")
            else:
                task_info["logs"].append(f"[{idx}/{len(cards)}] ❌ Пустой результат TTS для: {front[:20]}")

        except Exception as e:
            task_info["logs"].append(f"[{idx}/{len(cards)}] ❌ Ошибка озвучки: {str(e)}")

        if options.delay > 0:
            await asyncio.sleep(options.delay)

    task_info["status"] = "completed"
    task_info["logs"].append("🎉 Генерация озвучки успешно завершена!")
    task_manager.update_task_progress(deck_id, status="completed", log_msg="🎉 Завершено")


async def run_batch_ai_regeneration(task_id: str, options: BatchRegenerateDeckRequest):
    global regen_tasks
    task_info = regen_tasks.get(task_id)
    if not task_info:
        return

    decks_to_process = []
    total_cards_count = 0
    excluded_cards_set = set(options.exclude_card_ids or [])
    if task_info.get("committed_dry_run_card_ids"):
        excluded_cards_set.update(task_info["committed_dry_run_card_ids"])

    for deck_id in options.deck_ids:
        deck, cards, is_lib = get_deck_and_cards(deck_id)
        if not deck:
            task_info["logs"].append(f"⚠️ Колода #{deck_id} не найдена, пропускаем")
            continue

        if excluded_cards_set:
            cards = [c for c in cards if c.id not in excluded_cards_set]
        if getattr(options, 'target_card_ids', None):
            target_set = set(options.target_card_ids)
            cards = [c for c in cards if c.id in target_set]
        if options.skip_completed:
            before_len = len(cards)
            cards = [c for c in cards if not card_is_fully_completed(c)]
            skipped_comp = before_len - len(cards)
            if skipped_comp > 0:
                task_info["logs"].append(f"  🛡️ Пропущено полностью заполненных карточек: {skipped_comp} в «{deck.name}»")
        if options.only_empty:
            cards = [c for c in cards if not c.back_text or not c.context]
        if options.only_no_context:
            cards = [c for c in cards if not c.context or not str(c.context).strip() or (('?' in (c.front_text or '') or '\n*' in (c.front_text or '')) and not ('🎯' in str(c.context) and '📖' in str(c.context)))]

        if options.dry_run:
            cards = cards[:2]
        elif options.cards_per_deck_limit and options.cards_per_deck_limit > 0:
            cards = cards[:options.cards_per_deck_limit]

        if cards:
            decks_to_process.append((deck_id, deck, cards, is_lib))
            total_cards_count += len(cards)

    start_d_idx = (options.start_deck_idx - 1) if (options.start_deck_idx and options.start_deck_idx > 1) else 0
    if start_d_idx > 0 and start_d_idx < len(decks_to_process):
        decks_to_process = decks_to_process[start_d_idx:]

    task_info["total_decks"] = len(decks_to_process)
    task_info["total_cards"] = total_cards_count
    task_info["status"] = "running"
    task_info["control"] = "run"
    task_info["dry_run_results"] = []
    task_info["is_dry_run"] = options.dry_run
    task_info["sync_copies"] = options.sync_copies
    task_info["voice"] = options.voice or "de-DE-KatjaNeural"
    task_info["no_audio"] = options.no_audio
    task_info["options"] = options.dict()
    task_info["task_type"] = "batch_ai"
    task_info["failed_card_ids"] = []
    task_info["failed_cards"] = []

    mode_str = "🧪 ТЕСТ (Dry-Run: по 2 карточки на колоду)" if options.dry_run else f"🚀 МАССОВАЯ ГЕНЕРАЦИЯ ({len(decks_to_process)} колод, {total_cards_count} карточек)"
    task_info["logs"].append(f"Запуск: {mode_str} (Голос: {options.voice or 'Default'})...")

    if not options.dry_run:
        try:
            _create_full_db_backup()
            task_info["logs"].append("🛡️ Автобэкап базы данных успешно создан перед стартом пакета.")
        except Exception as e:
            task_info["logs"].append(f"⚠️ Не удалось создать автобэкап: {e}")

    task_manager.update_task_progress(task_id, status="running", log_msg="🚀 Запуск пакета")
    global_card_idx = 0

    for d_idx, (deck_id, deck, cards, is_lib) in enumerate(decks_to_process, 1):
        task_info["processed_decks"] = d_idx - 1
        task_info["current_deck_id"] = str(deck_id)
        task_info["current_deck_name"] = deck.name
        task_info["logs"].append(f"📦 [{d_idx}/{len(decks_to_process)}] Колода: «{deck.name}» (#{deck_id}) — {len(cards)} карточек...")
        task_manager.update_task_progress(
            task_id, status="running",
            current_deck_idx=d_idx, current_deck_id=str(deck_id), current_deck_name=deck.name,
            processed_decks=d_idx - 1
        )

        user_id_val = getattr(deck, 'user_id', 0) if hasattr(deck, 'user_id') and isinstance(getattr(deck, 'user_id'), int) else 0
        target_lang = getattr(deck, 'target_language', 'de') or options.target_lang or "de"
        native_lang = options.native_lang or "uk"
        start_c_idx = (options.start_card_idx - 1) if (d_idx == 1 and options.start_card_idx and options.start_card_idx > 1) else 0
        cards_to_process = cards[start_c_idx:] if start_c_idx > 0 else cards

        for c_idx, card in enumerate(cards_to_process, start_c_idx + 1):
            while task_info.get("control") == "pause":
                task_info["status"] = "paused"
                task_manager.update_task_progress(task_id, status="paused")
                await asyncio.sleep(0.5)

            if task_info.get("control") == "stop":
                task_info["status"] = "stopped"
                task_info["logs"].append("🛑 Пакетная генерация остановлена пользователем.")
                task_manager.update_task_progress(task_id, status="stopped", log_msg="🛑 Остановлено")
                return

            task_info["status"] = "running"
            front = (card.front_text or "").strip()
            if not front:
                global_card_idx += 1
                task_info["processed_cards"] = global_card_idx
                continue

            global_card_idx += 1
            task_info["processed_cards"] = global_card_idx
            task_info["current_card"] = f"⏳ [{c_idx}/{len(cards)}] {front[:30]}..."
            task_manager.update_task_progress(
                task_id, status="running",
                current_deck_idx=d_idx, current_deck_id=str(deck_id), current_deck_name=deck.name,
                current_card_idx=c_idx, current_card_id=card.id, current_card_text=front,
                processed_cards=global_card_idx, processed_decks=d_idx - 1
            )

            try:
                res = await asyncio.wait_for(
                    ai_service.generate_card_fields(
                        user_id=user_id_val, phrase=front,
                        target_language=target_lang, native_language=native_lang, action_type="full_card"
                    ),
                    timeout=90.0
                )
            except asyncio.TimeoutError:
                task_info["current_card"] = f"[{c_idx}/{len(cards)}] {front[:30]}"
                task_info["logs"].append(f"  [{c_idx}/{len(cards)}] ⏰ Таймаут 90с: {front[:25]} — в список ошибок")
                task_info.setdefault("failed_card_ids", []).append(card.id)
                task_info.setdefault("failed_cards", []).append({"card_id": card.id, "deck_id": str(deck_id), "deck_name": deck.name, "front": front[:40], "error": "Timeout 90s"})
                if options.delay > 0:
                    await asyncio.sleep(min(options.delay, 0.5))
                continue
            except Exception as e:
                task_info["current_card"] = f"[{c_idx}/{len(cards)}] {front[:30]}"
                task_info["logs"].append(f"  [{c_idx}/{len(cards)}] ❌ Исключение AI: {str(e)[:60]}")
                task_info.setdefault("failed_card_ids", []).append(card.id)
                task_info.setdefault("failed_cards", []).append({"card_id": card.id, "deck_id": str(deck_id), "deck_name": deck.name, "front": front[:40], "error": str(e)[:100]})
                if options.delay > 0:
                    await asyncio.sleep(min(options.delay, 0.5))
                continue

            task_info["current_card"] = f"[{c_idx}/{len(cards)}] {front[:30]}"

            if isinstance(res, dict) and "error" in res:
                task_info["logs"].append(f"  [{c_idx}/{len(cards)}] ❌ {front[:20]}: {res['error'][:60]}")
                task_info.setdefault("failed_card_ids", []).append(card.id)
                task_info.setdefault("failed_cards", []).append({"card_id": card.id, "deck_id": str(deck_id), "deck_name": deck.name, "front": front[:40], "error": str(res['error'])[:100]})
                if options.delay > 0:
                    await asyncio.sleep(min(options.delay, 0.5))
                continue

            try:
                new_front = res.get("front") or front
                new_back = res.get("back") or ""
                new_context = res.get("context") or ""
                new_level = res.get("level")

                if options.dry_run:
                    test_audio_path = None
                    if not options.no_audio and options.voice and str(options.voice).lower() not in ("none", "off", "no", "disabled", ""):
                        try:
                            from api.utils.audio import generate_audio
                            res_audio = await generate_audio(new_front, voice=options.voice, rate=options.rate or "+0%")
                            if isinstance(res_audio, tuple):
                                res_audio = res_audio[0]
                            if res_audio:
                                test_audio_path = save_audio_to_db_or_cloud(res_audio)
                        except Exception as err:
                            task_info["logs"].append(f"    ⚠️ [TTS] Ошибка озвучки теста: {err}")
                    task_info["dry_run_results"].append({
                        "deck_id": str(deck_id), "deck_name": deck.name, "card_id": card.id,
                        "front": new_front, "back": new_back, "context": new_context,
                        "level": new_level, "audio_path": test_audio_path,
                        "voice": options.voice, "rate": options.rate
                    })
                else:
                    card.front_text = new_front
                    card.back_text = new_back
                    card.context = new_context
                    if new_level:
                        curr_tags = card.tags or ""
                        cleaned = ",".join([t for t in curr_tags.split(",") if t and t.upper() not in {"A1", "A2", "B1", "B2", "C1", "C2"}])
                        card.tags = f"{cleaned},{new_level}".strip(",") if cleaned else new_level
                    card.updated_at = datetime.datetime.now()
                    card.save()

                    if not options.no_audio and options.voice and str(options.voice).lower() not in ("none", "off", "no", "disabled", ""):
                        try:
                            from api.utils.audio import generate_audio
                            res_audio = await generate_audio(new_front, voice=options.voice, rate=options.rate or "+0%")
                            if isinstance(res_audio, tuple):
                                res_audio = res_audio[0]
                            if res_audio:
                                saved_audio = save_audio_to_db_or_cloud(res_audio)
                                if saved_audio:
                                    card.audio_path = saved_audio
                                    card.save()
                        except Exception as err:
                            task_info["logs"].append(f"    ⚠️ [TTS] Ошибка озвучки: {err}")

                    if options.sync_copies:
                        d_count, c_count = sync_card_updates_to_matching_decks(
                            deck, front, new_front, new_back, new_context, new_level, card.audio_path
                        )
                        if c_count > 0:
                            task_info["logs"].append(f"    ↪ 🔄 Синхронизировано с {d_count} другими колодами ({c_count} карт.)")

                task_info["logs"].append(f"  [{c_idx}/{len(cards)}] ✅ {new_front[:20]} -> {new_back[:20]}")

            except Exception as e:
                task_info["logs"].append(f"  [{c_idx}/{len(cards)}] ❌ Исключение: {str(e)}")

            if options.delay > 0:
                await asyncio.sleep(options.delay)

        task_info["processed_decks"] = d_idx

    task_info["status"] = "completed"
    task_info["logs"].append(f"🎉 Пакетная перегенерация {len(decks_to_process)} колод ({global_card_idx} карточек) успешно завершена!")
    task_manager.update_task_progress(task_id, status="completed", log_msg="🎉 Завершено")


async def run_batch_audio_regeneration(task_id: str, options: BatchRegenerateAudioRequest):
    global regen_tasks
    task_info = regen_tasks.get(task_id)
    if not task_info:
        return

    from api.utils.audio import generate_audio

    decks_to_process = []
    total_cards_count = 0
    excluded_cards_set = set(options.exclude_card_ids or [])

    for deck_id in options.deck_ids:
        deck, cards, is_lib = get_deck_and_cards(deck_id)
        if not deck:
            task_info["logs"].append(f"⚠️ Колода #{deck_id} не найдена, пропускаем")
            continue

        if excluded_cards_set:
            cards = [c for c in cards if c.id not in excluded_cards_set]
        if options.skip_completed:
            before_len = len(cards)
            cards = [c for c in cards if not card_is_fully_completed(c)]
            skipped_comp = before_len - len(cards)
            if skipped_comp > 0:
                task_info["logs"].append(f"  🛡️ Пропущено полностью заполненных карточек: {skipped_comp} в «{deck.name}»")
        if options.only_missing_audio:
            cards = [c for c in cards if not card_has_valid_audio(c)]
        if options.cards_per_deck_limit and options.cards_per_deck_limit > 0:
            cards = cards[:options.cards_per_deck_limit]

        if cards:
            decks_to_process.append((deck_id, deck, cards, is_lib))
            total_cards_count += len(cards)

    start_d_idx = (options.start_deck_idx - 1) if (options.start_deck_idx and options.start_deck_idx > 1) else 0
    if start_d_idx > 0 and start_d_idx < len(decks_to_process):
        decks_to_process = decks_to_process[start_d_idx:]

    task_info["total_decks"] = len(decks_to_process)
    task_info["total_cards"] = total_cards_count
    task_info["status"] = "running"
    task_info["control"] = "run"
    task_info["voice"] = options.voice or "de-DE-KatjaNeural"
    task_info["rate"] = options.rate or "+0%"
    task_info["sync_copies"] = options.sync_copies
    task_info["options"] = options.dict()
    task_info["task_type"] = "batch_audio"

    voice_str = options.voice or "de-DE-KatjaNeural"
    rate_str = options.rate or "+0%"
    task_info["logs"].append(f"🎙️ Запуск пакетного озвучивания: {len(decks_to_process)} колод, {total_cards_count} карточек (Голос: {voice_str}, Скорость: {rate_str})...")

    global_card_idx = 0

    for d_idx, (deck_id, deck, cards, is_lib) in enumerate(decks_to_process, start_d_idx + 1):
        task_info["current_deck_id"] = str(deck_id)
        task_info["current_deck_name"] = deck.name
        task_info["processed_decks"] = d_idx - 1
        task_info["logs"].append(f"📦 [{d_idx}/{len(decks_to_process)}] Озвучка колоды: «{deck.name}» (#{deck_id}) — {len(cards)} карточек...")

        start_c_idx = (options.start_card_idx - 1) if (d_idx == start_d_idx + 1 and options.start_card_idx and options.start_card_idx > 1) else 0
        cards_slice = cards[start_c_idx:]

        for c_idx, card in enumerate(cards_slice, start_c_idx + 1):
            while task_info.get("control") == "pause":
                task_info["status"] = "paused"
                task_manager.update_task_progress(task_id, status="paused")
                await asyncio.sleep(0.5)

            if task_info.get("control") == "stop":
                task_info["status"] = "stopped"
                task_info["logs"].append("🛑 Пакетное озвучивание остановлено пользователем.")
                task_manager.update_task_progress(task_id, status="stopped", log_msg="🛑 Остановлено")
                return

            task_info["status"] = "running"
            front = (card.front_text or "").strip()
            if not front:
                global_card_idx += 1
                task_info["processed_cards"] = global_card_idx
                continue

            global_card_idx += 1
            task_info["processed_cards"] = global_card_idx
            task_info["current_card"] = f"⏳ [{c_idx}/{len(cards)}] {front[:30]}..."
            task_manager.update_task_progress(
                task_id, status="running",
                current_deck_idx=d_idx, current_deck_id=str(deck_id), current_deck_name=deck.name,
                current_card_idx=c_idx, current_card_id=card.id, current_card_text=front,
                processed_cards=global_card_idx, processed_decks=d_idx - 1
            )

            try:
                res_audio = await asyncio.wait_for(
                    generate_audio(front, voice=voice_str, rate=rate_str),
                    timeout=60.0
                )
                if isinstance(res_audio, tuple):
                    res_audio = res_audio[0]

                task_info["current_card"] = f"[{c_idx}/{len(cards)}] {front[:30]}"

                if res_audio:
                    saved_audio = save_audio_to_db_or_cloud(res_audio)
                    if saved_audio:
                        card.audio_path = saved_audio
                        card.updated_at = datetime.datetime.now()
                        card.save()

                        if options.sync_copies:
                            d_count, c_count = sync_card_audio_to_matching_decks(deck, front, saved_audio)
                            if c_count > 0:
                                task_info["logs"].append(f"    ↪ 🔄 Аудио синхронизировано с {d_count} другими колодами ({c_count} карт.)")

                        task_info["logs"].append(f"  [{c_idx}/{len(cards)}] 🎙️ {front[:25]} -> {saved_audio}")
                    else:
                        task_info["logs"].append(f"  [{c_idx}/{len(cards)}] ❌ Ошибка сохранения аудио: {front[:20]}")
                else:
                    task_info["logs"].append(f"  [{c_idx}/{len(cards)}] ❌ Пустой результат TTS: {front[:20]}")

            except asyncio.TimeoutError:
                task_info["current_card"] = f"[{c_idx}/{len(cards)}] {front[:30]}"
                task_info["logs"].append(f"  [{c_idx}/{len(cards)}] ⏰ Таймаут TTS 60с: {front[:25]} — пропускаем")
            except Exception as e:
                task_info["current_card"] = f"[{c_idx}/{len(cards)}] {front[:30]}"
                task_info["logs"].append(f"  [{c_idx}/{len(cards)}] ❌ Ошибка TTS: {str(e)[:60]}")

            if options.delay > 0:
                await asyncio.sleep(options.delay)

        task_info["processed_decks"] = d_idx

    task_info["status"] = "completed"
    task_info["logs"].append(f"🎉 Пакетная озвучка {len(decks_to_process)} колод ({global_card_idx} карточек) успешно завершена!")
    task_manager.update_task_progress(task_id, status="completed", log_msg="🎉 Завершено")


async def run_bulk_card_creation(task_id: str, req: BulkCreateCardsRequest):
    global regen_tasks
    task_info = regen_tasks.get(task_id)
    if not task_info:
        return

    deck = None
    is_lib = False
    if req.deck_id:
        deck, _, is_lib = get_deck_and_cards(req.deck_id)
        if not deck:
            task_info["status"] = "failed"
            task_info["logs"].append(f"❌ Колода #{req.deck_id} не найдена")
            task_manager.save_task_checkpoint(task_info)
            return
    elif req.new_deck_name:
        deck_name = req.new_deck_name.strip()
        t_lang = req.target_language or "de"
        lvl = req.level or "A1"
        top = req.topic
        u_id = req.user_id or 0

        if req.is_library:
            deck = models.Deck.create(name=deck_name, target_language=t_lang, level=lvl, topic=top, is_deleted=False)
            is_lib = True
            req.deck_id = f"lib_{deck.id}"
            task_info["logs"].append(f"📁 Создана новая Библиотечная колода: «{deck.name}» (ID: lib_{deck.id})")
        else:
            import json as _json
            meta = {"is_default": True} if req.is_default else {}
            deck = models.TMA_Deck.create(
                user_id=u_id, name=deck_name, target_language=t_lang, level=lvl, topic=top,
                metadata=_json.dumps(meta), is_deleted=False
            )
            is_lib = False
            req.deck_id = str(deck.id)
            task_info["logs"].append(f"📁 Создана новая колода пользователя: «{deck.name}» (ID: #{deck.id})")

            if req.is_default:
                all_users = list(models.TMAUser.select())
                for u in all_users:
                    if u.user_id != u_id:
                        models.TMA_Deck.create(
                            user_id=u.user_id, name=deck_name, target_language=t_lang, level=lvl, topic=top,
                            metadata=_json.dumps({"source_deck_id": deck.id, "is_default": True}), is_deleted=False
                        )
    else:
        task_info["status"] = "failed"
        task_info["logs"].append("❌ Не указан ID колоды и не задано имя для новой колоды")
        task_manager.save_task_checkpoint(task_info)
        return

    clean_phrases = [p.strip() for p in req.phrases if p and p.strip()]
    if not clean_phrases:
        task_info["status"] = "completed"
        task_info["logs"].append("⚠️ Список слов пуст. Карточки не созданы.")
        task_manager.save_task_checkpoint(task_info)
        return

    start_idx = (req.start_card_idx - 1) if (req.start_card_idx and req.start_card_idx > 1) else 0
    phrases_to_process = clean_phrases[start_idx:]

    task_info["total_cards"] = len(clean_phrases)
    task_info["total_decks"] = 1
    task_info["current_deck_id"] = str(req.deck_id)
    task_info["current_deck_name"] = deck.name
    task_info["status"] = "running"
    task_info["control"] = "run"
    task_info["voice"] = req.voice or "de-DE-KatjaNeural"
    task_info["rate"] = req.rate or "+0%"
    task_info["options"] = req.dict()
    task_info["task_type"] = "bulk_create"
    task_info["logs"].append(f"🚀 Запуск массового добавления {len(phrases_to_process)} карточек в колоду «{deck.name}»...")

    card_model = models.Card if is_lib else models.TMA_Card
    deck_filter = (models.Card.deck == deck) if is_lib else (models.TMA_Card.deck_id == deck.id)
    last_card = card_model.select().where(deck_filter & (card_model.is_deleted == False)).order_by(card_model.position.desc()).first()
    curr_pos = (last_card.position or 0) if last_card else 0

    target_lang = getattr(deck, 'target_language', 'de') or req.target_language or "de"
    native_lang = req.native_lang or "uk"
    user_id_val = getattr(deck, 'user_id', 0) if hasattr(deck, 'user_id') and isinstance(getattr(deck, 'user_id'), int) else 0
    voice_str = req.voice or "de-DE-KatjaNeural"
    rate_str = req.rate or "+0%"
    global_c_idx = start_idx
    created_count = 0

    from api.utils.audio import generate_audio

    for idx, phrase in enumerate(phrases_to_process, start_idx + 1):
        while task_info.get("control") == "pause":
            task_info["status"] = "paused"
            task_manager.update_task_progress(task_id, status="paused")
            await asyncio.sleep(0.5)

        if task_info.get("control") == "stop":
            task_info["status"] = "stopped"
            task_info["logs"].append("🛑 Массовое добавление карточек остановлено пользователем.")
            task_manager.update_task_progress(task_id, status="stopped", log_msg="🛑 Остановлено")
            return

        task_info["status"] = "running"
        global_c_idx += 1
        task_info["processed_cards"] = global_c_idx
        task_info["current_card"] = f"⏳ [{idx}/{len(clean_phrases)}] {phrase[:30]}..."

        new_front = phrase
        new_back = ""
        new_context = ""
        new_level = req.level or getattr(deck, 'level', None)

        if req.generate_ai:
            try:
                res = await asyncio.wait_for(
                    ai_service.generate_card_fields(
                        user_id=user_id_val, phrase=phrase,
                        target_language=target_lang, native_language=native_lang, action_type="full_card"
                    ),
                    timeout=90.0
                )
                if isinstance(res, dict) and "error" not in res:
                    new_front = res.get("front") or phrase
                    new_back = res.get("back") or ""
                    new_context = res.get("context") or ""
                    if res.get("level"):
                        new_level = res.get("level")
                elif isinstance(res, dict) and "error" in res:
                    task_info["logs"].append(f"  [{idx}/{len(clean_phrases)}] ⚠️ AI ошибка: {res['error'][:60]}")
            except Exception as e:
                task_info["logs"].append(f"  [{idx}/{len(clean_phrases)}] ⚠️ AI исключение: {str(e)[:60]}")

        saved_audio = None
        if req.generate_audio:
            try:
                res_audio = await asyncio.wait_for(
                    generate_audio(new_front, voice=voice_str, rate=rate_str),
                    timeout=60.0
                )
                if isinstance(res_audio, tuple):
                    res_audio = res_audio[0]
                if res_audio:
                    saved_audio = save_audio_to_db_or_cloud(res_audio)
            except Exception as err:
                task_info["logs"].append(f"  [{idx}/{len(clean_phrases)}] ⚠️ TTS ошибка: {str(err)[:60]}")

        curr_pos += 1
        now = datetime.datetime.now()
        if is_lib:
            created_card = models.Card.create(
                deck=deck, front_text=new_front, back_text=new_back, context=new_context,
                tags=new_level, audio_path=saved_audio, position=curr_pos,
                is_deleted=False, created_at=now, updated_at=now
            )
        else:
            created_card = models.TMA_Card.create(
                deck_id=deck.id, user_id=user_id_val, front_text=new_front, back_text=new_back,
                context=new_context, tags=new_level, audio_path=saved_audio, position=curr_pos,
                is_deleted=False, created_at=now, updated_at=now
            )
        created_count += 1

        if req.sync_copies:
            try:
                sync_card_updates_to_matching_decks(deck, phrase, new_front, new_back, new_context, new_level, saved_audio)
            except Exception:
                pass

        task_info["current_card"] = f"[{idx}/{len(clean_phrases)}] {new_front[:30]}"
        task_info["logs"].append(f"  [{idx}/{len(clean_phrases)}] ✅ «{new_front}» -> «{new_back}»")
        task_manager.update_task_progress(
            task_id, status="running",
            current_deck_idx=1, current_deck_id=str(req.deck_id), current_deck_name=deck.name,
            current_card_idx=idx, current_card_id=created_card.id, current_card_text=new_front,
            processed_cards=global_c_idx, processed_decks=1
        )

        if req.delay > 0:
            await asyncio.sleep(req.delay)

    task_info["status"] = "completed"
    task_info["logs"].append(f"🎉 Массовое добавление завершено: успешно создано {created_count} карточек в колоде «{deck.name}»!")
    task_manager.update_task_progress(task_id, status="completed", log_msg=f"🎉 Завершено ({created_count} карточек)")
