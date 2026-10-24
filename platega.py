from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import aiohttp
from aiohttp import web

import db

logger = logging.getLogger("vpn_bot.platega")


@dataclass(frozen=True)
class TopupOption:
    rub_amount: int
    credit_amount: int


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def is_enabled() -> bool:
    return bool(_env("PLATEGA_MERCHANT_ID") and _env("PLATEGA_SECRET"))


def _api_base() -> str:
    return _env("PLATEGA_API_BASE", "https://app.platega.io").rstrip("/")


def _headers() -> dict[str, str]:
    merchant_id = _env("PLATEGA_MERCHANT_ID")
    secret = _env("PLATEGA_SECRET")
    if not merchant_id or not secret:
        raise RuntimeError("PLATEGA_MERCHANT_ID/PLATEGA_SECRET не заданы")
    return {
        "X-MerchantId": merchant_id,
        "X-Secret": secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def get_topups() -> list[TopupOption]:
    """
    Формат PLATEGA_TOPUPS: RUB:CREDIT,RUB:CREDIT
    Пример: 100:50,200:100,400:200,800:400
    """
    raw = _env("PLATEGA_TOPUPS", "100:50,200:100,400:200,800:400")
    result: list[TopupOption] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            rub_s, credit_s = part.split(":", 1)
            rub = int(rub_s)
            credit = int(credit_s)
        except (TypeError, ValueError):
            logger.warning("Некорректный элемент PLATEGA_TOPUPS: %r", part)
            continue
        if rub > 0 and credit > 0:
            result.append(TopupOption(rub_amount=rub, credit_amount=credit))
    if not result:
        raise RuntimeError("PLATEGA_TOPUPS не содержит корректных сумм")
    return result


def rub_to_kopecks(value: Any) -> int:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Некорректная сумма RUB: {value!r}") from exc
    return int(amount * 100)


async def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=25)
    url = f"{_api_base()}{path}"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, headers=_headers(), json=json_body) as response:
            text = await response.text()
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Platega HTTP {response.status}: {text[:500]}")
            if not text.strip():
                return {}
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Platega вернула не JSON: {text[:500]}") from exc
            if not isinstance(data, dict):
                raise RuntimeError("Platega вернула неожиданный формат ответа")
            return data


async def create_payment(
    *,
    telegram_id: int,
    username: str,
    rub_amount: int,
    credit_amount: int,
) -> dict[str, Any]:
    return_url = _env("PLATEGA_RETURN_URL", "https://t.me/")
    failed_url = _env("PLATEGA_FAILED_URL", return_url)
    payload = f"topup|{int(telegram_id)}|{int(credit_amount)}"

    brand = str(os.getenv("BRAND_NAME", "VPN") or "").strip() or "VPN"

    body = {
        "paymentDetails": {
            "amount": int(rub_amount),
            "currency": "RUB",
        },
        "description": f"Пополнение {brand} на {int(credit_amount)}★",
        "return": return_url,
        "failedUrl": failed_url,
        "payload": payload,
        "metadata": {
            "userId": str(int(telegram_id)),
            "userName": str(username or ""),
        },
    }
    return await _request("POST", "/v2/transaction/process", json_body=body)


async def get_payment(transaction_id: str) -> dict[str, Any]:
    tx_id = str(transaction_id or "").strip()
    if not tx_id:
        raise ValueError("transaction_id пуст")
    return await _request("GET", f"/transaction/{tx_id}")


def _callback_authorized(request: web.Request) -> bool:
    merchant = request.headers.get("X-MerchantId", "")
    secret = request.headers.get("X-Secret", "")
    return (
        bool(merchant)
        and bool(secret)
        and merchant == _env("PLATEGA_MERCHANT_ID")
        and secret == _env("PLATEGA_SECRET")
    )


async def _notify_credit(bot: Any, telegram_id: int, credited_now: bool, new_balance: int, credit_amount: int) -> None:
    if not credited_now:
        return
    try:
        await bot.send_message(
            int(telegram_id),
            "✅ <b>Оплата подтверждена</b>\n\n"
            f"Начислено: <b>{int(credit_amount)}★</b>\n"
            f"Текущий баланс: <b>{int(new_balance)}★</b>.",
        )
    except Exception:
        logger.exception("Не удалось уведомить пользователя о Platega-платеже")


async def callback_handler(request: web.Request) -> web.Response:
    if not _callback_authorized(request):
        logger.warning("Отклонён callback Platega с неверными заголовками")
        return web.Response(status=401, text="unauthorized")

    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="invalid json")

    if not isinstance(body, dict):
        return web.Response(status=400, text="invalid body")

    transaction_id = str(body.get("id") or body.get("Id") or "").strip()
    status = str(body.get("status") or body.get("Status") or "").strip().upper()
    currency = str(body.get("currency") or body.get("Currency") or "").strip().upper()
    amount = body.get("amount", body.get("Amount"))

    if not transaction_id or not status:
        return web.Response(status=400, text="missing fields")

    try:
        amount_kopecks = rub_to_kopecks(amount) if amount is not None else None
        result = db.process_platega_status(
            transaction_id=transaction_id,
            status=status,
            callback_amount_kopecks=amount_kopecks,
            currency=currency or "RUB",
        )
    except KeyError:
        logger.error("Callback Platega для неизвестной транзакции: %s", transaction_id)
        return web.Response(status=404, text="transaction not found")
    except Exception:
        logger.exception("Ошибка обработки callback Platega: %s", transaction_id)
        return web.Response(status=500, text="processing error")

    if result and status == "CONFIRMED":
        await _notify_credit(
            request.app["bot"],
            int(result["telegram_id"]),
            bool(result["credited_now"]),
            int(result["new_balance"]),
            int(result["credit_amount"]),
        )

    return web.Response(status=200, text="ok")


async def health_handler(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "platega": is_enabled()})


async def start_callback_server(bot: Any) -> web.AppRunner | None:
    if not is_enabled():
        logger.warning("Platega выключена: не заданы MerchantId/Secret")
        return None

    host = _env("PLATEGA_CALLBACK_HOST", "127.0.0.1")
    port = int(_env("PLATEGA_CALLBACK_PORT", "8088"))

    app = web.Application(client_max_size=128 * 1024)
    app["bot"] = bot
    app.router.add_post("/platega/callback", callback_handler)
    app.router.add_get("/platega/health", health_handler)

    runner = web.AppRunner(app, access_log=logger)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("Platega callback server: http://%s:%s", host, port)
    return runner
