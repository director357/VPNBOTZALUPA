from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

STARS_PARTNER_URL = os.getenv("STARS_PARTNER_URL", "").strip()
# URLs for Happ clients
ANDROID_URL = "https://play.google.com/store/apps/details?id=com.happproxy"
IOS_URL = "https://apps.apple.com/ru/app/incy/id6756943388"
WINDOWS_URL = "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe"
MAC_URL = "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973"

# Custom emoji IDs для кнопок BlackHole VPN.
# Если aiogram/Bot API на сервере не поддержит icon_custom_emoji_id,
# helper kb_button автоматически сделает обычную кнопку без падения бота.
CUSTOM_EMOJI = {
    "channel": "5350572310627632617",        # подписка на паблик
    "success": "5219899949281453881",          # галочка / успех
    "buy": "5258024802010026053",              # купить подписку
    "stars": "5258039825805624495",            # баланс / Stars
    "subs_old": "5341492148468465410",         # старая папка / запасной вариант
    "subs": "5393302369024882368",             # мои подписки / замочек
    "wallet": "5258204546391351475",           # пополнить баланс / кошелёк
    "key": "5454386656628991407",              # получить конфиги / ключ
    "buy_new": "5274008024585871702",          # купить новую подписку
    "referrals": "5258513401784573443",        # рефералы
    "download": "5258084656674250503",         # скачать клиент
    "nodes_old": "5454074580010295588",        # старая загруженность / запасной вариант
    "nodes": "5258420634785947640",            # загруженность / обновление
    "support": "5454371323595744068",          # поддержка
    "admin": "5453991094435997597",            # админ-панель / два меча
    "home": "5257963315258204021",             # в главное меню / домик
    "next": "5260450573768990626",             # след / стрелка вправо
    "prev": "5258236805890710909",             # пред / стрелка влево
    "users": "5258362837411045098",            # пользователи / человечек
    "search": "5429571366384842791",           # поиск / лупа
    "delete": "5258130763148172425",           # удалить подписку / мусорное ведро
}

def kb_button(
    text: str,
    *,
    emoji_key: str | None = None,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
    **kwargs,
) -> InlineKeyboardButton:
    """Создаёт InlineKeyboardButton с кастомной emoji-иконкой.

    Работает безопасно: если установленная версия aiogram ещё не знает поля
    icon_custom_emoji_id/style, кнопка создастся без кастомной иконки.
    """
    data = {"text": text}
    if callback_data is not None:
        data["callback_data"] = callback_data
    if url is not None:
        data["url"] = url

    emoji_id = CUSTOM_EMOJI.get(emoji_key or "")
    if emoji_id:
        data["icon_custom_emoji_id"] = emoji_id
    if style:
        data["style"] = style

    data.update(kwargs)

    try:
        return InlineKeyboardButton(**data)
    except Exception:
        # Fallback для старого aiogram/Bot API.
        data.pop("icon_custom_emoji_id", None)
        data.pop("style", None)
        return InlineKeyboardButton(**data)


def info_channel_kb(
    channel_url: str,
) -> InlineKeyboardMarkup:
    """Клавиатура обязательной подписки на инфо-паблик."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                kb_button(
                    "Подписаться на паблик",
                    url=channel_url,
                    emoji_key="channel",
                    style="primary",
                )
            ],
            [
                kb_button(
                    "Проверить подписку",
                    callback_data="check_subscription",
                    emoji_key="success",
                    style="success",
                )
            ],
        ]
    )

def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [
            kb_button("Мой баланс", callback_data="menu:balance", emoji_key="stars"),
            kb_button("Рефералы", callback_data="menu:referrals", emoji_key="referrals"),
        ],
        [
            kb_button("Купить подписку", callback_data="menu:buy", emoji_key="buy", style="primary"),
            kb_button("Мои подписки", callback_data="menu:subs", emoji_key="subs"),
        ],
        [
            kb_button("Скачать клиент", callback_data="menu:manual", emoji_key="download"),
            kb_button("Загруженность серверов", callback_data="menu:nodes", emoji_key="nodes"),
        ],
        [
            kb_button("Пополнить баланс", callback_data="menu:refill", emoji_key="wallet", style="primary"),
        ],
    ]

    # Кнопка «Купить Stars у партнёра» показывается только если URL задан.
    if STARS_PARTNER_URL:
        buttons.append([
            kb_button(
                "Купить Stars у партнёра",
                url=STARS_PARTNER_URL,
                style="success",
                emoji_key="stars",
            ),
        ])

    buttons.append([
        kb_button("Поддержка", callback_data="menu:support", emoji_key="support"),
    ])

    if is_admin:
        buttons.append([kb_button("Админ-панель", callback_data="menu:admin", emoji_key="admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [kb_button("В главное меню", callback_data="menu:main", emoji_key="home")]
        ]
    )


def balance_menu_kb(page: int = 1, pages: int = 1) -> InlineKeyboardMarkup:
    rows = []

    # Навигация по истории операций (если страниц больше 1)
    if int(pages) > 1:
        nav = []
        if int(page) > 1:
            nav.append(kb_button("Пред", callback_data=f"balance:page:{int(page)-1}", emoji_key="prev"))
        nav.append(kb_button(f"{int(page)}/{int(pages)}", callback_data="balance:noop"))
        if int(page) < int(pages):
            nav.append(kb_button("След", callback_data=f"balance:page:{int(page)+1}", emoji_key="next"))
        rows.append(nav)

    rows.append([kb_button("Пополнить баланс", callback_data="menu:refill", emoji_key="wallet", style="primary")])
    rows.append([kb_button("В главное меню", callback_data="menu:main", emoji_key="home")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def expired_subscription_kb() -> InlineKeyboardMarkup:
    """Клавиатура для уведомления об окончании подписки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                kb_button("Пополнить баланс", callback_data="menu:refill", emoji_key="wallet", style="primary"),
                kb_button("Купить подписку", callback_data="menu:buy", emoji_key="buy", style="primary"),
            ],
            [kb_button("Мои подписки", callback_data="subs:list", emoji_key="subs")],
            [kb_button("В главное меню", callback_data="menu:main", emoji_key="home")],
        ]
    )


def refill_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                kb_button(
                    "Оплатить картой / СБП",
                    callback_data="platega:menu",
                    emoji_key="wallet",
                    style="primary",
                )
            ],
            [
                kb_button(
                    "Оплатить через Stars",
                    callback_data="stars:menu",
                    emoji_key="stars",
                    style="success",
                )
            ],
            [kb_button("Назад", callback_data="menu:balance", emoji_key="prev")],
        ]
    )


def stars_refill_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                kb_button("25★", callback_data="topup:25", emoji_key="stars"),
                kb_button("50★", callback_data="topup:50", emoji_key="stars"),
            ],
            [
                kb_button("100★", callback_data="topup:100", emoji_key="stars"),
                kb_button("200★", callback_data="topup:200", emoji_key="stars"),
            ],
            [kb_button("Назад", callback_data="menu:refill", emoji_key="prev")],
        ]
    )


def platega_refill_kb(options) -> InlineKeyboardMarkup:
    rows = []
    pair = []
    for option in options:
        pair.append(
            kb_button(
                f"{int(option.credit_amount)}★ — {int(option.rub_amount)} ₽",
                callback_data=(
                    f"platega:pay:{int(option.rub_amount)}:"
                    f"{int(option.credit_amount)}"
                ),
                emoji_key="wallet",
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([kb_button("Назад", callback_data="menu:refill", emoji_key="prev")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                kb_button("Пробный 3 дня", callback_data="buy:trial_3d", style="success"),
                kb_button("7 дней (25★)", callback_data="buy:week_7d", emoji_key="buy"),
            ],
            [
                kb_button("30 дней (50★)", callback_data="buy:month_30d", emoji_key="buy"),
                kb_button("60 дней (100★)", callback_data="buy:month_60d", emoji_key="buy"),
            ],
            [kb_button("Пополнить баланс", callback_data="menu:refill", emoji_key="wallet", style="primary")],
            [kb_button("В главное меню", callback_data="menu:main", emoji_key="home")],
        ]
    )


def subscriptions_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [kb_button("Мои подписки", callback_data="subs:list", emoji_key="subs")],
            [kb_button("Получить все конфиги", callback_data="subs:all_configs", emoji_key="key")],
            [kb_button("Купить новую подписку", callback_data="menu:buy", emoji_key="buy_new", style="primary")],
            [kb_button("Удалить подписку", callback_data="subs:delete_menu", emoji_key="delete", style="danger")],
            [kb_button("В главное меню", callback_data="menu:main", emoji_key="home")],
        ]
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                kb_button("Пользователи", callback_data="admin:users", emoji_key="users"),
                kb_button("Поиск", callback_data="admin:search_user", emoji_key="search"),
            ],
            [
                kb_button("Общая статистика", callback_data="admin:stats", emoji_key="nodes"),
                kb_button("Начислить баланс", callback_data="admin:add_balance", emoji_key="wallet"),
            ],
            [
                kb_button("Управление подписками", callback_data="admin:manage_subs", emoji_key="subs"),
                kb_button("Рассылка", callback_data="admin:broadcast", emoji_key="support"),
            ],
            [
                kb_button(
                    "Диагностика",
                    callback_data="admin:health",
                    emoji_key="nodes",
                    style="primary",
                )
            ],
            [kb_button("Списать баланс", callback_data="admin:sub_balance", emoji_key="wallet")],
            [kb_button("В главное меню", callback_data="menu:main", emoji_key="home")],
        ]
    )


def happ_manual_kb() -> InlineKeyboardMarkup:
    """
    Клавиатура для пункта 'Скачать клиент'.
    Кнопки-платформы ведут сразу на загрузку Happ, плюс кнопка назад.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                kb_button("Android", url=ANDROID_URL, emoji_key="download"),
                kb_button("iOS", url=IOS_URL, emoji_key="download"),
            ],
            [
                kb_button("Windows", url=WINDOWS_URL, emoji_key="download"),
                kb_button("macOS", url=MAC_URL, emoji_key="download"),
            ],
            [kb_button("В главное меню", callback_data="menu:main", emoji_key="home")],
        ]
    )


def support_kb(username: str) -> InlineKeyboardMarkup:
    """
    Кнопка, ведущая сразу в ЛС к саппорту.
    """
    if not username.startswith("@"):
        username = "@" + username
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [kb_button("Написать в поддержку", url=f"https://t.me/{username.lstrip('@')}", emoji_key="support")],
            [kb_button("В главное меню", callback_data="menu:main", emoji_key="home")],
        ]
    )
