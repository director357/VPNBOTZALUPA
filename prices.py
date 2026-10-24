"""Единый источник правды по ценам, тарифам и суммам пополнения.

Здесь настраивается:
  • PLANS — тарифы подписок (title, days, price в звёздах, is_trial)
  • STARS_TOPUP_AMOUNTS — доступные номиналы пополнения через Telegram Stars
  • PLATEGA_TOPUPS_DEFAULT — дефолтные пары (RUB, STARS) для Platega

Изменения здесь автоматически применяются к:
  • кнопкам «Купить подписку», «Продлить», «Перейти на платный» — keyboards.py, handlers.py
  • тексту меню покупки тарифов — handlers.py
  • сумме списания при покупке/продлении — handlers.py
  • кнопкам пополнения через Stars — keyboards.py
  • суммам пополнения через Platega — platega.py

Для Platega можно переопределить через .env:
  PLATEGA_TOPUPS=100:50,200:100,400:200,800:400

Для Stars можно переопределить через .env:
  STARS_TOPUP_AMOUNTS=25,50,100,200
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


# ==========================================================================
# Тарифы подписок
# ==========================================================================
# Ключ — внутренний идентификатор (латиница, без пробелов).
# Значение:
#   title    — что видно пользователю
#   days     — длительность в днях
#   price    — сколько ★ списывается (0 для триала)
#   is_trial — True, если это пробный тариф (можно активировать 1 раз)
#
# Порядок в словаре определяет порядок кнопок в меню «Купить подписку».
# ==========================================================================

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


# ==========================================================================
# Пополнение через Telegram Stars
# ==========================================================================
# Номиналы в звёздах. Здесь 1★ = 1★, без курса — пользователь платит
# ровно столько звёзд, сколько получит на баланс.
#
# Переопределяется через env: STARS_TOPUP_AMOUNTS=25,50,100,200
# ==========================================================================

_STARS_TOPUP_DEFAULT: List[int] = [25, 50, 100, 200]


def _parse_stars_topup_env() -> List[int]:
    raw = os.getenv("STARS_TOPUP_AMOUNTS", "").strip()
    if not raw:
        return list(_STARS_TOPUP_DEFAULT)

    result: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if value > 0:
            result.append(value)

    # fallback на дефолт, если после парсинга ничего не осталось
    return result or list(_STARS_TOPUP_DEFAULT)


STARS_TOPUP_AMOUNTS: List[int] = _parse_stars_topup_env()


# ==========================================================================
# Пополнение через Platega (карта / СБП)
# ==========================================================================
# Список пар (RUB, STARS):
#   100 ₽ → 50★
#   200 ₽ → 100★
#   ...
#
# Здесь задаются только ДЕФОЛТЫ. В platega.py читается env PLATEGA_TOPUPS,
# и если он задан — используется он, а не этот список.
# ==========================================================================

PLATEGA_TOPUPS_DEFAULT: List[Tuple[int, int]] = [
    (100, 50),
    (200, 100),
    (400, 200),
    (800, 400),
]


def platega_topups_default_string() -> str:
    """Строка для .env: '100:50,200:100,400:200,800:400'."""
    return ",".join(f"{rub}:{stars}" for rub, stars in PLATEGA_TOPUPS_DEFAULT)


# ==========================================================================
# Хелперы — чтобы не писать PLANS.get(...) в каждом месте
# ==========================================================================

def get_plan(key: str) -> Dict[str, Any] | None:
    return PLANS.get(key)


def get_plan_title(key: str) -> str:
    plan = PLANS.get(key)
    return plan["title"] if plan else key


def get_plan_price(key: str) -> int:
    plan = PLANS.get(key)
    return int(plan["price"]) if plan else 0


def get_plan_days(key: str) -> int:
    plan = PLANS.get(key)
    return int(plan["days"]) if plan else 0


def plan_price_label(key: str) -> str:
    """Форматирует '50★' или '0★ (бесплатно)' для текстов/кнопок."""
    plan = PLANS.get(key)
    if not plan:
        return "—"
    price = int(plan["price"])
    if price == 0:
        return "0★ (бесплатно)"
    return f"{price}★"
