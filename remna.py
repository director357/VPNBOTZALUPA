"""Совместимый API-адаптер: старый интерфейс marzban.py поверх Remnawave 2.8.x.

Остальной бот продолжает вызывать create_user/get_user/modify_user/delete_user,
поэтому массовая переделка handlers.py не требуется.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

import aiohttp
from aiohttp import ClientTimeout, TCPConnector
from dotenv import load_dotenv

logger = logging.getLogger("remnawave")

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

REMNAWAVE_URL = os.getenv("REMNAWAVE_URL", "").rstrip("/")
REMNAWAVE_API_TOKEN = os.getenv("REMNAWAVE_API_TOKEN", "").strip()
REMNAWAVE_INTERNAL_SQUAD_UUID = os.getenv(
    "REMNAWAVE_INTERNAL_SQUAD_UUID", ""
).strip()
VERIFY_SSL = os.getenv("REMNAWAVE_SSL_VERIFY", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

SUBSCRIPTION_BASE_URL = (
    os.getenv("REMNAWAVE_SUBSCRIPTION_BASE_URL", "").rstrip("/")
    or os.getenv("SUBSCRIPTION_BASE_URL", "").rstrip("/")
)

_TIMEOUT_TOTAL = float(os.getenv("REMNAWAVE_TIMEOUT_TOTAL", "20") or 20)
_TIMEOUT_CONNECT = float(os.getenv("REMNAWAVE_TIMEOUT_CONNECT", "7") or 7)
_TIMEOUT_READ = float(os.getenv("REMNAWAVE_TIMEOUT_READ", "15") or 15)
_TIMEOUT = ClientTimeout(
    total=_TIMEOUT_TOTAL,
    connect=_TIMEOUT_CONNECT,
    sock_read=_TIMEOUT_READ,
)

_KEEPALIVE_TIMEOUT_TOTAL = float(
    os.getenv("REMNAWAVE_KEEPALIVE_TIMEOUT_TOTAL", "15") or 15
)
_KEEPALIVE_TIMEOUT_READ = float(
    os.getenv("REMNAWAVE_KEEPALIVE_TIMEOUT_READ", "12") or 12
)
_KEEPALIVE_TIMEOUT = ClientTimeout(
    total=_KEEPALIVE_TIMEOUT_TOTAL,
    connect=_TIMEOUT_CONNECT,
    sock_read=_KEEPALIVE_TIMEOUT_READ,
)
_KEEPALIVE_RETRIES = max(
    1,
    int(os.getenv("REMNAWAVE_KEEPALIVE_RETRIES", "2") or 2),
)
_KEEPALIVE_RETRY_DELAY = max(
    0.0,
    float(os.getenv("REMNAWAVE_KEEPALIVE_RETRY_DELAY", "1.5") or 1.5),
)
_CREATE_TIMEOUT_TOTAL = float(
    os.getenv("REMNAWAVE_CREATE_TIMEOUT_TOTAL", "75") or 75
)
_CREATE_TIMEOUT_READ = float(
    os.getenv("REMNAWAVE_CREATE_TIMEOUT_READ", "60") or 60
)
_CREATE_TIMEOUT = ClientTimeout(
    total=_CREATE_TIMEOUT_TOTAL,
    connect=_TIMEOUT_CONNECT,
    sock_read=_CREATE_TIMEOUT_READ,
)
_CREATE_RECONCILE_ATTEMPTS = int(
    os.getenv("REMNAWAVE_CREATE_RECONCILE_ATTEMPTS", "10") or 10
)
_CREATE_RECONCILE_DELAY = float(
    os.getenv("REMNAWAVE_CREATE_RECONCILE_DELAY", "1.5") or 1.5
)
_GET_TIMEOUT_TOTAL = float(
    os.getenv("REMNAWAVE_GET_TIMEOUT_TOTAL", "45") or 45
)
_GET_TIMEOUT_READ = float(
    os.getenv("REMNAWAVE_GET_TIMEOUT_READ", "40") or 40
)
_GET_TIMEOUT = ClientTimeout(
    total=_GET_TIMEOUT_TOTAL,
    connect=_TIMEOUT_CONNECT,
    sock_read=_GET_TIMEOUT_READ,
)
_GET_RETRIES = int(os.getenv("REMNAWAVE_GET_RETRIES", "3") or 3)
_GET_RETRY_DELAY = float(
    os.getenv("REMNAWAVE_GET_RETRY_DELAY", "1.0") or 1.0
)

_NODES_CACHE_TTL = int(os.getenv("REMNAWAVE_NODES_CACHE_TTL", "300") or 300)
_NODES_STALE_TTL = int(
    os.getenv("REMNAWAVE_NODES_STALE_TTL", "86400") or 86400
)
_NODES_TIMEOUT_TOTAL = float(
    os.getenv("REMNAWAVE_NODES_TIMEOUT_TOTAL", "60") or 60
)
_NODES_TIMEOUT_READ = float(
    os.getenv("REMNAWAVE_NODES_TIMEOUT_READ", "50") or 50
)
_NODES_TIMEOUT = ClientTimeout(
    total=_NODES_TIMEOUT_TOTAL,
    connect=_TIMEOUT_CONNECT,
    sock_read=_NODES_TIMEOUT_READ,
)
_NODES_RETRIES = int(os.getenv("REMNAWAVE_NODES_RETRIES", "2") or 2)
_NODES_RETRY_DELAY = float(
    os.getenv("REMNAWAVE_NODES_RETRY_DELAY", "1.5") or 1.5
)
_NODES_CACHE: Optional[tuple[float, List[Dict[str, Any]]]] = None
_NODES_LOCK = asyncio.Lock()
_SESSION: Optional[aiohttp.ClientSession] = None


class RemnawaveAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _validate_config() -> None:
    missing = []
    if not REMNAWAVE_URL:
        missing.append("REMNAWAVE_URL")
    if not REMNAWAVE_API_TOKEN:
        missing.append("REMNAWAVE_API_TOKEN")
    if missing:
        raise RuntimeError("Не заданы переменные: " + ", ".join(missing))


def _now() -> float:
    return time.time()


def _sanitize_username(username: str) -> str:
    """Нормализация, совместимая с legacy-миграцией коротких имён.

    Remnawave legacy page дополняет username короче 6 символов символами `_`.
    Для обычных bot-generated имён функция ничего не меняет.
    """
    value = (username or "").strip()
    value = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if len(value) < 6:
        value += "_" * (6 - len(value))
    return value


def _epoch_to_iso(value: int) -> str:
    return (
        datetime.fromtimestamp(int(value), tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _iso_to_epoch(value: Any) -> int:
    if not value or not isinstance(value, str):
        return 0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return 0


def _normalize_subscription_url(url: Any, short_uuid: str = "") -> Any:
    if isinstance(url, str) and url.strip():
        value = url.strip()
        if value.startswith(("http://", "https://")):
            return value
        if SUBSCRIPTION_BASE_URL:
            return f"{SUBSCRIPTION_BASE_URL}/{value.lstrip('/')}"
        return value

    if short_uuid and SUBSCRIPTION_BASE_URL:
        return f"{SUBSCRIPTION_BASE_URL}/{short_uuid}"
    return url


def _unwrap(data: Any) -> Any:
    if isinstance(data, dict) and "response" in data:
        return data["response"]
    return data


def _normalize_user_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Преобразует Remnawave UserResponse в формат, ожидаемый старым ботом."""
    data = dict(raw or {})
    short_uuid = str(data.get("shortUuid") or "")
    subscription_url = _normalize_subscription_url(
        data.get("subscriptionUrl"), short_uuid
    )

    status_map = {
        "ACTIVE": "active",
        "DISABLED": "disabled",
        "LIMITED": "disabled",
        "EXPIRED": "disabled",
    }
    status = status_map.get(str(data.get("status") or "").upper(), "disabled")

    proxies: Dict[str, Dict[str, str]] = {}
    if data.get("vlessUuid"):
        proxies["vless"] = {"id": str(data["vlessUuid"])}
    if data.get("trojanPassword"):
        proxies["trojan"] = {"password": str(data["trojanPassword"])}
    if data.get("ssPassword"):
        proxies["shadowsocks"] = {"password": str(data["ssPassword"])}

    result = dict(data)
    result.update(
        {
            "username": data.get("username"),
            "status": status,
            "expire": _iso_to_epoch(data.get("expireAt")),
            "subscription_url": subscription_url,
            "links": [subscription_url] if subscription_url else [],
            "proxies": proxies,
            "data_limit": int(float(data.get("trafficLimitBytes") or 0)),
            "data_limit_reset_strategy": str(
                data.get("trafficLimitStrategy") or "NO_RESET"
            ).lower(),
            "note": data.get("description") or "",
        }
    )
    return result


async def _get_session() -> aiohttp.ClientSession:
    global _SESSION
    _validate_config()
    if _SESSION is None or _SESSION.closed:
        connector = TCPConnector(
            limit=50,
            ttl_dns_cache=300,
            keepalive_timeout=180,
            ssl=VERIFY_SSL,
        )
        _SESSION = aiohttp.ClientSession(timeout=_TIMEOUT, connector=connector)
    return _SESSION


async def close() -> None:
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close()
    _SESSION = None


def _headers() -> Dict[str, str]:
    _validate_config()
    return {
        "Authorization": f"Bearer {REMNAWAVE_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _request(
    method: str,
    path: str,
    *,
    json_payload: Optional[Dict[str, Any]] = None,
    allow_404: bool = False,
    accepted: tuple[int, ...] = (200, 201, 204),
    timeout: Optional[ClientTimeout] = None,
) -> Any:
    session = await _get_session()
    url = f"{REMNAWAVE_URL}{path}"

    try:
        async with session.request(
            method,
            url,
            headers=_headers(),
            json=json_payload,
            ssl=VERIFY_SSL,
            timeout=timeout or _TIMEOUT,
        ) as response:
            text = await response.text()
            if allow_404 and response.status == 404:
                return None
            if response.status not in accepted:
                # Низкоуровневый адаптер фиксирует детали как WARNING.
                # Решение об аварийном ERROR принимает вызывающий обработчик,
                # иначе одно HTTP-событие приходит администратору дважды.
                logger.warning(
                    "Remnawave request failed: %s %s -> HTTP %s, body=%s, payload=%s",
                    method,
                    path,
                    response.status,
                    text[:1000],
                    json_payload,
                )
                detail = text[:500] or response.reason
                raise RemnawaveAPIError(
                    f"Remnawave request failed: HTTP {response.status}: {detail}",
                    status=response.status,
                    body=text,
                )
            if response.status == 204 or not text.strip():
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise RemnawaveAPIError(
                    "Remnawave вернул не-JSON ответ",
                    status=response.status,
                    body=text,
                ) from exc
    except asyncio.TimeoutError as exc:
        raise RemnawaveAPIError(f"Таймаут запроса Remnawave: {method} {path}") from exc
    except aiohttp.ClientError as exc:
        raise RemnawaveAPIError(f"Ошибка соединения с Remnawave: {exc}") from exc


async def _get_user_raw(
    username: str,
    *,
    attempts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Получает пользователя с повтором временных сетевых ошибок.

    404 означает, что кандидата действительно нет. Таймаут/обрыв соединения
    не превращаем в ложный 404: повторяем запрос и после исчерпания попыток
    пробрасываем ошибку вызывающему коду.
    """
    candidates = [username]
    sanitized = _sanitize_username(username)
    if sanitized != username:
        candidates.append(sanitized)

    retry_count = max(1, attempts if attempts is not None else _GET_RETRIES)
    last_error: Optional[RemnawaveAPIError] = None

    for candidate in candidates:
        for attempt in range(retry_count):
            if attempt:
                await asyncio.sleep(_GET_RETRY_DELAY)
            try:
                data = await _request(
                    "GET",
                    f"/api/users/by-username/{quote(candidate, safe='')}",
                    allow_404=True,
                    timeout=_GET_TIMEOUT,
                )
            except RemnawaveAPIError as exc:
                if exc.status != 0:
                    raise
                last_error = exc
                logger.warning(
                    "Временная ошибка GET пользователя %s, попытка %s/%s: %s",
                    candidate,
                    attempt + 1,
                    retry_count,
                    exc,
                )
                continue

            # Успешный HTTP 404: этого кандидата нет, переходим к следующему.
            if not data:
                break

            user = _unwrap(data)
            if isinstance(user, dict):
                return user

    if last_error is not None:
        raise last_error
    return None


def _is_recoverable_created_user(raw: Dict[str, Any], note: str) -> bool:
    """Проверяет, что найденная учётка относится к этой операции создания.

    Username в боте детерминированный, а description содержит tg_id и тариф.
    Это позволяет безопасно восстановиться после ситуации, когда POST уже
    создал пользователя, но HTTP-ответ потерялся или пришёл после таймаута.
    """
    if not isinstance(raw, dict):
        return False
    if not note:
        return True
    return str(raw.get("description") or "") == note


async def _recover_created_user(
    username: str,
    note: str,
    *,
    attempts: int = _CREATE_RECONCILE_ATTEMPTS,
) -> Optional[Dict[str, Any]]:
    for attempt in range(max(1, attempts)):
        if attempt:
            await asyncio.sleep(_CREATE_RECONCILE_DELAY)
        try:
            raw = await _get_user_raw(username)
        except RemnawaveAPIError:
            logger.warning(
                "Не удалось проверить результат создания %s, попытка %s/%s",
                username,
                attempt + 1,
                attempts,
                exc_info=True,
            )
            continue
        if raw and _is_recoverable_created_user(raw, note):
            logger.warning(
                "POST /api/users не вернул надёжный ответ, но пользователь %s "
                "уже существует; продолжаю как после успешного создания",
                username,
            )
            return raw
    return None


async def create_user(
    tg_id: Optional[int] = None,
    plan_key: Optional[str] = None,
    expire_ts: Optional[int] = None,
    *,
    username: Optional[str] = None,
    note: str = "",
    traffic_limit_bytes: int = 0,
) -> Dict[str, Any]:
    if expire_ts is None:
        raise ValueError("expire_ts is required")

    final_username = username
    if not final_username:
        if tg_id is None or plan_key is None:
            raise ValueError("Need either username or (tg_id, plan_key)")
        final_username = f"tg_{plan_key}_{tg_id}_{uuid4().hex[:6]}".lower()

    final_username = _sanitize_username(final_username)
    payload: Dict[str, Any] = {
        "username": final_username,
        "status": "ACTIVE",
        "trafficLimitBytes": max(0, int(traffic_limit_bytes)),
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": _epoch_to_iso(int(expire_ts)),
        "description": note or "",
    }
    if REMNAWAVE_INTERNAL_SQUAD_UUID:
        payload["activeInternalSquads"] = [REMNAWAVE_INTERNAL_SQUAD_UUID]

    # После неоднозначного таймаута предыдущая попытка могла уже создать
    # пользователя, а локальная SQLite-запись ещё не появилась. Подхватываем
    # такую учётку и не создаём дубль с новым индексом.
    try:
        existing = await _get_user_raw(final_username)
    except RemnawaveAPIError as exc:
        if exc.status != 0:
            raise
        # Предварительный GET не является изменяющей операцией. Если он
        # временно недоступен, безопасно продолжаем к POST: username уникален,
        # а возможный 409 ниже будет сверён и восстановлен без дублей.
        logger.warning(
            "Предварительная проверка пользователя %s не ответила; "
            "продолжаю идемпотентное создание",
            final_username,
        )
        existing = None

    if existing:
        if _is_recoverable_created_user(existing, note):
            logger.warning(
                "Пользователь %s уже создан предыдущей попыткой; "
                "восстанавливаю операцию",
                final_username,
            )
            return _normalize_user_payload(existing)
        raise RemnawaveAPIError(
            f"User already exists: {final_username}",
            status=409,
        )

    try:
        data = await _request(
            "POST",
            "/api/users",
            json_payload=payload,
            timeout=_CREATE_TIMEOUT,
        )
    except RemnawaveAPIError as exc:
        # status=0 означает timeout/обрыв связи: сервер мог применить POST.
        # При 409 тоже проверяем, не является ли это результатом прошлой
        # неоднозначной попытки.
        if exc.status in {0, 409}:
            recovered = await _recover_created_user(final_username, note)
            if recovered:
                return _normalize_user_payload(recovered)
        raise

    raw = _unwrap(data)
    if not isinstance(raw, dict):
        recovered = await _recover_created_user(final_username, note)
        if recovered:
            return _normalize_user_payload(recovered)
        raise RemnawaveAPIError("Remnawave не вернул созданного пользователя")
    return _normalize_user_payload(raw)


async def get_user(username: str) -> Optional[Dict[str, Any]]:
    raw = await _get_user_raw(username)
    if not raw:
        return None
    return _normalize_user_payload(raw)


async def modify_user(
    username: str,
    *,
    expire_ts: Optional[int] = None,
    status: Optional[str] = None,
    traffic_limit_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    raw = await _get_user_raw(username)
    if not raw:
        raise RemnawaveAPIError(f"Пользователь {username!r} не найден", status=404)

    user_uuid = str(raw.get("uuid") or "")
    if not user_uuid:
        raise RemnawaveAPIError("В ответе Remnawave отсутствует uuid пользователя")

    patch_payload: Dict[str, Any] = {
        "uuid": user_uuid,
    }

    if expire_ts is not None:
        patch_payload["expireAt"] = _epoch_to_iso(int(expire_ts))

    if traffic_limit_bytes is not None:
        patch_payload["trafficLimitBytes"] = max(
            0,
            int(traffic_limit_bytes),
        )
        patch_payload["trafficLimitStrategy"] = "NO_RESET"

    if len(patch_payload) > 1:
        await _request(
            "PATCH",
            "/api/users",
            json_payload=patch_payload,
        )

    if status is not None:
        normalized = status.strip().lower()
        if normalized in {"active", "enabled"}:
            await _request(
                "POST",
                f"/api/users/{quote(user_uuid, safe='')}/actions/enable",
            )
        elif normalized in {"disabled", "on_hold", "limited", "expired"}:
            await _request(
                "POST",
                f"/api/users/{quote(user_uuid, safe='')}/actions/disable",
            )
        else:
            raise ValueError(f"Unsupported user status: {status}")

    updated = await _get_user_raw(str(raw.get("username") or username))
    final_raw = updated or raw

    if traffic_limit_bytes is not None:
        expected_limit = max(0, int(traffic_limit_bytes))

        try:
            actual_limit = int(
                final_raw.get("trafficLimitBytes") or 0
            )
        except (TypeError, ValueError):
            actual_limit = -1

        if actual_limit != expected_limit:
            raise RemnawaveAPIError(
                "Remnawave не применил лимит трафика: "
                f"ожидалось {expected_limit}, "
                f"получено {actual_limit}"
            )

    return _normalize_user_payload(final_raw)


async def delete_user(username: str) -> None:
    raw = await _get_user_raw(username)
    if not raw:
        return
    user_uuid = str(raw.get("uuid") or "")
    if not user_uuid:
        return
    await _request(
        "DELETE",
        f"/api/users/{quote(user_uuid, safe='')}",
        allow_404=True,
    )


async def revoke_user_subscription(username: str) -> Dict[str, Any]:
    raw = await _get_user_raw(username)
    if not raw:
        raise RemnawaveAPIError(f"Пользователь {username!r} не найден", status=404)
    user_uuid = str(raw.get("uuid") or "")
    data = await _request(
        "POST",
        f"/api/users/{quote(user_uuid, safe='')}/actions/revoke",
    )
    updated = _unwrap(data)
    if not isinstance(updated, dict):
        updated = await _get_user_raw(str(raw.get("username") or username)) or raw
    return _normalize_user_payload(updated)


def _extract_node_list(data: Any) -> List[Dict[str, Any]]:
    value = _unwrap(data)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("nodes", "items", "data"):
            items = value.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def _node_to_legacy(node: Dict[str, Any]) -> Dict[str, Any]:
    name = node.get("name") or node.get("nodeName") or node.get("node_name")
    node_id = node.get("id") or node.get("uuid") or node.get("nodeUuid")

    total = 0
    for key in (
        "trafficUsedBytes",
        "usedTrafficBytes",
        "trafficBytes",
        "totalTrafficBytes",
        "traffic",
    ):
        value = node.get(key)
        if isinstance(value, (int, float)):
            total = int(value)
            break

    user_traffic = node.get("userTraffic")
    if total == 0 and isinstance(user_traffic, dict):
        total = int(user_traffic.get("usedTrafficBytes") or 0)

    return {
        "node_name": str(name or f"node_{node_id}"),
        "node_id": node_id,
        "uplink": 0,
        "downlink": total,
        "status": node.get("isConnected", node.get("status")),
        "users_online": int(node.get("usersOnline") or 0),
    }


def _merge_nodes_with_stale_traffic(
    current: List[Dict[str, Any]],
    stale: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Сохраняет последнее известное значение трафика при fallback на /api/nodes."""
    stale_by_id = {
        str(item.get("node_id")): item
        for item in stale
        if item.get("node_id") is not None
    }
    stale_by_name = {
        str(item.get("node_name")): item
        for item in stale
        if item.get("node_name")
    }

    merged: List[Dict[str, Any]] = []
    for item in current:
        value = dict(item)
        old = stale_by_id.get(str(value.get("node_id")))
        if old is None:
            old = stale_by_name.get(str(value.get("node_name")))
        if old and int(value.get("uplink", 0) or 0) == 0 and int(
            value.get("downlink", 0) or 0
        ) == 0:
            value["uplink"] = int(old.get("uplink", 0) or 0)
            value["downlink"] = int(old.get("downlink", 0) or 0)
        merged.append(value)
    return merged


async def _get_nodes_stats_with_retries() -> List[Dict[str, Any]]:
    last_error: Optional[RemnawaveAPIError] = None
    for attempt in range(max(1, _NODES_RETRIES)):
        if attempt:
            await asyncio.sleep(_NODES_RETRY_DELAY)
        try:
            data = await _request(
                "GET",
                "/api/system/stats/nodes",
                timeout=_NODES_TIMEOUT,
            )
            nodes = _extract_node_list(data)
            if nodes:
                return nodes
        except RemnawaveAPIError as exc:
            last_error = exc
            if exc.status != 0:
                break

    if last_error is not None:
        raise last_error
    return []


async def get_nodes_usage() -> List[Dict[str, Any]]:
    global _NODES_CACHE
    now = _now()
    if _NODES_CACHE_TTL > 0 and _NODES_CACHE:
        cached_at, cached = _NODES_CACHE
        if now - cached_at < _NODES_CACHE_TTL:
            return cached

    async with _NODES_LOCK:
        now = _now()
        if _NODES_CACHE_TTL > 0 and _NODES_CACHE:
            cached_at, cached = _NODES_CACHE
            if now - cached_at < _NODES_CACHE_TTL:
                return cached

        stale: List[Dict[str, Any]] = []
        stale_age = 0.0
        if _NODES_CACHE:
            cached_at, stale = _NODES_CACHE
            stale_age = max(0.0, now - cached_at)

        try:
            nodes = await _get_nodes_stats_with_retries()
            result = [_node_to_legacy(node) for node in nodes]
            if result:
                _NODES_CACHE = (_now(), result)
                return result
        except RemnawaveAPIError as exc:
            logger.warning(
                "Статистика нод временно не ответила (%s); использую fallback",
                exc,
            )

        # /api/nodes обычно отвечает быстрее и позволяет хотя бы показать
        # список/статус нод. Если в нём нет traffic-полей, добавляем последнее
        # успешное значение трафика из кэша.
        try:
            data = await _request(
                "GET",
                "/api/nodes",
                timeout=_GET_TIMEOUT,
            )
            fallback = [_node_to_legacy(node) for node in _extract_node_list(data)]
            if fallback:
                if stale:
                    fallback = _merge_nodes_with_stale_traffic(fallback, stale)
                _NODES_CACHE = (_now(), fallback)
                return fallback
        except RemnawaveAPIError as exc:
            logger.warning("Fallback /api/nodes тоже временно недоступен: %s", exc)

        # Не ломаем пользовательский экран из-за краткого сбоя статистики.
        # Возвращаем даже просроченный кэш в пределах STALE_TTL.
        if stale and (_NODES_STALE_TTL <= 0 or stale_age < _NODES_STALE_TTL):
            logger.warning(
                "Возвращаю последнюю успешную статистику нод возрастом %.0f сек.",
                stale_age,
            )
            return stale

        return []


async def keepalive() -> None:
    """Проверяет health endpoint с повторами временных сетевых ошибок."""
    await _get_session()
    last_error: Optional[RemnawaveAPIError] = None

    for attempt in range(1, _KEEPALIVE_RETRIES + 1):
        try:
            await _request(
                "GET",
                "/api/system/health",
                timeout=_KEEPALIVE_TIMEOUT,
            )
            return

        except RemnawaveAPIError as exc:
            last_error = exc

            # Ошибки авторизации/валидации повтором не исправить.
            retryable = (
                exc.status == 0
                or exc.status == 429
                or exc.status >= 500
            )
            if not retryable or attempt >= _KEEPALIVE_RETRIES:
                raise

            await asyncio.sleep(
                _KEEPALIVE_RETRY_DELAY * attempt
            )

    if last_error is not None:
        raise last_error
