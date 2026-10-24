"""Единый источник правды по ценам, тарифам и суммам пополнения.

Значения читаются из .env, если там заданы, иначе используются
дефолты из этого файла. Это позволяет менять цены без правки кода —
install.sh спрашивает их при установке и пишет в .env.

Переопределяемые переменные:
  PLAN_TRIAL_TITLE / PLAN_TRIAL_DAYS / PLAN_TRIAL_PRICE      (trial_3d)
  PLAN_WEEK_TITLE  / PLAN_WEEK_DAYS  / PLAN_WEEK_PRICE       (week_7d)
  PLAN_MONTH_TITLE / PLAN_MONTH_DAYS / PLAN_MONTH_PRICE      (month_30d)
  PLAN_2MONTH_TITLE/ PLAN_2MONTH_DAYS/ PLAN_2MONTH_PRICE     (month_60d)
  STARS_TOPUP_AMOUNTS
  PLATEGA_TOPUPS
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


# ==========================================================================
# Хелперы чтения из env с дефолтами
# ==========================================================================

def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name, "").strip()
    return raw or default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return int(default)
    return value


# ==========================================================================
# Тарифы подписок
# ==========================================================================
# Ключ — внутренний идентификатор (не меняется, чтобы не сломать БД и callback'и).
# title/days/price читаются из env, чтобы install.sh мог их переопределить.
# ==========================================================================

PLANS: Dict[str, Dict[str, Any]] = {
    "trial_3d": {
        "title": _env_str("PLAN_TRIAL_TITLE", "Пробный 3 дня"),
        "days": _env_int("PLAN_TRIAL_DAYS", 3),
        "price": _env_int("PLAN_TRIAL_PRICE", 0),
        "is_trial": True,
    },
    "week_7d": {
        "title": _env_str("PLAN_WEEK_TITLE", "7 дней"),
        "days": _env_int("PLAN_WEEK_DAYS", 7),
        "price": _env_int("PLAN_WEEK_PRICE", 25),
        "is_trial": False,
    },
    "month_30d": {
        "title": _env_str("PLAN_MONTH_TITLE", "30 дней"),
        "days": _env_int("PLAN_MONTH_DAYS", 30),
        "price": _env_int("PLAN_MONTH_PRICE", 50),
        "is_trial": False,
    },
    "month_60d": {
        "title": _env_str("PLAN_2MONTH_TITLE", "60 дней"),
        "days": _env_int("PLAN_2MONTH_DAYS", 60),
        "price": _env_int("PLAN_2MONTH_PRICE", 100),
        "is_trial": False,
    },
}


# ==========================================================================
# Пополнение через Telegram Stars
# ==========================================================================
# Номиналы в звёздах. 1★ = 1★, без курса.
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

    return result or list(_STARS_TOPUP_DEFAULT)


STARS_TOPUP_AMOUNTS: List[int] = _parse_stars_topup_env()


# ==========================================================================
# Пополнение через Platega (карта / СБП)
# ==========================================================================
# Список пар (RUB, STARS).
# Здесь заданы только ДЕФОЛТЫ. В platega.py читается env PLATEGA_TOPUPS,
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
# Хелперы для handlers.py
# ==========================================================================

def get_plan(key: str) -> Dict[str, Any] | None:
    return PLANS.get(key)


def get_plan_title(key: str) -> str:
    plan = PLANS.get(key)
    return str(plan["title"]) if plan else key


def get_plan_price(key: str) -> int:
    plan = PLANS.get(key)
    return int(plan["price"]) if plan else 0


def get_plan_days(key: str) -> int:
    plan = PLANS.get(key)
    return int(plan["days"]) if plan else 0


def plan_price_label(key: str) -> str:
    plan = PLANS.get(key)
    if not plan:
        return "—"
    price = int(plan["price"])
    if price == 0:
        return "0★ (бесплатно)"
    return f"{price}★"
