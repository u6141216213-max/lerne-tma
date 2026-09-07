import os
import json
import logging
import datetime
import html
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
except ImportError:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    WebAppInfo = None

from peewee import fn, Case, JOIN
from ..models import TMA_Deck, TMA_Card, TMAProgress, TMAUser, TMASetting, TMAAuthIdentity, tma_db

logger = logging.getLogger(__name__)

TMA_URL = "https://tma-amber.vercel.app"

def get_user_reminder_settings(user_id: int) -> dict:
    """Возвращает настройки напоминаний пользователя."""
    default_settings = {
        "enabled": True,
        "times": ["09:00", "19:00"],
        "frequency": "twice_daily",  # 'hourly', 'five_times', 'three_times', 'twice_daily', 'daily', 'custom'
        "hourly_start": "08:00",
        "hourly_end": "22:00",
        "only_due": False,
        "quiet_enabled": False,
        "quiet_start": "23:00",
        "quiet_end": "07:00",
        "timezone_offset": 3  # UTC+3 по умолчанию
    }
    try:
        setting = TMASetting.get_or_none(TMASetting.key == f"REMINDER_SETTINGS_{user_id}")
        if setting and setting.value:
            loaded = json.loads(setting.value)
            default_settings.update(loaded)
    except Exception as e:
        logger.error(f"Error reading reminder settings for user {user_id}: {e}")
    return default_settings

def save_user_reminder_settings(user_id: int, settings: dict) -> bool:
    """Сохраняет настройки напоминаний пользователя."""
    try:
        current = get_user_reminder_settings(user_id)
        current.update(settings)
        setting, created = TMASetting.get_or_create(key=f"REMINDER_SETTINGS_{user_id}")
        setting.value = json.dumps(current)
        setting.updated_at = datetime.datetime.now()
        setting.save()
        logger.info(f"Saved reminder settings for user {user_id}: {current}")
        return True
    except Exception as e:
        logger.error(f"Error saving reminder settings for user {user_id}: {e}")
        return False

def get_user_due_summary(user_id: int) -> dict:
    """
    Рассчитывает количество карточек к повторению по SRS только для активных колод (is_learning == True).
    Разбивает на 3 категории:
    🔴 due: срочные к повторению сегодня (queue == 'review' and next_review <= now)
    🟡 learning: на закреплении в текущем цикле (queue in ['learning', 'relearning'])
    🔵 new: новые карточки, которые ни разу не запускались
    """
    now = datetime.datetime.now()
    summary = {
        "total_due": 0,
        "total_learning": 0,
        "total_new": 0,
        "total_active_decks": 0,
        "deck_details": []
    }

    try:
        decks = list(TMA_Deck.select().where(
            (TMA_Deck.user_id == user_id) & 
            (TMA_Deck.is_deleted == False)
        ))

        active_decks = []
        for d in decks:
            meta = {}
            if d.metadata:
                try:
                    meta = json.loads(d.metadata)
                except Exception:
                    meta = {}
            if meta.get("is_learning", False):
                active_decks.append(d)

        summary["total_active_decks"] = len(active_decks)
        if not active_decks:
            return summary

        active_deck_ids = [d.id for d in active_decks]

        # Подсчет статистики через оптимизированный Case / Join
        tracked_case = Case(None, [(TMAProgress.queue != 'new', 1)], None)
        learning_case = Case(None, [(TMAProgress.queue << ['learning', 'relearning'], 1)], None)
        due_case = Case(None, [((TMAProgress.queue == 'review') & (TMAProgress.next_review <= now), 1)], None)

        counts_query = (TMA_Card
                        .select(
                            TMA_Card.deck_id,
                            fn.COUNT(TMA_Card.id).alias('total'),
                            fn.COUNT(tracked_case).alias('tracked'),
                            fn.COUNT(learning_case).alias('learning'),
                            fn.COUNT(due_case).alias('due')
                        )
                        .join(TMAProgress, JOIN.LEFT_OUTER, on=(
                            (TMAProgress.card_id == TMA_Card.id) & (TMAProgress.user_id == user_id)
                        ))
                        .where(
                            (TMA_Card.deck_id << active_deck_ids) &
                            (TMA_Card.is_deleted == False)
                        )
                        .group_by(TMA_Card.deck_id)
                        .dicts())

        counts_map = {row['deck_id']: row for row in counts_query}

        for d in active_decks:
            c = counts_map.get(d.id, {})
            total = int(c.get('total') or 0)
            tracked = int(c.get('tracked') or 0)
            learning = int(c.get('learning') or 0)
            due = int(c.get('due') or 0)
            new_cards = max(0, total - tracked)

            summary["total_due"] += due
            summary["total_learning"] += learning
            summary["total_new"] += new_cards

            if due > 0 or learning > 0 or new_cards > 0:
                summary["deck_details"].append({
                    "id": d.id,
                    "name": d.name,
                    "due": due,
                    "learning": learning,
                    "new": new_cards,
                    "total": total
                })

        return summary
    except Exception as e:
        logger.error(f"Error calculating due summary for user {user_id}: {e}", exc_info=True)
        return summary


def plural_cards(n: int) -> str:
    """Склонение слова 'карточка' для русского языка."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} карточка"
    elif 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return f"{n} карточки"
    else:
        return f"{n} карточек"


def format_reminder_message(first_name: str, summary: dict, is_test: bool = False) -> str:
    """Формирует понятный текст уведомления для Telegram с ярлыками и 3 цветами."""
    safe_name = html.escape(first_name or "друг")
    total_due = summary.get("total_due", 0)
    total_learning = summary.get("total_learning", 0)
    total_new = summary.get("total_new", 0)
    deck_details = summary.get("deck_details", [])

    prefix = "⚡ <b>Тестовое напоминание Lerne</b>\n\n" if is_test else ""

    text = prefix
    text += f"Привет, {safe_name}! Напоминание по колодам, за которыми вы следите:\n\n"

    if deck_details:
        has_any_work = (total_due + total_learning + total_new) > 0
        for item in deck_details:
            name = html.escape(item.get("name", "Колода"))
            due = item.get("due", 0)
            learning = item.get("learning", 0)
            new_cards = item.get("new", 0)

            text += f"📚 <b>{name}</b>\n"
            if due > 0 or learning > 0 or new_cards > 0:
                text += f"   🔴 {due} повторить | 🟡 {learning} закрепить | 🔵 {new_cards} новых\n\n"
            else:
                text += "   ✅ Все карточки пройдены!\n\n"

        if has_any_work:
            text += (
                f"<b>Итого на сегодня:</b>\n"
                f"🔴 Повторить: <b>{total_due}</b> | 🟡 Закрепить: <b>{total_learning}</b> | 🔵 Новых: <b>{total_new}</b>\n\n"
                f"Нажмите кнопку ниже, чтобы начать! 🚀"
            )
        else:
            text += "Все карточки на сегодня успешно пройдены. Отличный прогресс! 🎉\n\nВозвращайтесь в любое удобное время! 🚀"
    else:
        text += "В ваших изучаемых колодах все карточки на сегодня успешно пройдены. Отличный прогресс! 🎉\n\n"
        text += "Возвращайтесь в любое удобное время! 🚀"

    return text


async def send_reminder_to_user(bot_app, user_id: int, force: bool = False) -> dict:
    """Отправляет персональное напоминание пользователю в Telegram."""
    if not bot_app or not bot_app.bot:
        return {"status": "error", "message": "Bot not initialized"}

    try:
        user = TMAUser.get_or_none(TMAUser.user_id == user_id)
        if not user or user.is_guest:
            return {"status": "skipped", "message": "Guest user"}

        identity = TMAAuthIdentity.get_or_none(
            (TMAAuthIdentity.account == user_id) & (TMAAuthIdentity.provider == 'telegram'))
        if identity is None or not identity.subject.isdigit() or int(identity.subject) <= 0:
            return {"status": "skipped", "message": "Telegram not linked"}

        settings = get_user_reminder_settings(user_id)
        if not settings.get("enabled", True) and not force:
            return {"status": "skipped", "message": "Notifications disabled by user"}

        summary = get_user_due_summary(user_id)
        
        # Если не принудительный тест и нет карточек для повторения и новых карточек — не спамим
        if not force and summary["total_due"] == 0 and summary["total_new"] == 0:
            return {"status": "skipped", "message": "No due or new cards"}

        first_name = user.first_name or user.username or "Пользователь"
        msg_text = format_reminder_message(first_name, summary, is_test=force)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Учить в браузере", url=TMA_URL)]
        ])

        await bot_app.bot.send_message(
            chat_id=int(identity.subject),
            text=msg_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )

        logger.info(f"Sent SRS reminder to user {user_id} (due={summary['total_due']}, new={summary['total_new']})")
        return {"status": "success", "user_id": user_id, "total_due": summary["total_due"]}

    except Exception as e:
        logger.error(f"Failed to send reminder to user {user_id}: {e}")
        return {"status": "error", "message": str(e)}


async def check_and_send_all_reminders(bot_app) -> dict:
    """
    Проверяет всех пользователей и рассылает напоминания согласно их расписанию и часовому поясу.
    """
    if not bot_app or not bot_app.bot:
        return {"status": "error", "message": "Bot not configured"}

    results = {"sent": 0, "skipped": 0, "errors": 0}
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    try:
        users = list(TMAUser.select().where(TMAUser.is_guest == False))
        for u in users:
            try:
                settings = get_user_reminder_settings(u.user_id)
                if not settings.get("enabled", True):
                    results["skipped"] += 1
                    continue

                tz_offset = int(settings.get("timezone_offset", 3))
                user_local_time = now_utc + datetime.timedelta(hours=tz_offset)
                current_hour = user_local_time.hour
                date_hour_key = f"{user_local_time.strftime('%Y-%m-%d')}_{current_hour:02d}"

                # Защита от повторной отправки в тот же самый час
                last_sent_key = f"LAST_REMINDER_SENT_{u.user_id}"
                last_sent_setting = TMASetting.get_or_none(TMASetting.key == last_sent_key)
                if last_sent_setting and last_sent_setting.value == date_hour_key:
                    results["skipped"] += 1
                    continue

                # Проверка тихого режима (если включен)
                if settings.get("quiet_enabled", False):
                    q_start = int(settings.get("quiet_start", "23:00").split(":")[0])
                    q_end = int(settings.get("quiet_end", "07:00").split(":")[0])
                    if q_start > q_end:
                        if current_hour >= q_start or current_hour < q_end:
                            results["skipped"] += 1
                            continue
                    else:
                        if q_start <= current_hour < q_end:
                            results["skipped"] += 1
                            continue

                frequency = settings.get("frequency", "twice_daily")
                should_send = False

                if frequency == "hourly":
                    h_start = int(settings.get("hourly_start", "08:00").split(":")[0])
                    h_end = int(settings.get("hourly_end", "22:00").split(":")[0])
                    if h_start <= current_hour <= h_end:
                        should_send = True
                else:
                    scheduled_times = settings.get("times", ["09:00", "19:00"])
                    for t_str in scheduled_times:
                        try:
                            sh_hour = int(t_str.split(":")[0])
                            if current_hour == sh_hour:
                                should_send = True
                                break
                        except Exception:
                            pass

                if should_send:
                    # Проверка фильтра "только созревшие"
                    if settings.get("only_due", False):
                        due_check = get_user_due_summary(u.user_id)
                        if due_check.get("total_due", 0) == 0:
                            results["skipped"] += 1
                            continue

                    res = await send_reminder_to_user(bot_app, u.user_id, force=False)
                    if res.get("status") == "success":
                        results["sent"] += 1
                        # Фиксируем отметку успешной отправки в этот час
                        s_obj, _ = TMASetting.get_or_create(key=last_sent_key)
                        s_obj.value = date_hour_key
                        s_obj.updated_at = datetime.datetime.now()
                        s_obj.save()
                    else:
                        results["skipped"] += 1
                else:
                    results["skipped"] += 1
            except Exception as user_err:
                logger.error(f"Error processing user {u.user_id} in cron: {user_err}")
                results["errors"] += 1

        return {"status": "ok", "results": results}
    except Exception as e:
        logger.error(f"Error in check_and_send_all_reminders: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
