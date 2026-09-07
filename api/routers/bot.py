import os
import logging
import datetime
import hashlib
import hmac
import urllib.parse
from fastapi import APIRouter, Request, Header, Depends, HTTPException
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application

from api.dependencies.auth import get_user_id
from api import services

router = APIRouter()
logger = logging.getLogger(__name__)

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
# Извлекаем имя канала (убираем @ если есть для ссылки)
RAW_CHANNEL = os.getenv("REQUIRED_CHANNEL", "LerneDeutsch287").replace("@", "")
CHANNEL_ID = f"@{RAW_CHANNEL}"
# Всегда используем продакшн URL для ссылок в боте, локальная переменная TMA_LINK для разработки
TMA_URL = os.getenv("TMA_PUBLIC_URL", "https://tma-amber.vercel.app").rstrip("/")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

def make_browser_url(base_url: str, user, extra_param: str = None) -> str:
    """Open the app without treating URL data as an identity claim."""
    params = {}
    if extra_param:
        params["tgWebAppStartParam"] = extra_param
    return f"{base_url}/?{urllib.parse.urlencode(params)}" if params else base_url

# Инициализация приложения PTB (без запуска polling)
ptb_app = Application.builder().token(TOKEN).build() if TOKEN else None

async def check_user_sub(context, user_id: int):
    """Фоновая проверка подписки через бота."""
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Ошибка при проверке подписки: {e}")
        return False

async def save_tma_user(user):
    """Update a profile only after its Telegram identity was authenticated."""
    try:
        from api.models import TMAAuthIdentity, TMAUser
        identity = TMAAuthIdentity.get_or_none(
            (TMAAuthIdentity.provider == 'telegram') & (TMAAuthIdentity.subject == str(user.id)))
        if identity is None:
            return None
        tma_user = TMAUser.get_or_none(TMAUser.user_id == identity.account_id)
        if tma_user is None:
            logger.error("Authenticated Telegram identity has no user row: %s", user.id)
            return None
        tma_user.first_name = user.first_name
        tma_user.last_name = user.last_name
        tma_user.username = user.username
        tma_user.updated_at = datetime.datetime.now()
        tma_user.is_guest = False
        tma_user.save()
        logger.info(f"User profile synced via bot: {user.id} ({user.first_name})")
        return tma_user
    except Exception as e:
        logger.error(f"Error saving user in bot: {e}")
        return None

# --- Handlers ---

import html

async def safe_send_reply(update: Update, text_html: str, reply_markup=None):
    """Отправляет сообщение пользователю с HTML-разметкой, с автоматическим фоллбэком при ошибке."""
    message = update.effective_message
    if not message:
        return
    try:
        await message.reply_text(text_html, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to reply with HTML parse_mode: {e}. Retrying plain text.")
        plain_text = text_html.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", "")
        try:
            await message.reply_text(plain_text, reply_markup=reply_markup)
        except Exception as e2:
            logger.error(f"Failed to send plain text reply: {e2}")

# --- Handlers ---

async def start_handler(update: Update, context):
    try:
        user = update.effective_user
        if not user:
            return
            
        first_name = html.escape(user.first_name or "Пользователь")
        
        # Проверяем наличие аргументов в команде /start (например, /start link_12345)
        args = context.args

        if args and args[0].startswith("auth_"):
            try:
                from api.auth.providers import PROOF_TTL, VerifiedIdentity
                from api.auth.service import confirm_challenge
                from api.models import auth_utcnow
                state = args[0].removeprefix("auth_")
                now = auth_utcnow()
                proof = VerifiedIdentity(
                    'telegram', str(user.id), now, now + PROOF_TTL,
                    hashlib.sha256(f'telegram-bot-start:{state}:{user.id}'.encode()).hexdigest())
                confirm_challenge(state, proof)
                await safe_send_reply(update,
                    "✅ <b>Telegram подтверждён.</b> Вернитесь в Lerne: приложение завершит вход автоматически.")
            except Exception as exc:
                logger.info("Rejected Telegram auth challenge: %s", exc)
                await safe_send_reply(update,
                    "⚠️ Ссылка входа недействительна или истекла. Вернитесь в Lerne и начните вход ещё раз.")
            return
        
        if args and args[0].startswith("link_"):
            # Legacy guest-id links cannot prove ownership and are retired.
            await safe_send_reply(update,
                "🔐 Старая ссылка входа больше не действует. Откройте Lerne и выберите Telegram или Google.")
            return
            try:
                guest_id = int(args[0].replace("link_", ""))
                from api.models import TMALinkedSession
                session, _ = TMALinkedSession.get_or_create(guest_id=guest_id)
                session.telegram_id = user.id
                session.is_confirmed = True
                session.save()
                
                from api import services
                services.merge_guest_data(guest_id, user.id)
                
                logger.info(f"Auth Session Linked: guest={guest_id} -> user={user.id} ({user.first_name})")
                
                text = (
                    f"✅ <b>Вход в аккаунт подтверждён!</b>\n\n"
                    f"Привет, {first_name}! Мы связали ваш Telegram-аккаунт.\n\n"
                    "📱 <b>Если вы открывали ссылку из приложения (APK):</b>\n"
                    "Просто переключитесь обратно в приложение Lerne — вход выполнится автоматически! 🚀\n\n"
                    "🌍 <b>Если вы в браузере:</b>\n"
                    "Нажмите кнопку ниже:"
                )
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌍 Открыть в браузере", url=make_browser_url(TMA_URL, user))],
                    [InlineKeyboardButton("🔑 Код для ручного входа", callback_data="get_login_code")]
                ])
                await safe_send_reply(update, text, reply_markup=keyboard)
                return

            except Exception as e:
                logger.error(f"Error linking session: {e}", exc_info=True)
                
            # Fallback if error parsing guest_id
            text = (
                f"🔗 <b>Вход в аккаунт подтверждён!</b>\n\n"
                f"Привет, {first_name}! Мы нашли ваш Telegram-профиль.\n\n"
                "📱 Если вы в приложении Lerne — просто вернитесь в него или получите код ниже 👇\n"
                "🌍 Если вы в браузере — нажмите кнопку ниже:"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌍 Открыть в браузере", url=make_browser_url(TMA_URL, user))],
                [InlineKeyboardButton("🔑 Получить код для входа", callback_data="get_login_code")]
            ])
            await safe_send_reply(update, text, reply_markup=keyboard)
            return

        await save_tma_user(user)

        if args and (args[0].startswith("c_") or args[0].startswith("d_") or args[0].startswith("f_") or args[0].startswith("collab_")):
            share_id = args[0]
            clean_id = share_id.replace("collab_", "").strip()
            item_desc = "материалы"
            try:
                from api.models import TMA_Deck, TMAFolder, TMA_Card
                from api.services.reminder_service import plural_cards

                if clean_id.startswith("d_"):
                    # 1. Поиск по share_id (например, d_a1b2c3d4e5f6)
                    d = TMA_Deck.get_or_none((TMA_Deck.share_id == clean_id) & (TMA_Deck.is_deleted == False))
                    # 2. Если не найдено, проверка числового id (d_36)
                    if not d:
                        num_part = clean_id.replace("d_", "")
                        if num_part.isdigit():
                            d = TMA_Deck.get_or_none((TMA_Deck.id == int(num_part)) & (TMA_Deck.is_deleted == False))
                    
                    if d:
                        total_c = TMA_Card.select().where((TMA_Card.deck_id == d.id) & (TMA_Card.is_deleted == False)).count()
                        c_info = f" ({plural_cards(total_c)})" if total_c > 0 else ""
                        item_desc = f"колоду «<b>{html.escape(d.name)}</b>»{c_info}"
                    else:
                        item_desc = "колоду"

                elif clean_id.startswith("f_"):
                    f = TMAFolder.get_or_none((TMAFolder.share_id == clean_id) & (TMAFolder.is_deleted == False))
                    if not f:
                        num_part = clean_id.replace("f_", "")
                        if num_part.isdigit():
                            f = TMAFolder.get_or_none((TMAFolder.id == int(num_part)) & (TMAFolder.is_deleted == False))
                    
                    if f:
                        item_desc = f"папку «<b>{html.escape(f.name)}</b>»"
                    else:
                        item_desc = "папку с колодами"

                elif clean_id.startswith("c_"):
                    c = TMA_Card.get_or_none((TMA_Card.share_id == clean_id) & (TMA_Card.is_deleted == False))
                    if not c:
                        num_part = clean_id.replace("c_", "")
                        if num_part.isdigit():
                            c = TMA_Card.get_or_none((TMA_Card.id == int(num_part)) & (TMA_Card.is_deleted == False))
                    
                    if c:
                        card_preview = c.front_text[:30] + ("..." if len(c.front_text) > 30 else "")
                        item_desc = f"карточку «<b>{html.escape(card_preview)}</b>»"
                    else:
                        item_desc = "карточку"
            except Exception as e:
                logger.error(f"Error resolving share details for {share_id}: {e}", exc_info=True)

            text = (
                f"💌 <b>Здравствуйте, {first_name}!</b>\n\n"
                f"Вам отправили {item_desc}.\n\n"
                "Нажмите кнопку ниже, чтобы открыть её и начать учить! 👇"
            )
            browser_url = make_browser_url(TMA_URL, user, extra_param=share_id)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Открыть колоду", url=browser_url)],
                [InlineKeyboardButton("📢 Наш Telegram-канал", url=f"https://t.me/{RAW_CHANNEL}")]
            ])
            await safe_send_reply(update, text, reply_markup=keyboard)
            return

        text = (
            f"🌟 <b>Здравствуйте, {first_name}! Добро пожаловать в Lerne!</b>\n\n"
            "Это пространство для эффективного изучения немецкого языка с помощью ИИ. 🇩🇪\n\n"
            "Нажмите кнопку ниже, чтобы начать обучение в браузере или получить код для входа: 👇"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Начать учить в браузере", url=make_browser_url(TMA_URL, user))],
            [InlineKeyboardButton("🔑 Код для входа в приложение", callback_data="get_login_code")],
            [InlineKeyboardButton("📢 Наш Telegram-канал", url=f"https://t.me/{RAW_CHANNEL}")]
        ])
        
        await safe_send_reply(update, text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error handling /start command: {e}", exc_info=True)

def generate_user_login_code(user_id: int) -> str:
    """Генерирует 6-значный одноразовый код для входа на любом устройстве."""
    import random
    from api.models import TMAAuthCode
    TMAAuthCode.update(is_used=True).where(
        (TMAAuthCode.user_id == user_id) & (TMAAuthCode.is_used == False)
    ).execute()
    code = f"{random.randint(100000, 999999)}"
    for _ in range(10):
        if not TMAAuthCode.select().where((TMAAuthCode.code == code) & (TMAAuthCode.is_used == False)).exists():
            break
        code = f"{random.randint(100000, 999999)}"
    TMAAuthCode.create(
        code=code,
        user_id=user_id,
        created_at=datetime.datetime.now(),
        is_used=False
    )
    return code

async def code_handler(update: Update, context):
    """Команда /code для быстрого получения кода авторизации."""
    await safe_send_reply(update,
        "🔐 Коды входа больше не используются. Откройте Lerne и выберите «Войти через Telegram».")
    return
    try:
        user = update.effective_user
        if not user:
            return
        await save_tma_user(user)
        code = generate_user_login_code(user.id)
        formatted_code = f"{code[:3]} {code[3:]}"
        text = (
            f"🔑 <b>Ваш код для входа в Lerne:</b>\n\n"
            f"<code>{formatted_code}</code> <i>(нажмите, чтобы скопировать)</i>\n\n"
            f"⏳ Код действует <b>15 минут</b>.\n"
            f"Введите эти 6 цифр в приложении на телефоне (Android APK), компьютере или в браузере для мгновенного входа в свой аккаунт!"
        )
        await safe_send_reply(update, text)
    except Exception as e:
        logger.error(f"Error in code_handler: {e}", exc_info=True)

async def callback_handler(update: Update, context):
    try:
        query = update.callback_query
        user = update.effective_user
        if query:
            await query.answer()
        
        if user:
            await save_tma_user(user)
        
        if query and query.data == "get_login_code":
            await safe_send_reply(update,
                "🔐 Вход по коду отключён. Выберите Telegram или Google в приложении.")
            return
            code = generate_user_login_code(user.id)
            formatted_code = f"{code[:3]} {code[3:]}"
            text = (
                f"🔑 <b>Ваш код для входа в Lerne:</b>\n\n"
                f"<code>{formatted_code}</code> <i>(нажмите, чтобы скопировать)</i>\n\n"
                f"⏳ Код действует <b>15 минут</b>.\n"
                f"Введите его в приложении на телефоне или в браузере для мгновенного входа!"
            )
            await safe_send_reply(update, text)
            return

        if query and query.data == "check_and_open":
            try:
                await query.edit_message_text(
                    "✅ <b>Добро пожаловать!</b>\n\nТебе доступен полный функционал приложения. Удачи в обучении! 🚀",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚀 Начать учить в браузере", url=TMA_URL)]
                    ]),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error editing message in callback: {e}")
    except Exception as e:
        logger.error(f"Error in callback_handler: {e}", exc_info=True)

# Регистрируем хендлеры
if ptb_app:
    from telegram.ext import CommandHandler, CallbackQueryHandler
    ptb_app.add_handler(CommandHandler("start", start_handler))
    ptb_app.add_handler(CommandHandler("code", code_handler))
    ptb_app.add_handler(CallbackQueryHandler(callback_handler))

# --- Webhook Endpoint ---

@router.post("/bot_webhook")
async def bot_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    # Если секрет задан в настройках окружения — строго требуем и проверяем заголовок от Telegram.
    # Если переменная ещё не настроена — пропускаем запросы, чтобы бот не падал с 403.
    if WEBHOOK_SECRET:
        if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
                WEBHOOK_SECRET, x_telegram_bot_api_secret_token):
            raise HTTPException(status_code=403, detail="invalid_webhook_secret")
    if not ptb_app:
        return {"status": "bot_token_missing"}

    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        
        # В Vercel (serverless) мы должны инициализировать и запустить приложение для обработки одного обновления
        async with ptb_app:
            await ptb_app.process_update(update)
            
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

@router.get("/bot_setup")
async def bot_setup(request: Request):
    """Вспомогательный эндпоинт для установки вебхука и инициализации таблиц."""
    if not TOKEN:
        return {"error": "BOT_TOKEN missing"}
    setup_token = os.getenv("BOT_SETUP_TOKEN", "")
    public_api_base = os.getenv("PUBLIC_API_BASE_URL", "").rstrip("/")
    supplied = request.headers.get("x-internal-setup-token", "")
    if (not setup_token or not WEBHOOK_SECRET or not public_api_base.startswith("https://")
            or not supplied or not hmac.compare_digest(setup_token, supplied)):
        raise HTTPException(status_code=403, detail="bot_setup_not_authorized")
    webhook_url = f"{public_api_base}/api/bot_webhook"
    async with ptb_app:
        success = await ptb_app.bot.set_webhook(url=webhook_url, secret_token=WEBHOOK_SECRET)
    return {"webhook_url": webhook_url, "success": success,
            "db_status": "managed_by_explicit_migrations"}
    
    # Инициализация таблиц
    try:
        from api.models import TMALinkedSession, TMAUser, TMA_Deck, TMA_Card, TMAProgress
        TMALinkedSession.create_table(safe=True)
        TMAUser.create_table(safe=True)
        # На всякий случай проверяем основные таблицы
        TMA_Deck.create_table(safe=True)
        TMA_Card.create_table(safe=True)
        TMAProgress.create_table(safe=True)
        db_status = "Tables initialized"
    except Exception as e:
        db_status = f"DB Error: {e}"
    
    host = request.headers.get("host")
    # Vercel всегда использует https в продакшене
    protocol = "https" if "vercel" in host or "render" in host else "http"
    webhook_url = f"{protocol}://{host}/api/bot_webhook"
    
    async with ptb_app:
        success = await ptb_app.bot.set_webhook(url=webhook_url)
    
    return {
        "webhook_url": webhook_url,
        "success": success,
        "db_status": db_status
    }

@router.post("/bot/test-reminder")
async def test_bot_reminder(user_id: int = Depends(get_user_id)):
    """Отправляет тестовое напоминание текущему пользователю."""
    if not ptb_app:
        raise HTTPException(status_code=503, detail="Бот не настроен (BOT_TOKEN отсутствует)")
    
    async with ptb_app:
        result = await services.send_reminder_to_user(ptb_app, user_id, force=True)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "Не удалось отправить сообщение в Telegram"))
    return result

@router.get("/bot/cron-reminders")
@router.post("/bot/cron-reminders")
async def trigger_cron_reminders(
    x_internal_cron_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    """Эндпоинт для запуска крона рассылки напоминаний."""
    cron_token = os.getenv("REMINDER_CRON_TOKEN") or os.getenv("CRON_SECRET", "")
    # Vercel Cron sends the configured secret as Authorization: Bearer <secret>.
    # Keep the custom header for existing external schedulers.
    bearer_token = authorization[7:] if isinstance(authorization, str) and authorization.startswith("Bearer ") else None
    supplied_token = x_internal_cron_token or bearer_token
    if not cron_token or not supplied_token or not hmac.compare_digest(cron_token, supplied_token):
        raise HTTPException(status_code=403, detail="cron_not_authorized")
    if not ptb_app:
        return {"status": "skipped", "message": "Bot not configured"}
    async with ptb_app:
        return await services.check_and_send_all_reminders(ptb_app)

