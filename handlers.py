import contextlib
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo
from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    BufferedInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.exceptions import TelegramBadRequest
import qrcode
from io import BytesIO
import db
import remna as marzban
import platega
from keyboards import (
    main_menu_kb,
    back_to_main_kb,
    buy_menu_kb,
    balance_menu_kb,
    refill_menu_kb,
    stars_refill_kb,
    platega_refill_kb,
    subscriptions_menu_kb,
    admin_menu_kb,
    happ_manual_kb,
    support_kb,
    expired_subscription_kb,
    info_channel_kb,
    kb_button,
)

logger = logging.getLogger("vpn_bot")

LEGAL_PRIVACY_URL = os.getenv(
    "LEGAL_PRIVACY_URL",
    "https://telegra.ph/",
).strip()

LEGAL_TERMS_URL = os.getenv(
    "LEGAL_TERMS_URL",
    "https://telegra.ph/",
).strip()

LEGAL_LINKS_TEXT = (
    '<tg-emoji emoji-id="5393302369024882368">'
    '🔐</tg-emoji> '
    f'<a href="{LEGAL_PRIVACY_URL}">'
    'Политика конфиденциальности</a>\n'
    '<tg-emoji emoji-id="5258477770735885832">'
    '📝</tg-emoji> '
    f'<a href="{LEGAL_TERMS_URL}">'
    'Пользовательское соглашение</a>'
)

router = Router()

# VPN-бот работает только в обычных личных чатах.
router.message.filter(F.chat.type == "private")

# Таймзона для отображения пользователю
MSK_TZ = ZoneInfo("Europe/Moscow")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "")
BRAND_NAME = os.getenv("BRAND_NAME", "VPN").strip() or "VPN"

INFO_CHANNEL_USERNAME = os.getenv("INFO_CHANNEL_USERNAME", "").strip().lstrip("@")
INFO_CHANNEL_URL = os.getenv("INFO_CHANNEL_URL", f"https://t.me/{INFO_CHANNEL_USERNAME}").strip()


# Инструкция, которую показываем пользователю вместе с подпиской/конфигом
# (после покупки и в разделе «Мои подписки»).
HAPP_CONNECT_INSTRUCTION = (
    "📌 Инструкция для подключения:\n"
    "1. Вам выданы ссылка-ключ и QR-код — используйте любой удобный способ.\n"
    "2. Открой приложение Happ или INCY → нажмите ➕ → выберите:\n"
    "• Вставить из буфера — вставьте ссылку\n"
    "• QR-код — отсканируйте QR\n"
    "3. ✅ Готово! В приложении появится подписка с нашими серверами."
)

# В Marzban иногда subscription_url приходит относительным (/sub/...).
# Для пользователя всегда отдаём абсолютную ссылку.
SUBSCRIPTION_BASE_URL = (
    os.getenv("REMNAWAVE_SUBSCRIPTION_BASE_URL", "").rstrip("/")
    or os.getenv("SUBSCRIPTION_BASE_URL", "").rstrip("/")
    or os.getenv("REMNAWAVE_URL", "").rstrip("/")
    or os.getenv("MARZBAN_URL", "").rstrip("/")
)


def _normalize_sub_url(url: Any) -> Any:
    if not url or not isinstance(url, str):
        return url
    u = url.strip()
    if not u:
        return url
    if u.startswith("http://") or u.startswith("https://"):
        return u
    base = SUBSCRIPTION_BASE_URL
    if not base:
        return u
    if u.startswith("/"):
        return f"{base}{u}"
    return f"{base}/{u}"


def _copy_kb_single(text_to_copy: str) -> InlineKeyboardMarkup:
    """Кнопка копирования текста (не открывает браузер)."""
    # CopyTextButton появился в новых Bot API. В aiogram он может лежать в разных местах,
    # поэтому импорт делаем лениво и безопасно.
    try:
        from aiogram.types import CopyTextButton  # type: ignore
    except Exception:
        from aiogram.types.copy_text_button import CopyTextButton  # type: ignore

    btn = InlineKeyboardButton(
        text="📋 Скопировать ссылку",
        copy_text=CopyTextButton(text=text_to_copy[:256]),
    )
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def _copy_kb_many(items: List[str]) -> InlineKeyboardMarkup:
    """Несколько кнопок копирования (по одной на строку)."""
    try:
        from aiogram.types import CopyTextButton  # type: ignore
    except Exception:
        from aiogram.types.copy_text_button import CopyTextButton  # type: ignore

    rows: List[List[InlineKeyboardButton]] = []
    for idx, txt in enumerate(items, start=1):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📋 Скопировать #{idx}",
                    copy_text=CopyTextButton(text=(txt or "")[:256]),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Кэш имени бота (нужно для реферальной ссылки)
_BOT_USERNAME: Optional[str] = None

async def _get_bot_username(bot: Bot) -> str:
    global _BOT_USERNAME
    if _BOT_USERNAME:
        return _BOT_USERNAME
    me = await bot.get_me()
    _BOT_USERNAME = (me.username or "").strip("@")
    return _BOT_USERNAME

# Планы
PLANS: Dict[str, Dict[str, Any]] = {
    "trial_3d": {
        "title": "Пробный 3 дня",
        "days": 3,
        "price": 0,
        "is_trial": True,
    },
    "week_7d": {
        "title": "7 дней",
        "days": 7,
        "price": 25,
        "is_trial": False,
    },
    "month_30d": {
        "title": "30 дней",
        "days": 30,
        "price": 50,
        "is_trial": False,
    },
    "month_60d": {
        "title": "60 дней",
        "days": 60,
        "price": 100,
        "is_trial": False,
    },
}

MAX_ACTIVE_SUBS = 3

# Состояния админа (простая самодельная FSM)
admin_state: Dict[int, str] = {}
admin_temp: Dict[int, Dict[str, Any]] = {}


def is_admin(tg_id: int) -> bool:
    return tg_id == ADMIN_ID


def ts_to_msk_str(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=MSK_TZ)
    return dt.strftime("%d.%m.%Y %H:%M MSK")


async def is_user_subscribed_to_info_channel(bot: Bot, tg_id: int) -> bool:
    """Проверяет подписку на информационный паблик.

    Важно: бот должен быть добавлен в канал/паблик администратором,
    иначе Telegram может не дать проверить подписку через get_chat_member().
    """
    if is_admin(tg_id):
        return True

    if not INFO_CHANNEL_USERNAME:
        # Если канал не задан, не блокируем работу бота.
        return True

    try:
        member = await bot.get_chat_member(f"@{INFO_CHANNEL_USERNAME}", tg_id)
    except Exception as e:
        logger.warning("Не удалось проверить подписку tg_id=%s: %s", tg_id, e)
        return False

    status = getattr(member.status, "value", str(member.status)).lower()
    return status not in {"left", "kicked"}


def subscription_required_text() -> str:
    return (
        f"{ce('bolt', '⚡')} <b>{BRAND_NAME}</b>\n\n"
        "Чтобы пользоваться VPN, подпишись на наш "
        "информационный паблик.\n\n"
        "Там публикуются важные новости, обновления "
        "и полезные материалы.\n\n"
        f"{ce('num1', '1️⃣')} Нажми "
        "<b>«Подписаться на паблик»</b>\n"
        f"{ce('num2', '2️⃣')} Подпишись и вернись в бота\n"
        f"{ce('num3', '3️⃣')} Нажми "
        "<b>«Проверить подписку»</b>\n\n"
        f"{ce('check', '✅')} После проверки откроется "
        "главное меню."
    )

async def show_subscription_required_message(message: Message) -> None:
    await message.answer(
        subscription_required_text(),
        reply_markup=info_channel_kb(INFO_CHANNEL_URL),
        disable_web_page_preview=True,
    )


async def show_subscription_required_callback(callback: CallbackQuery) -> None:
    await callback.answer("Сначала подпишись на информационный паблик.", show_alert=True)
    try:
        await callback.message.edit_text(
            subscription_required_text(),
            reply_markup=info_channel_kb(INFO_CHANNEL_URL),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.exception("Ошибка показа экрана подписки: %s", e)


async def ensure_subscription_callback(callback: CallbackQuery) -> bool:
    if await is_user_subscribed_to_info_channel(callback.bot, callback.from_user.id):
        return True
    await show_subscription_required_callback(callback)
    return False


# Custom emoji для главного приветственного сообщения.
# В тексте сообщений кастомные emoji вставляются через HTML-тег <tg-emoji>.
MAIN_EMOJI = {
    "hello": "5175176802720547704",       # рукопожатие
    "bolt": "5172425562634847208",       # молния /
    "skull": "5357592447557848986",      # череп / DPI
    "check": "5215492745900077682",      # зелёная галочка / 2 устройства
    "warning": "5213070680697876332",    # восклицательный знак
    "blocked": "5215204871422093648",    # перечёркнутый крестик
    "hourglass": "5258113901106580375",  # песочные часы / активные подписки
    "gift": "5215668908278686541",       # подарок / рефералы
    "num1": "5390970128768713615",       # цифра 1
    "num2": "5391310947308553793",       # цифра 2
    "num3": "5388918006279587380",       # цифра 3
    "num4": "5388846628218095428",       # цифра 4
    "num5": "5388890020272687260",       # цифра 5
    "chart": "5267318205000471835",      # график / статистика
    "buy_card": "5366223171454278937",   # карта в руке / выбор тарифа
    "balance_bag": "5215420556089776398",# мешок денег / баланс
    "plus": "5391051561348646680",       # плюс / история операций
    "minus": "5388700195603105888",      # минус / история операций
    "phone": "5433951331083630226",      # смартфон / скачать клиент
    "bulb": "5258466217273871977",       # лампочка / подсказка
    "down": "5231102735817918643",       # палец вниз
}


def ce(key: str, fallback: str) -> str:
    emoji_id = MAIN_EMOJI.get(key)
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def build_main_text(first_name: str, active_cnt: int) -> str:
    user_name = (first_name or "").strip() or "друг"

    text = f"{ce('hello', '🤝')} <b>Добро пожаловать, {user_name}!</b>\n\n"
    text += f"{ce('bolt', '⚡')} <b>{BRAND_NAME}:</b>\n"
    text += "• Анонимность\n"
    text += "• Высокая скорость\n"
    text += "• Безлимитный трафик\n"
    text += f"{ce('warning', '❗')} <b>ВНИМАНИЕ!</b>\n"
    text += "• Запрещены торренты и файлообменные сети\n"
    text += "• Запрещена нагрузка, превышающая разумные пределы\n"
    text += f"{ce('blocked', '❌')} Нарушение этих правил ведёт к автоматической блокировке!\n\n"

    text = text.rstrip()
    text += "\n\n" + LEGAL_LINKS_TEXT

    if active_cnt > 0:
        text += f"\n\n{ce('hourglass', '⏳')} Активных подписок: <b>{active_cnt}</b>"

    return text


async def safe_edit(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,
    disable_web_page_preview: bool = True,
    *,
    animate: bool = True,
):
    """Удаляет старое меню и отправляет новое сообщение.

    Никаких точек/кадров/имитации распада: старое сообщение удаляется,
    затем появляется новый экран. Если Telegram не даст удалить сообщение,
    используем обычный edit_text как запасной вариант.
    """
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    if not callback.message:
        return

    # animate оставлен для совместимости со старыми вызовами safe_edit(..., animate=False).
    # При animate=False делаем обычное редактирование без удаления.
    if not animate:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        except TelegramBadRequest as e:
            error_text = str(e).lower()

            if "message is not modified" in error_text:
                pass

            elif "message to edit not found" in error_text:
                try:
                    await callback.message.answer(
                        text,
                        reply_markup=reply_markup,
                        disable_web_page_preview=disable_web_page_preview,
                    )
                except Exception as send_error:
                    logger.exception(
                        "Не удалось отправить новое сообщение "
                        "после ошибки edit_text: %s",
                        send_error,
                    )

            else:
                logger.exception("Ошибка edit_text: %s", e)
        return

    try:
        # Сначала убираем клавиатуру, чтобы пользователь не нажал кнопку повторно.
        with contextlib.suppress(Exception):
            await callback.message.edit_reply_markup(reply_markup=None)

        await callback.message.delete()

        await callback.message.answer(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        return
    except Exception as e:
        logger.debug("Не удалось удалить старое сообщение, делаю edit_text: %s", e)

    # Запасной вариант: если удалить нельзя, хотя бы не плодим новые меню.
    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
    except TelegramBadRequest as e:
        error_text = str(e).lower()

        if "message is not modified" in error_text:
            pass

        elif "message to edit not found" in error_text:
            try:
                await callback.message.answer(
                    text,
                    reply_markup=reply_markup,
                    disable_web_page_preview=disable_web_page_preview,
                )
            except Exception as send_error:
                logger.exception(
                    "Не удалось отправить новое сообщение "
                    "после ошибки edit_text: %s",
                    send_error,
                )

        else:
            logger.exception("Ошибка edit_text: %s", e)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        f"🆘 <b>Помощь {BRAND_NAME}</b>\n\n"
        "• Подписки — просмотр, продление и обновление "
        "через главное меню.\n"
        "• Подключение — выбери устройство и установи "
        "рекомендуемое приложение.\n"
        "• Если VPN не подключается — обнови подписку "
        "и попробуй другой сервер.\n"
        "• По вопросам оплаты и доступа используй "
        "раздел «Поддержка».\n\n"
        "<b>Документы:</b>\n"
        + LEGAL_LINKS_TEXT,
        disable_web_page_preview=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message):
    db.init_db()

    # /start всегда сбрасывает незавершённый админский режим,
    # чтобы старое состояние не срабатывало на следующие сообщения.
    with contextlib.suppress(Exception):
        admin_state.pop(message.from_user.id, None)
        admin_temp.pop(message.from_user.id, None)

    # Создаём/находим пользователя (и узнаём, новый ли он)
    user_row, created = db.get_or_create_user_ex(message.from_user.id)

    # Админа помечаем флагом в БД
    user_is_admin = is_admin(message.from_user.id)

    if user_is_admin and not user_row["is_admin"]:
        db.set_admin(message.from_user.id, True)

    # Рефералка: /start ref_<telegram_id> или /start <telegram_id>
    ref_tg_id: Optional[int] = None
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) == 2:
        arg = parts[1].strip()
        if arg.isdigit():
            ref_tg_id = int(arg)
        elif arg.lower().startswith("ref_") and arg[4:].isdigit():
            ref_tg_id = int(arg[4:])

    referral_attached = False
    # Привязываем реферера только при первом /start (когда пользователь реально новый)
    if created and ref_tg_id and ref_tg_id != message.from_user.id:
        try:
            attached = db.try_set_referrer(user_row["id"], ref_tg_id)
            if attached:
                referral_attached = True
        except Exception as e:
            logger.exception("Ошибка привязки реферала: %s", e)

    # Если реферер успешно привязан — уведомляем пригласившего
    if referral_attached:
        try:
            await message.bot.send_message(
                ref_tg_id,
                "🎉 По твоей реферальной ссылке зарегистрировался новый пользователь.\n"
                "Он появится в разделе «Рефералы» и сможет принести тебе бонус при первом пополнении.",
            )
        except Exception as e:
            logger.exception("Не удалось отправить уведомление рефереру о регистрации: %s", e)


    if not await is_user_subscribed_to_info_channel(message.bot, message.from_user.id):
        await show_subscription_required_message(message)
        return

    # Приветственный текст
    active_cnt = db.get_active_subscriptions_count(user_row["id"])
    text = build_main_text(message.from_user.first_name or "", active_cnt)

    await message.answer(text, reply_markup=main_menu_kb(is_admin=user_is_admin),
        disable_web_page_preview=True,
)

# ---------- Проверка обязательной подписки ----------

@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery):
    user_row = db.get_or_create_user(callback.from_user.id)
    if not await is_user_subscribed_to_info_channel(callback.bot, callback.from_user.id):
        await show_subscription_required_callback(callback)
        return

    active_cnt = db.get_active_subscriptions_count(user_row["id"])
    text = build_main_text(callback.from_user.first_name or "", active_cnt)
    await safe_edit(
        callback,
        text,
        reply_markup=main_menu_kb(is_admin=is_admin(callback.from_user.id)),
    )


# ---------- Главное меню ----------

@router.callback_query(F.data.startswith("menu:"))
async def main_menu_router(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    action = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    user_row = db.get_or_create_user(user_id)
    user_is_admin = is_admin(user_id)

    if action == "main":
        active_cnt = db.get_active_subscriptions_count(user_row["id"])
        text = build_main_text(callback.from_user.first_name or "", active_cnt)

        await safe_edit(callback, text, reply_markup=main_menu_kb(is_admin=user_is_admin))

    elif action == "balance":
        await show_balance_page(callback, page=1)


    elif action == "refill":
        text = (
            "➕ <b>Пополнение баланса</b>\n\n"
            "Выбери способ оплаты."
        )
        await safe_edit(callback, text, reply_markup=refill_menu_kb())

    elif action == "referrals":
        invited, rewarded, earned = db.get_referral_stats(user_row["id"])

        ref_link = ""
        try:
            bot_username = await _get_bot_username(callback.bot)
            if bot_username:
                ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        except Exception as e:
            logger.exception("Ошибка получения username бота для реф-ссылки: %s", e)

        if ref_link:
            # Формат максимально похож на тот, что у тебя "работал"
            text = (
                f"{ce('gift', '🎁')} Реферальная программа {BRAND_NAME}\n\n"
                "Приглашай друзей по своей ссылке и получай 80% бонус от их первого пополнения баланса.\n\n"
                "Как пользоваться ссылкой:\n"
                f"{ce('num1', '1️⃣')} Скопируй ссылку ниже.\n"
                f"{ce('num2', '2️⃣')} Отправь её другу.\n"
                f"{ce('num3', '3️⃣')} Друг открывает ссылку через любой браузер.\n"
                f"{ce('num4', '4️⃣')} После открытия ссылки в браузере, Telegram предложит открыть бота — друг соглашается и нажимает «Start».\n"
                f"{ce('num5', '5️⃣')} Когда он впервые пополнит баланс, ты автоматически получишь реферальный бонус. {ce('gift', '🎁')}\n\n"
                "🔗 Твоя ссылка:\n"
                f"{ref_link}\n\n"
                f"{ce('chart', '📊')} Статистика:\n"
                f"• Приглашено: {invited}\n"
                f"• Пополнили и дали бонус: {rewarded}\n"
                f"• Заработано Stars: {earned}★"
            )
        else:
            text = (
                f"{ce('gift', '🎁')} Реферальная программа {BRAND_NAME}\n\n"
                "Не удалось собрать реферальную ссылку (нет username у бота).\n"
                "Напиши админу, чтобы он настроил username.\n\n"
                f"{ce('chart', '📊')} Статистика:\n"
                f"• Приглашено: {invited}\n"
                f"• Пополнили и дали бонус: {rewarded}\n"
                f"• Заработано Stars: {earned}★"
            )

        await safe_edit(callback, text, reply_markup=back_to_main_kb())

    elif action == "buy":
        text = (
            f"{ce('buy_card', '🛒')} Выбери тариф:\n\n"
            "Пробный 3 дня — 0★ (один раз на аккаунт)\n"
            "7 дней — 25★\n"
            "30 дней — 50★\n"
            "60 дней — 100★\n\n"
            "Нажимая «Купить», вы принимаете:\n\n"
            + LEGAL_LINKS_TEXT
        )
        await safe_edit(callback, text, reply_markup=buy_menu_kb())

    elif action == "subs":
        text = "📂 Раздел подписок:"
        await safe_edit(callback, text, reply_markup=subscriptions_menu_kb())

    elif action == "manual":
        text = (
            f"{ce('phone', '📲')} Скачай клиент Happ или INCY и добавь туда свою подписку.\n\n"
            f"{ce('num1', '1️⃣')} Выбери платформу ниже и установи приложение.\n"
            f"{ce('num2', '2️⃣')} После покупки подписки, ты получишь QR код и ссылку.\n"
            f"{ce('num3', '3️⃣')} В приложении добавь подписку с помощью QR кода или ссылки.\n\n"
            f"{ce('bulb', '💡')} Если у вас андроид - приложение Happ, для айфонов App StoreРФ - INCY.\n\n"
            f"Клиент под каждую платформу ниже {ce('down', '👇')}"
        )
        await safe_edit(
            callback,
            text,
            reply_markup=happ_manual_kb(),
            disable_web_page_preview=True,
        )

    elif action == "nodes":
        await show_nodes_usage(callback)

    elif action == "support":
        if SUPPORT_USERNAME:
            text = (
                "🆘 Поддержка.\n\n"
                f"Если что-то не работает — пиши в саппорт: {SUPPORT_USERNAME}"
            )
            await safe_edit(callback, text, reply_markup=support_kb(SUPPORT_USERNAME))
        else:
            await safe_edit(
                callback,
                "🆘 Поддержка пока не настроена.",
                reply_markup=back_to_main_kb(),
            )

    elif action == "admin":
        if not user_is_admin:
            await callback.answer("Недостаточно прав.", show_alert=True)
            return
        text = "👮 Админ-панель. Выбери действие:"
        await safe_edit(callback, text, reply_markup=admin_menu_kb())

    else:
        await callback.answer("Неизвестное действие меню.", show_alert=True)


# ---------- Пополнение баланса (Platega + Telegram Stars) ----------


@router.callback_query(F.data == "stars:menu")
async def stars_menu_callback(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return
    await safe_edit(
        callback,
        "⭐ <b>Оплата через Telegram Stars</b>\n\n"
        "Выбери сумму пополнения.",
        reply_markup=stars_refill_kb(),
    )


@router.callback_query(F.data == "platega:menu")
async def platega_menu_callback(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return
    if not platega.is_enabled():
        await callback.answer("Platega пока не настроена.", show_alert=True)
        return
    try:
        options = platega.get_topups()
    except Exception as exc:
        logger.exception("Ошибка PLATEGA_TOPUPS: %s", exc)
        await callback.answer("Суммы пополнения не настроены.", show_alert=True)
        return
    await safe_edit(
        callback,
        "💳 <b>Оплата картой / СБП</b>\n\n"
        "Выбери сумму. После оплаты баланс начислится автоматически.",
        reply_markup=platega_refill_kb(options),
    )


@router.callback_query(F.data.startswith("platega:pay:"))
async def platega_pay_callback(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return
    try:
        _, _, rub_s, credit_s = callback.data.split(":", 3)
        rub_amount = int(rub_s)
        credit_amount = int(credit_s)
    except Exception:
        await callback.answer("Некорректная сумма.", show_alert=True)
        return

    allowed = {(o.rub_amount, o.credit_amount) for o in platega.get_topups()}
    if (rub_amount, credit_amount) not in allowed:
        await callback.answer("Эта сумма недоступна.", show_alert=True)
        return

    tg_id = int(callback.from_user.id)
    user_row = db.get_or_create_user(tg_id)
    username = (
        f"@{callback.from_user.username}"
        if callback.from_user.username
        else callback.from_user.full_name
    )
    try:
        response = await platega.create_payment(
            telegram_id=tg_id,
            username=username,
            rub_amount=rub_amount,
            credit_amount=credit_amount,
        )
        transaction_id = str(response.get("transactionId") or "").strip()
        payment_url = str(response.get("url") or response.get("redirect") or "").strip()
        if not transaction_id or not payment_url:
            raise RuntimeError(f"Неполный ответ Platega: {response}")
        payload = f"topup|{tg_id}|{credit_amount}"
        db.create_platega_payment(
            transaction_id=transaction_id,
            user_id=int(user_row["id"]),
            telegram_id=tg_id,
            amount_kopecks=rub_amount * 100,
            credit_amount=credit_amount,
            payment_url=payment_url,
            payload=payload,
        )
    except Exception as exc:
        logger.exception("Не удалось создать платёж Platega: %s", exc)
        await callback.answer("Не удалось создать платёж. Попробуй позже.", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [kb_button("Перейти к оплате", url=payment_url, emoji_key="wallet", style="success")],
            [kb_button("Проверить оплату", callback_data=f"platega:check:{transaction_id}", emoji_key="success")],
            [kb_button("Назад", callback_data="platega:menu", emoji_key="prev")],
        ]
    )
    await callback.message.answer(
        "💳 <b>Счёт создан</b>\n\n"
        f"К оплате: <b>{rub_amount} ₽</b>\n"
        f"На баланс: <b>{credit_amount}★</b>\n\n"
        "Ссылка действует ограниченное время. После оплаты нажми «Проверить оплату», "
        "если уведомление не пришло автоматически.",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("platega:check:"))
async def platega_check_callback(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return
    transaction_id = callback.data.split(":", 2)[2].strip()
    local = db.get_platega_payment(transaction_id)
    if not local or int(local["telegram_id"]) != int(callback.from_user.id):
        await callback.answer("Платёж не найден.", show_alert=True)
        return
    try:
        remote = await platega.get_payment(transaction_id)
        status = str(remote.get("status") or "").upper()
        payment_details = remote.get("paymentDetails") or {}
        amount = payment_details.get("amount") if isinstance(payment_details, dict) else None
        amount_kopecks = (
            platega.rub_to_kopecks(amount)
            if amount is not None
            else int(local["amount_kopecks"])
        )
        result = db.process_platega_status(
            transaction_id=transaction_id,
            status=status,
            callback_amount_kopecks=amount_kopecks,
            currency=(
                str(payment_details.get("currency") or "RUB")
                if isinstance(payment_details, dict)
                else "RUB"
            ),
        )
    except Exception as exc:
        logger.exception("Ошибка проверки Platega %s: %s", transaction_id, exc)
        await callback.answer("Не удалось проверить платёж.", show_alert=True)
        return

    if status == "CONFIRMED" or str(result.get("status")) == "CREDITED":
        await callback.answer(
            f"Оплата подтверждена. Баланс: {int(result['new_balance'])}★",
            show_alert=True,
        )
    elif status == "CANCELED":
        await callback.answer("Платёж отменён.", show_alert=True)
    elif status == "CHARGEBACKED":
        await callback.answer("По платежу оформлен возврат.", show_alert=True)
    else:
        await callback.answer("Платёж пока ожидает подтверждения.", show_alert=True)


TOPUP_AMOUNTS = {25, 50, 100, 200}


@router.callback_query(F.data.startswith("topup:"))
async def topup_callback(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    try:
        amount = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректная сумма.", show_alert=True)
        return

    if amount not in TOPUP_AMOUNTS:
        await callback.answer("Эта сумма недоступна.", show_alert=True)
        return

    tg_id = callback.from_user.id
    payload = f"topup|{tg_id}|{amount}|{int(datetime.now(timezone.utc).timestamp())}"

    try:
        await callback.message.answer_invoice(
            title="Пополнение баланса",
            description=f"Пополнение внутреннего баланса на {amount}★",
            payload=payload,
            provider_token="",  # для Stars токен не нужен
            currency="XTR",
            prices=[LabeledPrice(label=f"{amount}★", amount=amount)],
        )
        await callback.answer()
    except Exception as e:
        logger.exception("Не удалось выставить инвойс Stars: %s", e)
        await callback.answer("Не удалось создать счёт. Попробуй позже.", show_alert=True)


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Telegram требует ответить на pre-checkout
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    sp = message.successful_payment

    if not sp:
        return

    # Этот обработчик принимает только Telegram Stars.
    if (sp.currency or "").upper() != "XTR":
        return

    paid_amount = int(sp.total_amount or 0)

    if paid_amount <= 0:
        logger.error(
            "Получен успешный платёж с некорректной "
            "суммой: tg_id=%s amount=%s",
            message.from_user.id,
            paid_amount,
        )
        return

    tg_id = int(message.from_user.id)

    payload = str(
        sp.invoice_payload or ""
    ).strip()

    charge_id = str(
        getattr(
            sp,
            "telegram_payment_charge_id",
            "",
        )
        or ""
    ).strip()

    provider_charge_id = str(
        getattr(
            sp,
            "provider_payment_charge_id",
            "",
        )
        or ""
    ).strip()

    # Без уникального Telegram charge ID безопасно
    # определить повторную доставку невозможно.
    if not charge_id:
        logger.error(
            "У успешного платежа отсутствует "
            "telegram_payment_charge_id: "
            "tg_id=%s amount=%s payload=%s",
            tg_id,
            paid_amount,
            payload,
        )

        if ADMIN_ID:
            try:
                await message.bot.send_message(
                    ADMIN_ID,
                    "🚨 Успешный платёж без charge ID.\n"
                    f"TG ID: <code>{tg_id}</code>\n"
                    f"Сумма: <b>{paid_amount}★</b>\n"
                    f"Payload: <code>{payload}</code>\n\n"
                    "Баланс автоматически не начислен.",
                )
            except Exception:
                pass

        await message.answer(
            "⚠️ Платёж прошёл, но отсутствует "
            "идентификатор операции.\n"
            "Баланс автоматически не начислен. "
            "Напиши в поддержку."
        )
        return

    # Payload используем для дополнительной диагностики.
    # Фактическая сумма берётся из successful_payment.
    if payload.startswith("topup|"):
        parts = payload.split("|")

        if len(parts) >= 3:
            try:
                payload_tg_id = int(parts[1])
                payload_amount = int(parts[2])

                if (
                    payload_tg_id != tg_id
                    or payload_amount != paid_amount
                ):
                    logger.warning(
                        "Параметры payload отличаются от "
                        "successful_payment: "
                        "tg_id=%s payload_tg_id=%s "
                        "paid=%s payload_amount=%s",
                        tg_id,
                        payload_tg_id,
                        paid_amount,
                        payload_amount,
                    )

            except (TypeError, ValueError):
                logger.warning(
                    "Некорректный payload Stars: %s",
                    payload,
                )

    user_row = db.get_or_create_user(tg_id)

    try:
        credited_now, new_balance = (
            db.credit_telegram_stars_payment(
                user_id=int(user_row["id"]),
                telegram_id=tg_id,
                amount=paid_amount,
                telegram_payment_charge_id=charge_id,
                provider_payment_charge_id=(
                    provider_charge_id
                ),
                invoice_payload=payload,
            )
        )

    except Exception as exc:
        logger.exception(
            "Не удалось атомарно начислить Stars: "
            "tg_id=%s charge_id=%s",
            tg_id,
            charge_id,
        )

        if ADMIN_ID:
            try:
                await message.bot.send_message(
                    ADMIN_ID,
                    "🚨 Ошибка начисления Telegram Stars.\n"
                    f"TG ID: <code>{tg_id}</code>\n"
                    f"Сумма: <b>{paid_amount}★</b>\n"
                    f"Charge ID: "
                    f"<code>{charge_id}</code>\n"
                    f"Ошибка: "
                    f"<code>{type(exc).__name__}: "
                    f"{str(exc)[:300]}</code>\n\n"
                    "Баланс автоматически не изменён.",
                )
            except Exception:
                pass

        await message.answer(
            "⚠️ Платёж прошёл, но баланс не удалось "
            "начислить автоматически.\n"
            "Напиши в поддержку — платёж сохранён "
            "в Telegram."
        )
        return

    if not credited_now:
        logger.warning(
            "Повторная доставка successful_payment "
            "проигнорирована: tg_id=%s charge_id=%s",
            tg_id,
            charge_id,
        )

        await message.answer(
            "ℹ️ Этот платёж уже был зачислен ранее.\n"
            f"Текущий баланс: <b>{new_balance}★</b>."
        )
        return

    await message.answer(
        "✅ <b>Баланс успешно пополнен</b>\n\n"
        f"Начислено: <b>{paid_amount}★</b>\n"
        f"Текущий баланс: <b>{new_balance}★</b>."
    )



# ---------- Покупка подписки ----------

def _build_username_for_plan(tg_id: int, plan_key: str, sub_index: int) -> str:
    if plan_key == "trial_3d":
        kind = "trial"
    elif plan_key == "week_7d":
        kind = "week"
    elif plan_key == "month_30d":
        kind = "month"
    elif plan_key == "month_60d":
        kind = "2month"
    else:
        kind = plan_key
    return f"tg_{kind}_{tg_id}_{sub_index}"


async def _generate_qr_bytes(url: str) -> BytesIO:
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def _create_subscription_for_user(
    tg_id: int,
    plan_key: str,
) -> Dict[str, Any]:
    """
    Создаёт пользователя в Marzban, записывает подписку в БД и возвращает dict с данными:
    {sub_id, sub_url, end_ts, is_trial, plan_title, price}

    Главное изменение: если Marzban отвечает 409 "User already exists"
    на имя вида tg_week_..._N — мы автоматически пробуем N+1, N+2 и т.д.
    """
    user_row = db.get_or_create_user(tg_id)
    internal_user_id = user_row["id"]

    if plan_key not in PLANS:
        raise ValueError("Unknown plan")

    plan = PLANS[plan_key]

    # Ограничение по количеству активных подписок
    active_cnt = db.get_active_subscriptions_count(internal_user_id)
    if active_cnt >= MAX_ACTIVE_SUBS:
        raise RuntimeError(
            f"У тебя уже {MAX_ACTIVE_SUBS} активных подписок. Сначала отключи одну из них."
        )

    # Ограничение на триал
    if plan["is_trial"] and db.user_has_trial(internal_user_id):
        raise RuntimeError("Пробная подписка уже была активирована ранее.")

    price = plan["price"]

    # Проверяем баланс для платных тарифов
    if price > 0:
        balance = db.get_balance(internal_user_id)
        if balance < price:
            raise RuntimeError(
                f"Недостаточно средств. Нужно {price}★, а у тебя только {balance}★."
            )

    now_utc = int(datetime.now(timezone.utc).timestamp())
    days = plan["days"]
    end_ts = now_utc + days * 24 * 60 * 60

    # Номер подписки по счёту — базовый (по БД)
    total_subs = db.get_total_subscriptions_count(internal_user_id)
    base_index = total_subs + 1

    note = f"tg_id={tg_id}, plan={plan_key}"

    username = None
    created: Dict[str, Any] | None = None

    # Пытаемся подобрать свободное имя: _N, _N+1, ..., _N+4
    for extra in range(0, 5):
        current_index = base_index + extra
        candidate_username = _build_username_for_plan(tg_id, plan_key, current_index)
        try:
            created = await marzban.create_user(
                username=candidate_username,
                expire_ts=end_ts,
                note=note,
                traffic_limit_bytes=(
                    30 * 1024 ** 3
                    if (
                        bool(plan.get("is_trial"))
                        or plan_key.startswith("trial")
                    )
                    else 0
                ),
            )

            # Повторно закрепляем лимит после создания. Это важно,
            # если POST завершился таймаутом, но Remnawave уже успел
            # создать пользователя и бот восстановил существующую запись.
            if (
                bool(plan.get("is_trial"))
                or plan_key.startswith("trial")
            ):
                await marzban.modify_user(
                    candidate_username,
                    traffic_limit_bytes=30 * 1024 ** 3,
                )

            username = candidate_username
            break
        except RuntimeError as e:
            msg = str(e)
            # Если такой пользователь уже есть — пробуем следующий индекс
            if "User already exists" in msg or "409" in msg:
                logger.warning(
                    "Имя пользователя %s уже занято в Marzban, пробую следующий индекс",
                    candidate_username,
                )
                continue
            # Любая другая ошибка — сразу вылетаем с понятным текстом
            logger.exception("Ошибка создания пользователя в Marzban: %s", e)
            raise RuntimeError(
                "Ошибка при создании подписки на сервере. Попробуй позже."
            )
        except Exception as e:
            logger.exception("Неожиданная ошибка создания пользователя в Marzban: %s", e)
            raise RuntimeError(
                "Ошибка при создании подписки на сервере. Попробуй позже."
            )

    if not created or not username:
        # Если все 5 вариантов имён заняты/упали — шлём ошибку пользователю
        raise RuntimeError(
            "Не удалось создать учётку на сервере VPN (не нашли свободное имя). Попробуй позже."
        )

    sub_url = created.get("subscription_url")
    if not sub_url:
        # на всякий случай, если поле переименуют
        raise RuntimeError("Панель не вернула ссылку подписки.")

    # Записываем в БД
    sub_id = db.create_subscription(
        user_id=internal_user_id,
        plan_key=plan_key,
        is_trial=bool(plan["is_trial"]),
        starts_at=now_utc,
        end_at=end_ts,
        marzban_username=username,
        sub_url=sub_url,
    )

    # Списываем Stars только после успешного создания
    if price > 0:
        db.change_balance(
            internal_user_id,
            -price,
            tx_type="buy",
            description=f"Покупка подписки {plan['title']}",
        )

    return {
        "sub_id": sub_id,
        "sub_url": sub_url,
        "end_ts": end_ts,
        "is_trial": bool(plan["is_trial"]),
        "plan_title": plan["title"],
        "price": price,
    }


@router.callback_query(F.data.startswith("buy:"))
async def buy_plan_callback(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    import contextlib

    plan_key = callback.data.split(":", 1)[1]
    tg_id = callback.from_user.id

    if plan_key not in PLANS:
        try:
            await callback.answer("Неизвестный тариф.", show_alert=True)
        except TelegramBadRequest:
            pass
        return

    # 1) Сразу убираем "крутилку" Telegram у кнопки
    with contextlib.suppress(Exception):
        await callback.answer()

    # 2) Убираем клавиатуру с сообщения с тарифами, чтобы не жали повторно,
    # пока идёт запрос в Marzban. Не используем safe_edit здесь, чтобы не создавать
    # лишний экран перед сообщением «Оформляем подписку…».
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)

    # 3) Отправляем отдельное сообщение в чат "оформляем..."
    progress_msg = await callback.message.answer(
        "⏳ Оформляем подписку…\n\nПожалуйста, подожди."
    )

    try:
        result = await _create_subscription_for_user(tg_id, plan_key)
    except RuntimeError as e:
        msg = str(e)

        reply_markup = None

        if "Недостаточно средств" in msg:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        kb_button(
                            "Пополнить баланс",
                            callback_data="menu:refill",
                            emoji_key="wallet",
                            style="primary",
                        )
                    ],
                    [
                        kb_button(
                            "Вернуться к тарифам",
                            callback_data="menu:buy",
                            emoji_key="buy",
                        )
                    ],
                ]
            )

        with contextlib.suppress(Exception):
            await progress_msg.edit_text(
                f"❌ {msg}",
                reply_markup=reply_markup,
            )

        with contextlib.suppress(TelegramBadRequest):
            await callback.answer(
                msg,
                show_alert=False,
            )

        return

    except Exception as e:
        logger.exception(
            "Непредвиденная ошибка оформления подписки: %s",
            e,
        )

        with contextlib.suppress(Exception):
            await progress_msg.edit_text(
                "❌ Не удалось оформить подписку.\n\n"
                "Stars не списаны. Попробуй ещё раз немного позже.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            kb_button(
                                "Вернуться к тарифам",
                                callback_data="menu:buy",
                                emoji_key="buy",
                            )
                        ],
                        [
                            kb_button(
                                "Поддержка",
                                callback_data="menu:support",
                                emoji_key="support",
                            )
                        ],
                    ]
                ),
            )

        return

    end_str = ts_to_msk_str(result["end_ts"])
    plan_title = result["plan_title"]
    price = result["price"]

    if result["is_trial"]:
        cost_text = "бесплатно (триал)"
    else:
        cost_text = f"{price} ★ списано с баланса."

    sub_url = _normalize_sub_url(result.get("sub_url"))

    text = (
        f"✅ Подписка <b>{plan_title}</b> успешно оформлена ({cost_text})\n\n"
        f"Действует до: <b>{end_str}</b>\n\n"
        "Ссылка подписки:\n"
        f"<code>{sub_url}</code>\n\n"
        "\n"
        f"\n{HAPP_CONNECT_INSTRUCTION}\n"
        "Нажми кнопку ниже, чтобы <b>скопировать ссылку</b>\n"
    )

    # 4) Редактируем "оформляем..." в финальный результат (с кнопкой копирования)
    await progress_msg.edit_text(
        text,
        reply_markup=_copy_kb_single(str(sub_url)) if sub_url else None,
        disable_web_page_preview=True,
    )

    # 5) QR-код отдельным сообщением, как у тебя было
    qr_buf = await _generate_qr_bytes(str(sub_url or result.get("sub_url") or ""))
    photo = BufferedInputFile(qr_buf.getvalue(), filename="sub_qr.png")
    await callback.message.answer_photo(
        photo=photo,
        caption=(
            "Вот QR-код для добавления подписки в Happ.\n\n"
            f"{HAPP_CONNECT_INSTRUCTION}"
        ),
    )


def _first_int(data: Any, *keys: str) -> int:
    """Получает первое числовое поле из ответа Remnawave."""
    if not isinstance(data, dict):
        return 0

    for key in keys:
        value = data.get(key)
        if value is None:
            continue

        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue

    return 0


def _extract_user_traffic(data: Any) -> tuple[int, int]:
    """Возвращает использованный трафик и лимит в байтах."""
    used = _first_int(
        data,
        "usedTrafficBytes",
        "trafficUsedBytes",
        "usedBytes",
        "used_traffic_bytes",
        "used_traffic",
    )

    limit = _first_int(
        data,
        "trafficLimitBytes",
        "dataLimitBytes",
        "traffic_limit_bytes",
        "data_limit",
    )

    return used, limit


def _format_traffic(value: int) -> str:
    value = max(0, int(value))

    gib = 1024 ** 3
    mib = 1024 ** 2

    if value >= gib:
        number = value / gib
        if number >= 10:
            return f"{number:.0f} ГБ"
        return f"{number:.1f} ГБ"

    return f"{value / mib:.0f} МБ"


def _remaining_days_text(end_ts: int, now_ts: int) -> str:
    seconds = int(end_ts) - int(now_ts)

    if seconds <= 0:
        return "истекла"

    days = (seconds + 24 * 60 * 60 - 1) // (24 * 60 * 60)

    if days == 1:
        return "1 день"

    if 2 <= days <= 4:
        return f"{days} дня"

    return f"{days} дней"


def _remote_status_text(remote: Any) -> str:
    if not isinstance(remote, dict):
        return "🟢 Активна"

    value = str(remote.get("status") or "").strip().upper()

    statuses = {
        "ACTIVE": "🟢 Активна",
        "ENABLED": "🟢 Активна",
        "DISABLED": "🔴 Отключена",
        "LIMITED": "🟠 Лимит исчерпан",
        "EXPIRED": "🔴 Истекла",
        "ON_HOLD": "🟡 Приостановлена",
    }

    return statuses.get(value, "🟢 Активна")


# ---------- Мои подписки ----------


@router.callback_query(F.data == "subs:list")
async def list_subscriptions(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    tg_id = callback.from_user.id
    user_row = db.get_or_create_user(tg_id)
    subs = db.get_user_subscriptions(
        user_row["id"],
        include_inactive=False,
    )

    if not subs:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    kb_button(
                        "Купить подписку",
                        callback_data="menu:buy",
                        emoji_key="buy",
                        style="primary",
                    )
                ],
                [
                    kb_button(
                        "В главное меню",
                        callback_data="menu:main",
                        emoji_key="home",
                    )
                ],
            ]
        )

        await safe_edit(
            callback,
            "У тебя нет активных подписок.\n\n"
            "Можешь купить новую 👇",
            reply_markup=kb,
        )
        return

    # Получаем данные Remnawave параллельно.
    remote_results = await asyncio.gather(
        *(
            marzban.get_user(str(sub["marzban_username"]))
            for sub in subs
        ),
        return_exceptions=True,
    )

    now_ts = int(datetime.now(timezone.utc).timestamp())

    lines = ["📂 <b>Твои активные подписки</b>\n"]
    kb_rows: List[List[InlineKeyboardButton]] = []

    for idx, sub in enumerate(subs, start=1):
        plan_key = str(sub["plan_key"])
        plan_title = PLANS.get(
            plan_key,
            {},
        ).get("title", plan_key)

        end_ts = int(sub["end_at"])
        end_str = ts_to_msk_str(end_ts)
        is_trial = bool(sub["is_trial"])
        sub_id = int(sub["id"])
        username = str(sub["marzban_username"])

        remote_result = remote_results[idx - 1]
        remote = None

        if isinstance(remote_result, Exception):
            logger.warning(
                "Не удалось получить данные подписки %s: %s",
                username,
                remote_result,
            )
        elif isinstance(remote_result, dict):
            remote = remote_result

        status_text = _remote_status_text(remote)
        remaining_text = _remaining_days_text(
            end_ts,
            now_ts,
        )

        if remote is None:
            traffic_text = "временно недоступен"
        else:
            used, limit = _extract_user_traffic(remote)

            if limit > 0:
                percent = min(
                    999,
                    int(used * 100 / limit),
                )

                traffic_text = (
                    f"{_format_traffic(used)} / "
                    f"{_format_traffic(limit)} "
                    f"({percent}%)"
                )
            else:
                traffic_text = "Безлимит"

        trial_mark = " — 🎁 триал" if is_trial else ""

        lines.extend(
            [
                f"<b>#{idx} · {plan_title}</b>{trial_mark}",
                f"Статус: {status_text}",
                f"Действует до: <b>{end_str}</b>",
                f"Осталось: <b>{remaining_text}</b>",
                f"Трафик: <b>{traffic_text}</b>",
                "",
            ]
        )

        if is_trial:
            extend_text = f"Перейти на платный #{idx}"
            extend_emoji = "buy_new"
        else:
            extend_text = f"Продлить #{idx}"
            extend_emoji = "buy"

        kb_rows.append(
            [
                kb_button(
                    text=extend_text,
                    callback_data=f"sub:extend:{sub_id}",
                    emoji_key=extend_emoji,
                    style="primary",
                ),
                kb_button(
                    text=f"Конфиг #{idx}",
                    callback_data=f"sub:config:{sub_id}",
                    emoji_key="key",
                ),
            ]
        )

    kb_rows.append(
        [
            kb_button(
                text="Обновить данные",
                callback_data="subs:refresh",
                emoji_key="refresh",
                style="primary",
            )
        ]
    )

    kb_rows.append(
        [
            kb_button(
                text="Назад",
                callback_data="menu:subs",
                emoji_key="subs",
            )
        ]
    )

    await safe_edit(
        callback,
        "\n".join(lines).rstrip(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=kb_rows,
        ),
    )


@router.callback_query(F.data == "subs:refresh")
async def refresh_subscriptions(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    await callback.answer(
        "Обновляю данные…",
        show_alert=False,
    )

    try:
        await list_subscriptions(callback)
    except Exception as exc:
        logger.exception(
            "Ошибка ручного обновления подписок: %s",
            exc,
        )

        await callback.message.answer(
            "Не удалось обновить данные подписок. "
            "Попробуй немного позже."
        )


@router.callback_query(F.data == "subs:delete_menu")
async def delete_subscription_menu(callback: CallbackQuery):
    """Показывает список активных подписок, которые пользователь может удалить сам."""
    if not await ensure_subscription_callback(callback):
        return

    tg_id = callback.from_user.id
    user_row = db.get_or_create_user(tg_id)
    subs = db.get_user_subscriptions(user_row["id"], include_inactive=False)

    if not subs:
        text = (
            "🗑 <b>Удаление подписки</b>\n\n"
            "У тебя нет активных подписок для удаления."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [kb_button("Купить новую подписку", callback_data="menu:buy", emoji_key="buy_new", style="primary")],
                [kb_button("В главное меню", callback_data="menu:main", emoji_key="home")],
            ]
        )
        await safe_edit(callback, text, reply_markup=kb)
        return

    lines = [
        "🗑 <b>Удаление подписки</b>\n",
        "Выбери подписку, которую хочешь удалить.",
        "Доступ будет отключён, а действие нельзя будет отменить.\n",
    ]

    kb_rows: List[List[InlineKeyboardButton]] = []
    for idx, sub in enumerate(subs, start=1):
        plan_key = sub["plan_key"]
        plan_title = PLANS.get(plan_key, {}).get("title", plan_key)
        end_str = ts_to_msk_str(int(sub["end_at"]))
        trial_mark = " (триал)" if bool(sub["is_trial"]) else ""
        lines.append(f"{idx}) {plan_title}{trial_mark} — до {end_str}")

        kb_rows.append([
            kb_button(
                text=f"Удалить #{idx}",
                callback_data=f"sub:delete_confirm:{sub['id']}",
                emoji_key="delete",
                style="danger",
            )
        ])

    kb_rows.append([kb_button("Назад", callback_data="menu:subs", emoji_key="subs")])
    kb_rows.append([kb_button("В главное меню", callback_data="menu:main", emoji_key="home")])

    await safe_edit(
        callback,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@router.callback_query(F.data.startswith("sub:delete_confirm:"))
async def confirm_delete_subscription(callback: CallbackQuery):
    """Запрашивает подтверждение перед удалением подписки."""
    if not await ensure_subscription_callback(callback):
        return

    try:
        sub_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректная подписка.", show_alert=True)
        return

    tg_id = callback.from_user.id
    user_row = db.get_or_create_user(tg_id)
    sub = db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != user_row["id"] or not bool(sub["is_active"]):
        await callback.answer("Подписка не найдена или уже удалена.", show_alert=True)
        return

    plan_key = sub["plan_key"]
    plan_title = PLANS.get(plan_key, {}).get("title", plan_key)
    end_str = ts_to_msk_str(int(sub["end_at"]))
    trial_mark = " (триал)" if bool(sub["is_trial"]) else ""

    text = (
        "⚠️ <b>Подтверждение удаления</b>\n\n"
        f"Ты точно хочешь удалить подписку <b>{plan_title}{trial_mark}</b>?\n"
        f"Действует до: <b>{end_str}</b>\n\n"
        "После удаления конфиг перестанет работать. Это действие нельзя отменить."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [kb_button("Да, удалить подписку", callback_data=f"sub:delete_do:{sub_id}", emoji_key="delete", style="danger")],
            [kb_button("Отмена", callback_data="subs:delete_menu", emoji_key="home")],
        ]
    )
    await safe_edit(callback, text, reply_markup=kb)


@router.callback_query(F.data.startswith("sub:delete_do:"))
async def do_delete_subscription(callback: CallbackQuery):
    """Удаляет подписку пользователя в Marzban и помечает её неактивной в БД."""
    if not await ensure_subscription_callback(callback):
        return

    try:
        sub_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректная подписка.", show_alert=True)
        return

    tg_id = callback.from_user.id
    user_row = db.get_or_create_user(tg_id)
    sub = db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != user_row["id"] or not bool(sub["is_active"]):
        await callback.answer("Подписка не найдена или уже удалена.", show_alert=True)
        return

    plan_key = sub["plan_key"]
    plan_title = PLANS.get(plan_key, {}).get("title", plan_key)
    username = sub["marzban_username"]

    try:
        await marzban.delete_user(username)
    except Exception as e:
        logger.exception("Ошибка delete_user при удалении подписки пользователем: %s", e)
        await callback.answer("Не удалось удалить подписку на сервере. Попробуй позже.", show_alert=True)
        return

    db.set_subscription_active(sub_id, False)

    text = (
        "✅ <b>Подписка удалена</b>\n\n"
        f"Подписка <b>{plan_title}</b> отключена и удалена из панели."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [kb_button("Купить новую подписку", callback_data="menu:buy", emoji_key="buy_new", style="primary")],
            [kb_button("Мои подписки", callback_data="subs:list", emoji_key="subs")],
            [kb_button("В главное меню", callback_data="menu:main", emoji_key="home")],
        ]
    )
    await safe_edit(callback, text, reply_markup=kb)


@router.callback_query(F.data == "subs:all_configs")
async def all_configs(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    tg_id = callback.from_user.id
    user_row = db.get_or_create_user(tg_id)
    subs = db.get_user_subscriptions(user_row["id"], include_inactive=False)

    if not subs:
        await callback.answer("У тебя нет активных подписок.", show_alert=True)
        return

    urls: List[str] = []
    lines = ["🔗 Все твои активные конфиги:\n"]

    shown_idx = 0
    for sub in subs:
        # db.get_user_subscriptions может вернуть sqlite3.Row (у него нет .get)
        raw_sub_url = None
        try:
            raw_sub_url = sub["sub_url"]           # sqlite3.Row / dict-like
        except Exception:
            try:
                raw_sub_url = sub.get("sub_url")   # если вдруг dict
            except Exception:
                raw_sub_url = getattr(sub, "sub_url", None)

        sub_url = _normalize_sub_url(raw_sub_url)
        if not sub_url:
            continue

        shown_idx += 1
        sub_url = str(sub_url)
        urls.append(sub_url)

        # В <code> Telegram не делает ссылку кликабельной -> не кидает в браузер
        lines.append(f"{shown_idx}) <code>{sub_url}</code>")

    if not urls:
        await callback.answer("Не нашёл валидных конфигов для твоих подписок.", show_alert=True)
        return

    kb = _copy_kb_many(urls)
    await callback.message.answer(
        "\n".join(lines + ["", HAPP_CONNECT_INSTRUCTION]),
        reply_markup=kb,
        disable_web_page_preview=True,
    )

    # Плюсом отправляем QR-коды для каждого конфига (ничего не режем, только добавляем)
    try:
        try:
            from aiogram.types import InputMediaPhoto  # type: ignore
        except Exception:
            from aiogram.types.input_media_photo import InputMediaPhoto  # type: ignore

        media: List[InputMediaPhoto] = []
        for idx, sub_url in enumerate(urls, start=1):
            qr_buf = await _generate_qr_bytes(sub_url)
            photo = BufferedInputFile(qr_buf.getvalue(), filename=f"sub_qr_{idx}.png")
            # Инструкцию добавляем один раз (в первый QR), чтобы не спамить.
            caption = f"QR #{idx}"
            if idx == 1:
                caption = f"{caption}\n\n{HAPP_CONNECT_INSTRUCTION}"
            media.append(InputMediaPhoto(media=photo, caption=caption))

        if media:
            # Telegram ограничивает media group до 10 элементов
            await callback.message.answer_media_group(media[:10])

    except Exception as e:
        logger.exception("Не удалось отправить QR для all_configs: %s", e)

    await callback.answer()

@router.callback_query(F.data.startswith("sub:config:"))
async def sub_config(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    # Безопасно парсим sub_id из callback_data.
    try:
        sub_id = int(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("Некорректная подписка.", show_alert=True)
        return

    # Проверяем, что подписка принадлежит именно этому пользователю.
    user_row = db.get_or_create_user(callback.from_user.id)
    sub = db.get_subscription_by_id(sub_id)
    if not sub or int(sub["user_id"]) != int(user_row["id"]):
        await callback.answer("Подписка не найдена.", show_alert=True)
        return

    username = sub["marzban_username"]
    try:
        user_info = await marzban.get_user(username)
    except Exception as e:
        logger.exception("Ошибка get_user для конфига: %s", e)
        user_info = None

    sub_url = None
    if user_info:
        sub_url = user_info.get("subscription_url")
    if not sub_url:
        sub_url = sub["sub_url"]

    sub_url = _normalize_sub_url(sub_url)
    if not sub_url:
        await callback.answer("Не удалось получить ссылку подписки.", show_alert=True)
        return

    text = (
        "📄 Конфиг для выбранной подписки:\n\n"
        f"<code>{sub_url}</code>\n\n"
        "\n"
        f"\n{HAPP_CONNECT_INSTRUCTION}\n"
        "Нажми кнопку ниже, чтобы <b>скопировать ссылку</b>\n"
    )
    await callback.message.answer(
        text,
        reply_markup=_copy_kb_single(str(sub_url)),
        disable_web_page_preview=True,
    )

    # QR
    qr_buf = await _generate_qr_bytes(str(sub_url))
    photo = BufferedInputFile(qr_buf.getvalue(), filename="sub_qr.png")
    await callback.message.answer_photo(
        photo=photo,
        caption=(
            "QR-код для этой подписки.\n\n"
            f"{HAPP_CONNECT_INSTRUCTION}"
        ),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("sub:extend:"))
async def sub_extend(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    sub_id = int(callback.data.split(":", 2)[2])
    tg_id = callback.from_user.id
    user_row = db.get_or_create_user(tg_id)
    sub = db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != user_row["id"]:
        await callback.answer("Подписка не найдена.", show_alert=True)
        return

    plan_key = sub["plan_key"]
    is_trial = bool(sub["is_trial"])

    if is_trial:
        # Для триала предлагаем сразу выбрать платный тариф
        from aiogram.types import InlineKeyboardMarkup

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    kb_button("7 дней (25★)", callback_data=f"extendplan:{sub_id}:week_7d", emoji_key="buy"),
                    kb_button("30 дней (50★)", callback_data=f"extendplan:{sub_id}:month_30d", emoji_key="buy"),
                ],
                [kb_button("60 дней (100★)", callback_data=f"extendplan:{sub_id}:month_60d", emoji_key="buy")],
                [kb_button("Назад к списку", callback_data="subs:list", emoji_key="subs")],
            ]
        )
        await callback.message.answer(
            "Это пробная подписка. Выбери тариф, на который хочешь её продлить:",
            reply_markup=kb,
        )
        await callback.answer()
        return

    # Продляем по тому же тарифу
    plan = PLANS.get(plan_key)
    if not plan:
        await callback.answer("Неизвестный тариф для продления.", show_alert=True)
        return

    price = plan["price"]
    days = plan["days"]

    balance = db.get_balance(user_row["id"])
    if balance < price:
        await callback.answer(
            f"Недостаточно средств. Нужно {price}★, а у тебя {balance}★.",
            show_alert=True,
        )
        return

    now_utc = int(datetime.now(timezone.utc).timestamp())
    base = max(sub["end_at"], now_utc)
    new_end = base + days * 24 * 60 * 60

    # Сначала панель. При ошибке срок и Stars в локальной БД не меняем.
    try:
        await marzban.modify_user(
            sub["marzban_username"],
            expire_ts=new_end,
            traffic_limit_bytes=0,
        )
    except Exception as e:
        logger.exception("Ошибка modify_user при продлении: %s", e)
        await callback.answer(
            "Не удалось продлить подписку на сервере. Stars не списаны.",
            show_alert=True,
        )
        return

    db.finalize_paid_renewal(
        user_id=user_row["id"],
        sub_id=sub_id,
        new_end_ts=new_end,
        amount=price,
        tx_type="extend",
        description=f"Продление подписки {plan['title']}",
    )

    await callback.answer("Подписка продлена!", show_alert=False)
    await list_subscriptions(callback)


@router.callback_query(F.data.startswith("extendplan:"))
async def extend_plan_from_trial(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    _, sub_id_str, plan_key = callback.data.split(":", 2)
    sub_id = int(sub_id_str)
    tg_id = callback.from_user.id
    user_row = db.get_or_create_user(tg_id)
    sub = db.get_subscription_by_id(sub_id)
    if not sub or sub["user_id"] != user_row["id"]:
        await callback.answer("Подписка не найдена.", show_alert=True)
        return

    if plan_key not in PLANS:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return
    plan = PLANS[plan_key]
    price = plan["price"]
    days = plan["days"]

    balance = db.get_balance(user_row["id"])
    if balance < price:
        await callback.answer(
            f"Недостаточно средств. Нужно {price}★, а у тебя {balance}★.",
            show_alert=True,
        )
        return

    now_utc = int(datetime.now(timezone.utc).timestamp())
    base = max(sub["end_at"], now_utc)
    new_end = base + days * 24 * 60 * 60

    # Сначала панель. При ошибке локальные данные и Stars не меняем.
    try:
        await marzban.modify_user(
            sub["marzban_username"],
            expire_ts=new_end,
            traffic_limit_bytes=0,
        )
    except Exception as e:
        logger.exception("Ошибка modify_user при конвертации триала: %s", e)
        await callback.answer(
            "Не удалось обновить подписку на сервере. Stars не списаны.",
            show_alert=True,
        )
        return

    db.finalize_paid_renewal(
        user_id=user_row["id"],
        sub_id=sub_id,
        new_end_ts=new_end,
        amount=price,
        tx_type="extend",
        description=f"Продление (конвертация триала) до тарифа {plan['title']}",
        plan_key=plan_key,
        is_trial=False,
    )

    await callback.answer("Триал успешно продлён на платной основе!", show_alert=False)
    await list_subscriptions(callback)


# ---------- Загруженность нод ----------


async def show_nodes_usage(callback: CallbackQuery):
    """Показываем загруженность нод по МЕСЯЧНОМУ трафику.

    Для пользователя выводим только: "имя ноды — процент".
    """
    try:
        usages = await marzban.get_nodes_usage()
    except Exception as e:
        logger.exception("Ошибка получения статистики нод: %s", e)
        await safe_edit(
            callback,
            "Не удалось получить статистику по нодам. Попробуй позже.",
            reply_markup=back_to_main_kb(),
        )
        return

    if not usages:
        text = f"{ce('chart', '📊')} Загруженность серверов:\n\nНет данных по нодам."
        await safe_edit(callback, text, reply_markup=back_to_main_kb())
        return

    # Ёмкость ноды: 25 TB / месяц (берём как условный лимит для расчёта процентов)
    CAPACITY_BYTES = 25 * 1024 ** 4  # 25 TiB в байтах на ноду в месяц

    now_utc = datetime.now(timezone.utc)
    year_month = now_utc.strftime("%Y-%m")

    lines: List[str] = [f"{ce('chart', '📊')} Загруженность серверов (за {year_month}):\n"]
    shown_any = False

    for usage in usages:
        name = usage.get("node_name", f"node_{usage.get('node_id')}")
        if isinstance(name, str) and name.lower() == "master":
            # master обычно агрегирующий, пользователю он не нужен
            continue

        uplink = int(usage.get("uplink", 0) or 0)
        downlink = int(usage.get("downlink", 0) or 0)
        total = uplink + downlink

        # Бейзлайн (на начало месяца)
        base_total = db.get_node_monthly_baseline(str(name), year_month)
        if base_total is None:
            # Если ещё не знаем бейзлайн — считаем текущий total стартовой точкой.
            db.upsert_node_monthly_baseline(str(name), year_month, total)
            month_used = 0
        else:
            month_used = total - int(base_total)
            if month_used < 0:
                # На случай перезапуска статистики на стороне Marzban
                db.upsert_node_monthly_baseline(str(name), year_month, total)
                month_used = 0

        pct = 0.0
        if CAPACITY_BYTES > 0:
            pct = min(100.0, round(month_used / CAPACITY_BYTES * 100, 2))

        shown_any = True
        lines.append(f"🖥 <b>{name}</b> — <b>{pct:.2f}%</b>")

    if not shown_any:
        lines.append("Нет данных по нодам (кроме master).")

    await safe_edit(callback, "\n".join(lines), reply_markup=back_to_main_kb())


# ---------- Баланс: компактная история + пагинация ----------

async def show_balance_page(callback: CallbackQuery, page: int = 1):
    """Показывает баланс + историю операций постранично (по 5)."""
    tg_id = callback.from_user.id
    user_row = db.get_or_create_user(tg_id)

    per_page = 5
    total = db.get_user_transactions_count(user_row["id"])
    pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = max(1, min(int(page), pages))

    offset = (page - 1) * per_page
    tx_rows = db.get_user_transactions(user_row["id"], limit=per_page, offset=offset)

    balance = db.get_balance(user_row["id"])

    lines: List[str] = [f"{ce('balance_bag', '💰')} Твой баланс: <b>{balance} ★</b>\n"]

    if total <= 0:
        lines.append("История операций пуста.")
    else:
        lines.append(f"<b>История операций — страница {page}/{pages}</b> (показано {len(tx_rows)} из {total}):")

        msk = ZoneInfo("Europe/Moscow")
        for tx in tx_rows:
            ts = int(tx["created_at"])
            dt = datetime.fromtimestamp(ts, tz=msk).strftime("%d.%m %H:%M")

            amount = int(tx["amount"])
            sign = ce("plus", "➕") if amount > 0 else ce("minus", "➖")
            amt_abs = abs(amount)

            ttype = (tx["type"] or "").strip()
            desc = (tx["description"] or "").strip()
            details = desc or ttype or "операция"

            lines.append(f"{dt} {sign}{amt_abs}★ — {details}")

    await safe_edit(
        callback,
        "\n".join(lines),
        reply_markup=balance_menu_kb(page=page, pages=pages),
    )


@router.callback_query(F.data.startswith("balance:"))
async def balance_pagination(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    data = callback.data or ""
    if data == "balance:noop":
        await callback.answer()
        return

    if data.startswith("balance:page:"):
        try:
            page = int(data.split(":")[-1])
        except ValueError:
            await callback.answer("Некорректная страница.", show_alert=True)
            return
        await show_balance_page(callback, page=page)
        return

    await callback.answer()

# ---------- Админка ----------




@router.callback_query(F.data.startswith("admin:"))
async def admin_menu(callback: CallbackQuery):
    if not await ensure_subscription_callback(callback):
        return

    tg_id = callback.from_user.id
    if not is_admin(tg_id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    action = callback.data.split(":", 1)[1]

    if action == "users":
        await show_admin_users_page(callback, page=1)

    elif action == "health":
        import shutil
        import sqlite3
        import time as health_time
        from pathlib import Path as HealthPath

        started = health_time.monotonic()
        base_dir = HealthPath(__file__).resolve().parent
        db_path = base_dir / "vpn_bot.db"

        # ---------- SQLite ----------
        db_status = "🔴 Ошибка"
        db_detail = "не проверена"
        issue_count = 0

        try:
            with sqlite3.connect(
                str(db_path),
                timeout=5,
            ) as conn:
                row = conn.execute(
                    "PRAGMA quick_check"
                ).fetchone()

                check_result = (
                    str(row[0])
                    if row
                    else "нет ответа"
                )

                if check_result.lower() == "ok":
                    db_status = "🟢 OK"
                    db_detail = "quick_check: ok"
                else:
                    db_detail = check_result

                table_exists = conn.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'reconcile_issues'
                    """
                ).fetchone()

                if table_exists:
                    issue_row = conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM reconcile_issues
                        """
                    ).fetchone()

                    issue_count = int(
                        issue_row[0] or 0
                    )

        except Exception as exc:
            db_detail = str(exc)[:120]

        # ---------- Remnawave ----------
        panel_status = "🔴 Недоступен"
        panel_detail = "ошибка запроса"
        nodes_count = 0
        panel_ms = 0

        panel_started = health_time.monotonic()

        try:
            await marzban.keepalive()

            nodes = await marzban.get_nodes_usage()
            nodes_count = len(nodes or [])

            panel_ms = int(
                (
                    health_time.monotonic()
                    - panel_started
                )
                * 1000
            )

            panel_status = "🟢 Доступен"
            panel_detail = f"{panel_ms} мс"

        except Exception as exc:
            panel_ms = int(
                (
                    health_time.monotonic()
                    - panel_started
                )
                * 1000
            )

            panel_detail = (
                f"{type(exc).__name__}: {str(exc)[:100]}"
            )

        # ---------- Статистика бота ----------
        try:
            rows = db.get_all_users_with_stats()

            total_users = len(rows)

            active_subs = sum(
                int(row["active_subs"] or 0)
                for row in rows
            )

            total_subs = sum(
                int(row["total_subs"] or 0)
                for row in rows
            )

        except Exception:
            total_users = 0
            active_subs = 0
            total_subs = 0

        # ---------- Память процесса ----------
        memory_mb = 0.0

        try:
            status_text = HealthPath(
                "/proc/self/status"
            ).read_text()

            for line in status_text.splitlines():
                if line.startswith("VmRSS:"):
                    memory_kb = int(
                        line.split()[1]
                    )

                    memory_mb = memory_kb / 1024
                    break

        except Exception:
            pass

        # ---------- Сервер ----------
        disk = shutil.disk_usage("/")
        disk_free_gb = disk.free / 1024 ** 3
        disk_total_gb = disk.total / 1024 ** 3
        disk_used_percent = (
            disk.used * 100 / disk.total
            if disk.total
            else 0
        )

        try:
            load_1, load_5, load_15 = os.getloadavg()
        except Exception:
            load_1 = load_5 = load_15 = 0.0

        try:
            uptime_seconds = int(
                HealthPath("/proc/uptime")
                .read_text()
                .split()[0]
                .split(".")[0]
            )
        except Exception:
            uptime_seconds = 0

        uptime_days, remainder = divmod(
            uptime_seconds,
            86400,
        )

        uptime_hours, remainder = divmod(
            remainder,
            3600,
        )

        uptime_minutes = remainder // 60

        # ---------- Фоновые функции ----------
        background_enabled = os.getenv(
            "BACKGROUND_WORKER_ENABLED",
            "false",
        ).lower() in {"1", "true", "yes", "on"}

        renew_enabled = os.getenv(
            "AUTO_RENEW_ENABLED",
            "false",
        ).lower() in {"1", "true", "yes", "on"}

        reconcile_enabled = os.getenv(
            "RECONCILE_ENABLED",
            "false",
        ).lower() in {"1", "true", "yes", "on"}

        reconcile_autofix = os.getenv(
            "RECONCILE_AUTO_FIX",
            "false",
        ).lower() in {"1", "true", "yes", "on"}

        notify_enabled = bool(
            os.getenv(
                "ADMIN_NOTIFY_BOT_TOKEN",
                "",
            ).strip()
            and os.getenv(
                "ADMIN_NOTIFY_CHAT_ID",
                "",
            ).strip()
        )

        def flag(value: bool) -> str:
            return "🟢 Вкл" if value else "⚪ Выкл"

        total_ms = int(
            (
                health_time.monotonic()
                - started
            )
            * 1000
        )

        text = (
            "🩺 <b>Диагностика VPN-бота</b>\n\n"

            "<b>Сервисы</b>\n"
            f"Бот: 🟢 Работает\n"
            f"Remnawave: {panel_status}\n"
            f"Ответ API: <b>{panel_detail}</b>\n"
            f"Нод в ответе: <b>{nodes_count}</b>\n"
            f"SQLite: {db_status}\n"
            f"Проверка БД: <b>{db_detail}</b>\n\n"

            "<b>Данные</b>\n"
            f"Пользователей: <b>{total_users}</b>\n"
            f"Активных подписок: <b>{active_subs}</b>\n"
            f"Всего подписок: <b>{total_subs}</b>\n"
            f"Проблем сверки: <b>{issue_count}</b>\n\n"

            "<b>Фоновые функции</b>\n"
            f"Напоминания: {flag(background_enabled)}\n"
            f"Автопродление: {flag(renew_enabled)}\n"
            f"Сверка: {flag(reconcile_enabled)}\n"
            f"Автоисправление: {flag(reconcile_autofix)}\n"
            f"Админ-уведомления: {flag(notify_enabled)}\n\n"

            "<b>Сервер</b>\n"
            f"Память бота: <b>{memory_mb:.1f} МБ</b>\n"
            f"Диск: <b>{disk_used_percent:.1f}%</b> "
            f"занято\n"
            f"Свободно: <b>{disk_free_gb:.1f}</b> "
            f"из {disk_total_gb:.1f} ГБ\n"
            f"Load average: "
            f"<b>{load_1:.2f} / {load_5:.2f} / "
            f"{load_15:.2f}</b>\n"
            f"Uptime сервера: "
            f"<b>{uptime_days}д "
            f"{uptime_hours}ч "
            f"{uptime_minutes}м</b>\n\n"

            f"Диагностика выполнена за "
            f"<b>{total_ms} мс</b>."
        )

        await callback.message.answer(
            text,
            reply_markup=admin_menu_kb(),
        )

        await callback.answer()

    elif action == "stats":

        rows = db.get_all_users_with_stats()
        total_users = len(rows)
        total_balance = sum(r["balance"] for r in rows)
        total_spent = sum(r["stars_spent"] for r in rows)
        total_subs = sum(r["total_subs"] for r in rows)
        active_subs = sum(r["active_subs"] for r in rows)

        text = (
            "📊 Общая статистика:\n\n"
            f"Пользователей: <b>{total_users}</b>\n"
            f"Активных подписок: <b>{active_subs}</b>\n"
            f"Всего создано подписок: <b>{total_subs}</b>\n"
            f"Суммарный баланс всех пользователей: <b>{total_balance}★</b>\n"
            f"Всего потрачено Stars: <b>{total_spent}★</b>"
        )
        await callback.message.answer(text)
        await callback.answer()
    elif action == "add_balance":
        admin_state[tg_id] = "add_balance"
        admin_temp[tg_id] = {}
        text = (
            "💸 Начисление баланса.\n\n"
            "Отправь строку:\n"
            "<code>TELEGRAM_ID СУММА</code>\n"
            "(можно добавить комментарий после суммы)\n\n"
            "Например:\n"
            "<code>123456789 50 бонус</code>"
        )
        await callback.message.answer(text)
        await callback.answer()
    elif action == "sub_balance":
        admin_state[tg_id] = "sub_balance"
        admin_temp[tg_id] = {}
        text = (
            "➖ Списание баланса.\n\n"
            "Отправь строку:\n"
            "<code>TELEGRAM_ID СУММА</code>\n"
            "(можно добавить комментарий после суммы)\n\n"
            "Например:\n"
            "<code>123456789 25 возврат</code>"
        )
        await callback.message.answer(text)
        await callback.answer()

    elif action == "broadcast":
        admin_state[tg_id] = "broadcast"
        admin_temp[tg_id] = {}
        text = (
            "📣 Режим рассылки.\n\n"
            "Отправь текст, который нужно разослать всем пользователям."
        )
        await callback.message.answer(text)
        await callback.answer()
    elif action == "search_user":
        admin_state[tg_id] = "search_user"
        admin_temp[tg_id] = {}
        text = (
            "🔎 Поиск пользователя.\n\n"
            "Отправь <code>TELEGRAM_ID</code> пользователя, которого нужно найти."
        )
        await callback.message.answer(text)
        await callback.answer()
    elif action == "manage_subs":
        admin_state[tg_id] = "manage_subs"
        admin_temp[tg_id] = {}
        text = (
            "🔧 Управление подписками.\n\n"
            "Отправь <code>TELEGRAM_ID</code> пользователя, чьи подписки нужно настроить."
        )
        await callback.message.answer(text)
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие админки.", show_alert=True)

async def show_admin_users_page(callback: CallbackQuery, page: int):
    """Постраничный список пользователей в админке."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    db.init_db()

    per_page = 10
    total = db.get_users_count()
    if total <= 0:
        await safe_edit(callback, "👥 Пользователей пока нет.", reply_markup=admin_menu_kb())
        return

    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page), pages))

    offset = (page - 1) * per_page
    rows = db.get_users_with_stats_page(limit=per_page, offset=offset, order_desc=True)

    lines = [f"👥 Пользователи — страница <b>{page}/{pages}</b> (всего: {total})\n"]
    start_num = offset + 1
    for i, row in enumerate(rows, start=start_num):
        uid = row["telegram_id"]
        balance = row["balance"]
        total_subs = row["total_subs"]
        active_subs = row["active_subs"]
        created = ts_to_msk_str(row["created_at"])
        stars_spent = row["stars_spent"]
        lines.append(
            f"{i}) <code>{uid}</code> — баланс <b>{balance}★</b>, подписки <b>{active_subs}/{total_subs}</b>, "
            f"потрачено {stars_spent}★, рег. {created}"
        )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    kb_rows: List[List[InlineKeyboardButton]] = []
    nav: List[InlineKeyboardButton] = []
    if page > 1:
        nav.append(kb_button(text="Пред", callback_data=f"adminusers:page:{page-1}", emoji_key="prev"))
    nav.append(kb_button(text=f"{page}/{pages}", callback_data="adminusers:noop"))
    if page < pages:
        nav.append(kb_button(text="След", callback_data=f"adminusers:page:{page+1}", emoji_key="next"))
    kb_rows.append(nav)
    kb_rows.append([kb_button(text="В админку", callback_data="menu:admin", emoji_key="admin")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await safe_edit(callback, "\n".join(lines), reply_markup=kb)


@router.callback_query(F.data.startswith("adminusers:"))
async def admin_users_pagination(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    data = callback.data or ""
    if data == "adminusers:noop":
        await callback.answer()
        return

    # adminusers:page:N
    parts = data.split(":")
    if len(parts) == 3 and parts[1] == "page":
        try:
            page = int(parts[2])
        except ValueError:
            await callback.answer("Некорректная страница.", show_alert=True)
            return
        await show_admin_users_page(callback, page=page)
        return

    await callback.answer()

@router.callback_query(F.data.startswith("admin_sub:"))
async def admin_sub_actions(callback: CallbackQuery):
    tg_id = callback.from_user.id  # admin
    if not is_admin(tg_id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    _, action, arg = parts

    # 🔒 Полная блокировка пользователя (все его подписки)
    if action == "block_user":
        target_tg_id = int(arg)
        user_row = db.get_user_by_telegram_id(target_tg_id)
        if not user_row:
            await callback.answer("Пользователь не найден.", show_alert=True)
            return

        subs = db.get_user_subscriptions(user_row["id"], include_inactive=False)
        for sub in subs:
            try:
                await marzban.modify_user(sub["marzban_username"], status="disabled")
            except Exception as e:
                logger.exception("Ошибка modify_user при блокировке: %s", e)
            db.set_subscription_active(sub["id"], False)

        await callback.answer("Пользователь заблокирован, все подписки отключены.")
        return

    # Дальше работаем уже с конкретной подпиской
    sub_id = int(arg)
    sub = db.get_subscription_by_id(sub_id)
    if not sub:
        await callback.answer("Подписка не найдена.", show_alert=True)
        return

    username = sub["marzban_username"]

    # ⛔ Отключить одну подписку
    if action == "disable":
        try:
            # В Marzban статусы: active / disabled / on_hold
            await marzban.modify_user(username, status="disabled")
        except Exception as e:
            logger.exception("Ошибка modify_user disable: %s", e)

        db.set_subscription_active(sub_id, False)
        await callback.answer("Подписка отключена.")

    # 🗑 Удалить пользователя в Marzban
    elif action == "delete":
        try:
            await marzban.delete_user(username)
        except Exception as e:
            logger.exception("Ошибка delete_user: %s", e)

        db.set_subscription_active(sub_id, False)
        await callback.answer("Подписка удалена.")

    # ➕/➖ изменить срок действия подписки
    elif action in ("add7", "add30", "sub7", "sub30"):
        # На сколько дней двигаем
        if action == "add7":
            delta_days = 7
        elif action == "add30":
            delta_days = 30
        elif action == "sub7":
            delta_days = -7
        elif action == "sub30":
            delta_days = -30
        else:
            delta_days = 0

        now_utc = int(datetime.now(timezone.utc).timestamp())
        current_end = int(sub["end_at"])

        # По умолчанию просто сдвигаем конец подписки
        if delta_days > 0:
            # Если подписка уже истекла — отталкиваемся от now
            base = max(current_end, now_utc)
            new_end = base + delta_days * 24 * 60 * 60
            deactivate = False
        else:
            new_end_candidate = current_end + delta_days * 24 * 60 * 60
            # Если после уменьшения дата окончания в прошлом или прямо сейчас —
            # считаем, что подписка закончилась и сразу выключаем её.
            if new_end_candidate <= now_utc:
                new_end = now_utc
                deactivate = True
            else:
                new_end = new_end_candidate
                deactivate = False

        # Обновляем дату окончания в своей БД
        db.update_subscription_end(sub_id, new_end)

        # Обновляем пользователя в Marzban
        try:
            if delta_days > 0:
                # ➕ Добавляем дни
                if not sub["is_active"]:
                    # Подписка была отключена -> реактвируем
                    await marzban.modify_user(
                        username,
                        expire_ts=new_end,
                        status="active",
                    )
                    db.set_subscription_active(sub_id, True)
                else:
                    # Просто продление активной подписки
                    await marzban.modify_user(username, expire_ts=new_end)
            else:
                # ➖ Убавляем дни
                if deactivate:
                    # Ушли "в ноль" -> выключаем
                    await marzban.modify_user(
                        username,
                        expire_ts=new_end,
                        status="disabled",
                    )
                    db.set_subscription_active(sub_id, False)
                else:
                    # Просто уменьшили срок, но подписка ещё действует
                    await marzban.modify_user(username, expire_ts=new_end)
        except Exception as e:
            logger.exception("Ошибка modify_user +/- days: %s", e)

        # Уведомление пользователю
        try:
            u = db.get_user_by_id(sub["user_id"])
            if u:
                target_tg = int(u["telegram_id"])
                end_str = ts_to_msk_str(new_end)
                plan_title = PLANS.get(sub["plan_key"], {}).get("title", sub["plan_key"])

                if delta_days > 0:
                    msg = (
                        f"👮 Администратор добавил к твоей подписке <b>{plan_title}</b> "
                        f"<b>{delta_days}</b> дней.\n"
                        f"Теперь действует до: <b>{end_str}</b>."
                    )
                    # если хочешь, можно дописать сюда строку про "подписка была вновь активирована"
                else:
                    if deactivate:
                        msg = (
                            f"👮 Администратор убавил у твоей подписки <b>{plan_title}</b> "
                            f"<b>{abs(delta_days)}</b> дней.\n"
                            f"Подписка завершена и отключена."
                        )
                    else:
                        msg = (
                            f"👮 Администратор убавил у твоей подписки <b>{plan_title}</b> "
                            f"<b>{abs(delta_days)}</b> дней.\n"
                            f"Теперь действует до: <b>{end_str}</b>."
                        )

                await callback.bot.send_message(target_tg, msg)
        except Exception as e:
            logger.exception(
                "Не удалось уведомить пользователя об изменении дней: %s", e
            )

        # Ответ админу
        if delta_days > 0:
            await callback.answer(f"К подписке добавлено {delta_days} дней.")
        else:
            if deactivate:
                await callback.answer(
                    f"У подписки убавлено {abs(delta_days)} дней и она отключена."
                )
            else:
                await callback.answer(
                    f"У подписки убавлено {abs(delta_days)} дней."
                )

    else:
        await callback.answer("Неизвестное действие.", show_alert=True)

@router.message()
async def admin_text_states(message: Message):
    if not await is_user_subscribed_to_info_channel(message.bot, message.from_user.id):
        await show_subscription_required_message(message)
        return

    """
    Обрабатываем текстовые ответы админа в режимах:
    - add_balance
    - sub_balance
    - broadcast
    - manage_subs
    - search_user
    Остальные сообщения просим пользоваться /start.
    """
    tg_id = message.from_user.id
    if tg_id not in admin_state or not is_admin(tg_id):
        await message.answer("Используй /start, чтобы открыть меню бота.")
        return

    state = admin_state.get(tg_id)

    if state == "add_balance":
        parts = (message.text or "").strip().split(maxsplit=2)
        if len(parts) < 2:
            await message.answer(
                "Неверный формат.\nОтправь: <code>TELEGRAM_ID СУММА [комментарий]</code>"
            )
            return
        try:
            target_tg_id = int(parts[0])
            amount = int(parts[1])
        except ValueError:
            await message.answer("ID и сумма должны быть числами.")
            return
        if amount <= 0:
            await message.answer("Сумма должна быть больше 0.")
            return
        comment = parts[2].strip() if len(parts) >= 3 else ""

        user_row = db.get_user_by_telegram_id(target_tg_id)
        if not user_row:
            await message.answer(
                f"Пользователь <code>{target_tg_id}</code> не найден в базе.\n"
                "Баланс не начислен. Пользователь должен сначала открыть бота через /start."
            )
            admin_state.pop(tg_id, None)
            admin_temp.pop(tg_id, None)
            return

        desc = "Админ начислил баланс" + (f": {comment}" if comment else "")
        try:
            new_balance = db.change_balance(
                user_row["id"],
                amount,
                tx_type="admin_topup",
                description=desc,
            )
        except Exception as e:
            await message.answer(f"Ошибка операции: {e}")
            return

        await message.answer(
            f"Баланс пользователя {target_tg_id} пополнен на {amount} ★.\n"
            f"Новый баланс: {new_balance} ★."
        )
        try:
            msg = f"👮 Администратор начислил тебе {amount} ★ на баланс."
            if comment:
                msg += f"\n📝 Комментарий: {comment}"
            await message.bot.send_message(target_tg_id, msg)
        except Exception as e:
            logger.exception("Не удалось отправить уведомление пользователю: %s", e)

        admin_state.pop(tg_id, None)
        admin_temp.pop(tg_id, None)

    elif state == "sub_balance":
        parts = (message.text or "").strip().split(maxsplit=2)
        if len(parts) < 2:
            await message.answer(
                "Неверный формат.\nОтправь: <code>TELEGRAM_ID СУММА [комментарий]</code>"
            )
            return
        try:
            target_tg_id = int(parts[0])
            amount = int(parts[1])
        except ValueError:
            await message.answer("ID и сумма должны быть числами.")
            return
        if amount <= 0:
            await message.answer("Сумма должна быть больше 0.")
            return
        comment = parts[2].strip() if len(parts) >= 3 else ""

        user_row = db.get_user_by_telegram_id(target_tg_id)
        if not user_row:
            await message.answer(
                f"Пользователь <code>{target_tg_id}</code> не найден в базе.\n"
                "Списание не выполнено. Проверь ID или попроси пользователя сначала открыть бота через /start."
            )
            admin_state.pop(tg_id, None)
            admin_temp.pop(tg_id, None)
            return

        current_balance = db.get_balance(user_row["id"])
        if current_balance < amount:
            await message.answer(
                f"Недостаточно средств у пользователя. Сейчас {current_balance} ★, пытаешься списать {amount} ★."
            )
            return

        desc = "Админ списал баланс" + (f": {comment}" if comment else "")
        try:
            new_balance = db.change_balance(
                user_row["id"],
                -amount,
                tx_type="admin_withdraw",
                description=desc,
                count_as_spent=False,
            )
        except Exception as e:
            await message.answer(f"Ошибка операции: {e}")
            return

        await message.answer(
            f"Баланс пользователя {target_tg_id} уменьшен на {amount} ★.\n"
            f"Новый баланс: {new_balance} ★."
        )
        try:
            msg = f"👮 Администратор списал с твоего баланса {amount} ★."
            if comment:
                msg += f"\n📝 Комментарий: {comment}"
            await message.bot.send_message(target_tg_id, msg)
        except Exception as e:
            logger.exception("Не удалось отправить уведомление пользователю: %s", e)

        admin_state.pop(tg_id, None)
        admin_temp.pop(tg_id, None)
    elif state == "broadcast":
        text = message.text or ""
        rows = db.get_all_users_with_stats()

        sent = 0
        skipped = 0
        failed = 0

        expected_delivery_errors = (
            "bot was blocked by the user",
            "user is deactivated",
            "chat not found",
            "user not found",
        )

        for row in rows:
            uid = row["telegram_id"]

            try:
                await message.bot.send_message(uid, text)
                sent += 1

            except Exception as e:
                error_text = str(e).lower()

                if any(
                    marker in error_text
                    for marker in expected_delivery_errors
                ):
                    # Пользователь удалён, заблокировал бота
                    # или чат больше недоступен. Это не авария.
                    skipped += 1
                    logger.info(
                        "Рассылка пропущена для %s: %s",
                        uid,
                        e,
                    )
                    continue

                failed += 1
                logger.exception(
                    "Неожиданная ошибка рассылки %s: %s",
                    uid,
                    e,
                )

        await message.answer(
            "📣 Рассылка завершена.\n\n"
            f"✅ Доставлено: <b>{sent}</b>\n"
            f"⏭ Недоступны: <b>{skipped}</b>\n"
            f"❌ Другие ошибки: <b>{failed}</b>"
        )

        admin_state.pop(tg_id, None)
        admin_temp.pop(tg_id, None)

    elif state == "manage_subs":
        try:
            target_tg_id = int((message.text or "").strip())
        except ValueError:
            await message.answer("Нужно отправить числовой TELEGRAM_ID.")
            return

        user_row = db.get_user_by_telegram_id(target_tg_id)
        if not user_row:
            await message.answer("Пользователь не найден.")
            return

        subs = db.get_user_subscriptions(user_row["id"], include_inactive=False)
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        if not subs:
            await message.answer("У пользователя нет подписок.")
            admin_state.pop(tg_id, None)
            admin_temp.pop(tg_id, None)
            return

        lines = [f"Подписки пользователя {target_tg_id}:\n"]
        kb_rows: List[List[InlineKeyboardButton]] = []
        for idx, sub in enumerate(subs, start=1):
            plan_key = sub["plan_key"]
            plan_title = PLANS.get(plan_key, {}).get("title", plan_key)
            end_str = ts_to_msk_str(sub["end_at"])
            status = "активна" if sub["is_active"] else "отключена"
            lines.append(f"{idx}) {plan_title} — до {end_str} ({status})")

            sub_id = sub["id"]
            kb_rows.append(
                [
                    kb_button(
                        text=f"Откл. #{idx}",
                        callback_data=f"admin_sub:disable:{sub_id}",
                        emoji_key="success",
                    ),
                    kb_button(
                        text=f"Удалить #{idx}",
                        callback_data=f"admin_sub:delete:{sub_id}",
                        emoji_key="success",
                    ),
                ]
            )
            kb_rows.append(
                [
                    kb_button(
                        text=f"+7д #{idx}",
                        callback_data=f"admin_sub:add7:{sub_id}",
                        emoji_key="success",
                    ),
                    kb_button(
                        text=f"+30д #{idx}",
                        callback_data=f"admin_sub:add30:{sub_id}",
                        emoji_key="success",
                    ),
                ]
            )
            kb_rows.append(
                [
                    kb_button(
                        text=f"➖7д #{idx}",
                        callback_data=f"admin_sub:sub7:{sub_id}",
                        emoji_key="wallet",
                    ),
                    kb_button(
                        text=f"➖30д #{idx}",
                        callback_data=f"admin_sub:sub30:{sub_id}",
                        emoji_key="wallet",
                    ),
                ]
            )

        # Кнопка блокировки пользователя
        kb_rows.append(
            [
                kb_button(
                    text="Заблокировать пользователя",
                    callback_data=f"admin_sub:block_user:{target_tg_id}",
                    emoji_key="success",
                )
            ]
        )
        kb_rows.append(
            [kb_button(text="В админку", callback_data="menu:admin", emoji_key="admin")]
        )

        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        await message.answer("\n".join(lines), reply_markup=kb)
        admin_state.pop(tg_id, None)
        admin_temp.pop(tg_id, None)

    elif state == "search_user":
        try:
            target_tg_id = int((message.text or "").strip())
        except ValueError:
            await message.answer("Нужно отправить числовой TELEGRAM_ID.")
            return

        user_row = db.get_user_by_telegram_id(target_tg_id)
        if not user_row:
            await message.answer("Пользователь не найден.")
            # Остаёмся в режиме поиска, чтобы можно было ввести ID ещё раз
            return

        subs = db.get_user_subscriptions(user_row["id"], include_inactive=True)
        active_subs = [s for s in subs if s["is_active"]]
        total_subs = len(subs)
        active_cnt = len(active_subs)

        created_str = ts_to_msk_str(int(user_row["created_at"]))
        balance = int(user_row["balance"])
        stars_spent = int(user_row["stars_spent"])

        lines: List[str] = []
        lines.append(f"👤 Пользователь <code>{target_tg_id}</code>\n")
        lines.append(f"Баланс: <b>{balance}★</b>")
        lines.append(f"Подписок: активных <b>{active_cnt}</b> / всего <b>{total_subs}</b>")
        lines.append(f"Зарегистрирован: <b>{created_str}</b>")
        lines.append(f"Всего потрачено Stars: <b>{stars_spent}★</b>\n")

        if subs:
            lines.append("📂 Подписки:")
            for idx, sub in enumerate(subs, start=1):
                plan_key = sub["plan_key"]
                plan_title = PLANS.get(plan_key, {}).get("title", plan_key)
                end_str = ts_to_msk_str(int(sub["end_at"]))
                status = "активна" if sub["is_active"] else "отключена"
                lines.append(f"{idx}) {plan_title} — до {end_str} ({status})")
        else:
            lines.append("У пользователя нет подписок.")

        await message.answer("\n".join(lines))

        admin_state.pop(tg_id, None)
        admin_temp.pop(tg_id, None)

    else:
        await message.answer("Неизвестное состояние. Нажми /start.")
        admin_state.pop(tg_id, None)
        admin_temp.pop(tg_id, None)


# ---------- Фоновые задачи: уведомления и автопродление ----------

AUTO_RENEW_ENABLED = os.getenv("AUTO_RENEW_ENABLED", "true").lower() in ("1", "true", "yes")
AUTO_RENEW_FORCE_MONTH = os.getenv("AUTO_RENEW_FORCE_MONTH", "false").lower() in ("1", "true", "yes")
AUTO_RENEW_CHECK_EVERY_SEC = int(os.getenv("AUTO_RENEW_CHECK_EVERY_SEC", "900") or 900)

REMIND_BEFORE_SEC = int(os.getenv("SUB_REMIND_BEFORE_SEC", str(24 * 60 * 60)) or (24 * 60 * 60))

async def process_subscription_reminders_and_auto_renew(bot: Bot):
    """Напоминания за 3 дня и сутки, контроль трафика триала,
    истечение подписок и автоматическое продление.
    """
    # Даже если напоминания/автопродление выключены, нам всё равно нужно
    # отрабатывать истечение подписок (и уведомлять пользователя).
    if REMIND_BEFORE_SEC <= 0 and not AUTO_RENEW_ENABLED:
        # напоминания выключены и автопродление выключено — но уведомление об окончании
        # всё равно отправим (см. блок ниже)
        pass

    db.init_db()

    now_ts = int(datetime.now(timezone.utc).timestamp())
    subs = db.get_active_subscriptions_with_users()

    for sub in subs:
        try:
            sub_id = int(sub["id"])
            user_id = int(sub["user_id"])
            tg_id = int(sub["telegram_id"])
            end_at = int(sub["end_at"])
            plan_key = str(sub["plan_key"])
            is_trial = bool(sub["is_trial"])
            marzban_username = str(sub["marzban_username"])

            plan = PLANS.get(plan_key)
            if not plan:
                continue

            # Для авто-реню — какие дни/цена берём
            renew_plan = plan
            if AUTO_RENEW_FORCE_MONTH and "month_30d" in PLANS:
                renew_plan = PLANS["month_30d"]

            renew_days = int(renew_plan["days"])
            renew_price = int(renew_plan["price"])

            # 1) Напоминания по сроку действия
            seconds_left = end_at - now_ts

            reminder_kind = None
            reminder_period = None

            # Не отправляем сразу два уведомления, если осталось меньше суток.
            if (
                24 * 60 * 60 < seconds_left
                <= 3 * 24 * 60 * 60
            ):
                reminder_kind = "remind_3d"
                reminder_period = "меньше 3 дней"

            elif 0 < seconds_left <= 24 * 60 * 60:
                reminder_kind = "remind_24h"
                reminder_period = "меньше суток"

            if (
                reminder_kind
                and db.mark_subscription_event(
                    sub_id,
                    reminder_kind,
                    end_at,
                )
            ):
                end_str = ts_to_msk_str(end_at)
                bal = db.get_balance(user_id)

                if is_trial:
                    text_message = (
                        "⏰ <b>Пробная подписка скоро закончится</b>\n\n"
                        f"Осталось: <b>{reminder_period}</b>\n"
                        f"Дата окончания: <b>{end_str}</b>\n\n"
                        "Чтобы VPN не отключился, перейди на платный тариф."
                    )
                elif bal >= renew_price and AUTO_RENEW_ENABLED:
                    text_message = (
                        "⏰ <b>Подписка скоро продлится</b>\n\n"
                        f"Осталось: <b>{reminder_period}</b>\n"
                        f"Дата окончания: <b>{end_str}</b>\n"
                        f"Баланс: <b>{bal}★</b>\n\n"
                        "✅ Stars достаточно для автоматического продления."
                    )
                else:
                    text_message = (
                        "⏰ <b>Подписка скоро закончится</b>\n\n"
                        f"Осталось: <b>{reminder_period}</b>\n"
                        f"Дата окончания: <b>{end_str}</b>\n"
                        f"Баланс: <b>{bal}★</b>\n"
                        f"Для продления нужно: <b>{renew_price}★</b>."
                    )

                try:
                    await bot.send_message(
                        tg_id,
                        text_message,
                        reply_markup=expired_subscription_kb(),
                    )
                except Exception as e:
                    error_text = str(e).lower()

                    expected_delivery_errors = (
                        "bot was blocked by the user",
                        "user is deactivated",
                        "chat not found",
                        "user not found",
                    )

                    if any(
                        marker in error_text
                        for marker in expected_delivery_errors
                    ):
                        # Пользователь заблокировал бота,
                        # удалил аккаунт или чат недоступен.
                        # Это обычная недоставка, а не авария.
                        logger.info(
                            "Напоминание не доставлено %s: %s",
                            tg_id,
                            e,
                        )
                    else:
                        logger.exception(
                            "Не удалось отправить напоминание %s: %s",
                            tg_id,
                            e,
                        )

            # Контроль трафика пробной подписки.
            if is_trial and end_at > now_ts:
                try:
                    remote_user = await marzban.get_user(
                        marzban_username
                    )

                    used, limit = _extract_user_traffic(
                        remote_user
                    )

                    traffic_kind = None
                    traffic_title = None

                    if limit > 0:
                        percent = int(used * 100 / limit)

                        if percent >= 95:
                            traffic_kind = "traffic_95"
                            traffic_title = (
                                "⚠️ Осталось меньше 5% "
                                "трафика триала"
                            )
                        elif percent >= 80:
                            traffic_kind = "traffic_80"
                            traffic_title = (
                                "⚠️ Использовано 80% "
                                "трафика триала"
                            )

                    if (
                        traffic_kind
                        and db.mark_subscription_event(
                            sub_id,
                            traffic_kind,
                            end_at,
                        )
                    ):
                        remaining = max(0, limit - used)
                        percent = min(
                            999,
                            int(used * 100 / limit),
                        )

                        await bot.send_message(
                            tg_id,
                            f"<b>{traffic_title}</b>\n\n"
                            f"Использовано: "
                            f"<b>{_format_traffic(used)}</b>\n"
                            f"Лимит: "
                            f"<b>{_format_traffic(limit)}</b>\n"
                            f"Осталось: "
                            f"<b>{_format_traffic(remaining)}</b>\n"
                            f"Заполнено: <b>{percent}%</b>\n\n"
                            "Для безлимитного трафика "
                            "перейди на платный тариф.",
                            reply_markup=expired_subscription_kb(),
                        )

                except Exception as e:
                    # Ошибка статистики не должна мешать окончанию
                    # подписок и автопродлению.
                    logger.warning(
                        "Не удалось проверить трафик %s: %s",
                        marzban_username,
                        e,
                    )


            # 2) Истечение / автопродление (после окончания)
            #    Нужно уведомлять пользователя после окончания ЛЮБОЙ подписки, включая триал.
            #    Также обязательно выключаем истёкшие подписки, чтобы они не висели как активные.
            if end_at <= now_ts:
                end_str = ts_to_msk_str(end_at)

                # --- ТРИАЛ ---
                if is_trial:
                    if db.mark_subscription_event(sub_id, "expired_trial", end_at):
                        db.set_subscription_active(sub_id, False)
                        try:
                            await marzban.modify_user(marzban_username, status="disabled")
                        except Exception as e:
                            logger.exception("Ошибка modify_user при окончании триала: %s", e)

                        try:
                            await bot.send_message(
                                tg_id,
                                f"⛔ <b>Пробная</b> подписка закончилась ({end_str}).\n\n"
                                "Чтобы продолжить пользоваться VPN — купи подписку в боте.",
                                reply_markup=expired_subscription_kb(),
                            )
                        except Exception as e:
                            logger.exception(
                                "Не удалось отправить уведомление о конце триала %s: %s",
                                tg_id,
                                e,
                            )

                    continue

                # --- ПЛАТНАЯ ---
                if AUTO_RENEW_ENABLED:
                    # Дедуп по старому end_at (чтобы не продлить дважды)
                    if not db.mark_subscription_event(sub_id, "auto_renew_try", end_at):
                        continue

                    bal = db.get_balance(user_id)
                    if bal < renew_price:
                        # Денег нет — один раз отключаем подписку и уведомляем
                        if db.mark_subscription_event(sub_id, "expired_no_money", end_at):
                            # 1) в нашей БД помечаем подписку как неактивную
                            db.set_subscription_active(sub_id, False)

                            # 2) в Marzban выключаем пользователя
                            try:
                                await marzban.modify_user(marzban_username, status="disabled")
                            except Exception as e:
                                logger.exception(
                                    "Ошибка modify_user при отключении из-за недостатка средств: %s",
                                    e,
                                )

                            # 3) уведомление пользователю
                            try:
                                await bot.send_message(
                                    tg_id,
                                    "⛔ Подписка закончилась, а на балансе недостаточно Stars для автопродления.\n"
                                    f"Нужно: <b>{renew_price}★</b>, сейчас: <b>{bal}★</b>.",
                                    reply_markup=expired_subscription_kb(),
                                )
                            except Exception as e:
                                logger.exception(
                                    "Не удалось отправить уведомление об окончании %s: %s",
                                    tg_id,
                                    e,
                                )

                        # В любом случае дальше эту подписку уже не трогаем
                        continue

                    base = max(end_at, now_ts)
                    new_end = base + renew_days * 24 * 60 * 60

                    # Обновляем в БД
                    db.update_subscription_end(sub_id, new_end)

                    # Обновляем в Marzban
                    try:
                        await marzban.modify_user(marzban_username, expire_ts=new_end)
                    except Exception as e:
                        logger.exception("Ошибка modify_user при автопродлении: %s", e)

                    # Списываем Stars
                    try:
                        db.change_balance(
                            user_id,
                            -renew_price,
                            tx_type="auto_renew",
                            description=(
                                "Автопродление подписки "
                                f"{renew_plan.get('title', plan_key)}"
                            ),
                        )
                    except Exception as e:
                        logger.exception("Ошибка списания Stars при автопродлении: %s", e)

                    # Уведомляем пользователя
                    try:
                        end_str2 = ts_to_msk_str(new_end)
                        await bot.send_message(
                            tg_id,
                            f"🔁 Подписка продлена автоматически на <b>{renew_days}</b> дней.\n"
                            f"Теперь действует до: <b>{end_str2}</b>.",
                        )
                    except Exception as e:
                        logger.exception(
                            "Не удалось отправить сообщение об автопродлении %s: %s",
                            tg_id,
                            e,
                        )

                    continue

                # Автопродление выключено: просто завершаем подписку и уведомляем
                if db.mark_subscription_event(sub_id, "expired", end_at):
                    db.set_subscription_active(sub_id, False)
                    try:
                        await marzban.modify_user(marzban_username, status="disabled")
                    except Exception as e:
                        logger.exception("Ошибка modify_user при окончании подписки: %s", e)

                    bal = db.get_balance(user_id)
                    try:
                        if bal < renew_price:
                            msg = (
                                "⛔ Подписка закончилась.\n"
                                f"На балансе <b>{bal}★</b>, для продления нужно <b>{renew_price}★</b>."
                            )
                        else:
                            msg = (
                                "⛔ Подписка закончилась.\n"
                                f"На балансе <b>{bal}★</b> — можешь продлить/купить новую подписку."
                            )
                        await bot.send_message(
                            tg_id,
                            msg,
                            reply_markup=expired_subscription_kb(),
                        )
                    except Exception as e:
                        logger.exception(
                            "Не удалось отправить уведомление об окончании %s: %s",
                            tg_id,
                            e,
                        )

                continue


        except Exception as e:
            logger.exception("Ошибка обработки подписки в фоне: %s", e)

async def process_referral_notifications(bot: Bot):
    """Уведомляем о реферальных начислениях, которые уже выданы в БД."""
    db.init_db()
    rows = db.get_pending_referral_rewards(limit=50)
    for rr in rows:
        rid = int(rr["id"])
        referred_tg = int(rr["referred_tg"])
        referrer_tg = int(rr["referrer_tg"])
        amount = int(rr["amount"])

        try:
            await bot.send_message(
                referrer_tg,
                f"🎉 Реферальный бонус!\n"
                f"Пользователь по твоей ссылке впервые пополнил баланс на <b>{amount}★</b>.\n"
                f"Тебе начислено <b>{amount}★</b> (одноразово).",
            )
        except Exception as e:
            logger.exception("Не удалось отправить реф-уведомление рефереру %s: %s", referrer_tg, e)

        try:
            await bot.send_message(
                referred_tg,
                "✅ Пополнение зафиксировано.\n"
                "🎁 Пригласившему пользователю начислен реферальный бонус (одноразово).",
            )
        except Exception as e:
            logger.exception("Не удалось отправить реф-уведомление приглашённому %s: %s", referred_tg, e)

        db.mark_referral_reward_notified(rid)


async def background_worker(bot: Bot):
    """Запускается из bot.py как asyncio task."""
    while True:
        try:
            await process_subscription_reminders_and_auto_renew(bot)
            await process_referral_notifications(bot)
        except Exception as e:
            logger.exception("Ошибка в background_worker: %s", e)

        await asyncio.sleep(AUTO_RENEW_CHECK_EVERY_SEC)
