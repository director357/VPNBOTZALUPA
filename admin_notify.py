import asyncio
import hashlib
import html
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp


logger = logging.getLogger("admin_notify")

MSK = ZoneInfo("Europe/Moscow")

BOT_TOKEN_RE = re.compile(
    r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"
)

BEARER_RE = re.compile(
    r"(?i)(authorization:\s*bearer\s+)[^\s]+"
)


LOG_PREFIX_RE = re.compile(
    r"(?m)^\d{4}-\d{2}-\d{2} "
    r"\d{2}:\d{2}:\d{2},\d{3} "
    r"\[(?:ERROR|CRITICAL)\]\s+"
)


def redact_secrets(value: str) -> str:
    text = BOT_TOKEN_RE.sub("<BOT_TOKEN>", str(value))
    text = BEARER_RE.sub(r"\1<TOKEN>", text)
    return text


def error_fingerprint(value: str) -> str:
    """Стабильный отпечаток ошибки без меняющегося времени лога."""
    normalized = LOG_PREFIX_RE.sub("", str(value)).strip()
    return hashlib.sha256(
        normalized.encode("utf-8", errors="replace")
    ).hexdigest()


def format_datetime(timestamp: int) -> str:
    try:
        value = datetime.fromtimestamp(
            int(timestamp),
            tz=MSK,
        )
        return value.strftime("%d.%m.%Y %H:%M:%S MSK")
    except Exception:
        return str(timestamp)


class AdminNotifier:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

        self.token = os.getenv(
            "ADMIN_NOTIFY_BOT_TOKEN",
            "",
        ).strip()

        self.chat_id = os.getenv(
            "ADMIN_NOTIFY_CHAT_ID",
            "",
        ).strip()

        self.poll_sec = max(
            1.0,
            float(
                os.getenv(
                    "ADMIN_NOTIFY_POLL_SEC",
                    "3",
                )
            ),
        )

        self.error_dedup_sec = max(
            30.0,
            float(
                os.getenv(
                    "ADMIN_NOTIFY_ERROR_DEDUP_SEC",
                    "300",
                )
            ),
        )

        self.session: Optional[aiohttp.ClientSession] = None

        self.error_queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=200
        )

        self.recent_errors: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def enqueue_error(self, text: str) -> None:
        try:
            self.error_queue.put_nowait(
                redact_secrets(text)[:3500]
            )
        except asyncio.QueueFull:
            logger.warning(
                "Очередь уведомлений об ошибках заполнена"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _init_state(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_notify_state (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                )
                """
            )

            for key, table in (
                ("transaction_id", "transactions"),
                ("subscription_id", "subscriptions"),
            ):
                exists = conn.execute(
                    """
                    SELECT value
                    FROM admin_notify_state
                    WHERE key = ?
                    """,
                    (key,),
                ).fetchone()

                if exists:
                    continue

                maximum = conn.execute(
                    f"""
                    SELECT COALESCE(MAX(id), 0) AS value
                    FROM {table}
                    """
                ).fetchone()

                conn.execute(
                    """
                    INSERT INTO admin_notify_state (
                        key,
                        value
                    )
                    VALUES (?, ?)
                    """,
                    (
                        key,
                        int(maximum["value"] or 0),
                    ),
                )

            conn.commit()

    def _state(self, key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value
                FROM admin_notify_state
                WHERE key = ?
                """,
                (key,),
            ).fetchone()

            return int(row["value"]) if row else 0

    def _set_state(self, key: str, value: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_notify_state (
                    key,
                    value
                )
                VALUES (?, ?)
                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value
                """,
                (key, int(value)),
            )
            conn.commit()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=12
                )
            )

        return self.session

    async def send_text(self, text: str) -> bool:
        if not self.enabled:
            return False

        session = await self._ensure_session()

        url = (
            "https://api.telegram.org/bot"
            f"{self.token}/sendMessage"
        )

        payload = {
            "chat_id": self.chat_id,
            "text": redact_secrets(text)[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }

        try:
            async with session.post(
                url,
                data=payload,
            ) as response:
                result = await response.json(
                    content_type=None
                )

                if (
                    response.status != 200
                    or not result.get("ok")
                ):
                    logger.warning(
                        "Telegram уведомление не отправлено: "
                        "status=%s description=%s",
                        response.status,
                        result.get("description"),
                    )
                    return False

                return True

        except Exception as exc:
            logger.warning(
                "Ошибка отправки уведомления: %s",
                exc,
            )
            return False

    @staticmethod
    def _transaction_title(
        tx_type: str,
        amount: int,
        description: str,
    ) -> str:
        kind = tx_type.lower()
        desc = description.lower()

        if (
            "refund" in kind
            or "возврат" in desc
        ):
            return "↩️ Возврат Stars"

        if "конвертация триала" in desc:
            return "💳 Триал → платный тариф"

        if "автопрод" in desc:
            return "♻️ Автопродление"

        if kind in {
            "topup",
            "deposit",
            "user_topup",
            "refill",
            "admin_topup",
        }:
            return "⭐ Пополнение баланса"

        if kind == "ref_bonus":
            return "🎁 Реферальный бонус"

        if amount < 0 and kind == "extend":
            return "🔄 Продление подписки"

        if amount < 0 and kind in {
            "buy",
            "purchase",
            "subscription",
            "subscribe",
        }:
            return "🛒 Покупка подписки"

        if amount > 0:
            return "➕ Начисление Stars"

        if amount < 0:
            return "➖ Списание Stars"

        return "📋 Операция с балансом"

    async def _process_transactions(self) -> None:
        current_id = self._state("transaction_id")

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.id,
                    t.amount,
                    t.type,
                    t.description,
                    t.created_at,
                    u.telegram_id,
                    u.balance
                FROM transactions t
                JOIN users u
                  ON u.id = t.user_id
                WHERE t.id > ?
                ORDER BY t.id ASC
                LIMIT 100
                """,
                (current_id,),
            ).fetchall()

        for row in rows:
            amount = int(row["amount"])
            tx_type = str(row["type"] or "")
            description = str(
                row["description"] or ""
            )

            title = self._transaction_title(
                tx_type,
                amount,
                description,
            )

            signed_amount = (
                f"+{amount}"
                if amount > 0
                else str(amount)
            )

            message = (
                f"<b>{html.escape(title)}</b>\n\n"
                f"TG ID: "
                f"<code>{int(row['telegram_id'])}</code>\n"
                f"Сумма: <b>{signed_amount}★</b>\n"
                f"Баланс: <b>{int(row['balance'])}★</b>\n"
                f"Тип: <code>{html.escape(tx_type)}</code>\n"
                f"Описание: "
                f"{html.escape(description or '—')}\n"
                f"Время: "
                f"{format_datetime(row['created_at'])}"
            )

            sent = await self.send_text(message)

            if not sent:
                return

            self._set_state(
                "transaction_id",
                int(row["id"]),
            )

    async def _process_subscriptions(self) -> None:
        current_id = self._state("subscription_id")

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.id,
                    s.plan_key,
                    s.is_trial,
                    s.end_at,
                    s.marzban_username,
                    s.created_at,
                    u.telegram_id
                FROM subscriptions s
                JOIN users u
                  ON u.id = s.user_id
                WHERE s.id > ?
                ORDER BY s.id ASC
                LIMIT 100
                """,
                (current_id,),
            ).fetchall()

        for row in rows:
            # Платная подписка отдельно отображается
            # через операцию списания Stars.
            if bool(row["is_trial"]):
                message = (
                    "<b>🎁 Новый триал</b>\n\n"
                    f"TG ID: "
                    f"<code>{int(row['telegram_id'])}</code>\n"
                    f"Тариф: "
                    f"<code>{html.escape(str(row['plan_key']))}</code>\n"
                    f"Remnawave: "
                    f"<code>{html.escape(str(row['marzban_username']))}</code>\n"
                    f"Лимит: <b>30 ГБ</b>\n"
                    f"Действует до: "
                    f"{format_datetime(row['end_at'])}\n"
                    f"Создан: "
                    f"{format_datetime(row['created_at'])}"
                )

                sent = await self.send_text(message)

                if not sent:
                    return

            self._set_state(
                "subscription_id",
                int(row["id"]),
            )

    async def _process_error_queue(self) -> None:
        while True:
            try:
                error_text = self.error_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            now = time.monotonic()

            digest = error_fingerprint(error_text)

            previous = self.recent_errors.get(
                digest,
                0.0,
            )

            if (
                previous
                and now - previous
                < self.error_dedup_sec
            ):
                continue

            message = (
                "<b>🚨 Ошибка VPN-бота</b>\n\n"
                f"<pre>{html.escape(error_text[:3400])}</pre>"
            )

            sent = await self.send_text(message)

            if not sent:
                try:
                    self.error_queue.put_nowait(
                        error_text
                    )
                except asyncio.QueueFull:
                    pass
                return

            self.recent_errors[digest] = now

            # Чистим старую дедупликацию.
            threshold = now - (
                self.error_dedup_sec * 2
            )

            self.recent_errors = {
                key: timestamp
                for key, timestamp
                in self.recent_errors.items()
                if timestamp >= threshold
            }

    async def run(self) -> None:
        if not self.enabled:
            logger.warning(
                "Бизнес-уведомления отключены: "
                "не задан токен или chat_id"
            )

            while True:
                await asyncio.sleep(3600)

        self._init_state()

        logger.info(
            "Бизнес-уведомления включены"
        )

        while True:
            try:
                await self._process_error_queue()
                await self._process_transactions()
                await self._process_subscriptions()

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.warning(
                    "Ошибка фонового уведомителя: %s",
                    exc,
                )

            await asyncio.sleep(self.poll_sec)

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()


class TelegramErrorHandler(logging.Handler):
    def __init__(
        self,
        notifier: AdminNotifier,
        loop: asyncio.AbstractEventLoop,
    ):
        super().__init__(level=logging.ERROR)

        self.notifier = notifier
        self.loop = loop

        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] "
                "%(name)s: %(message)s"
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        # Не перехватываем ошибки самого уведомителя.
        if record.name.startswith("admin_notify"):
            return

        try:
            # logging.Formatter.format() уже добавляет exc_info.
            # Не приклеиваем traceback повторно.
            text = self.format(record)
            text = redact_secrets(text)[:3500]

            self.loop.call_soon_threadsafe(
                self.notifier.enqueue_error,
                text,
            )

        except Exception:
            # logging.Handler никогда не должен ронять бот.
            pass
