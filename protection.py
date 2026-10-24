import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Dict, Deque, Optional, Tuple

try:
    from aiogram import BaseMiddleware
except ImportError:
    from aiogram.dispatcher.middlewares.base import BaseMiddleware

from aiogram.types import CallbackQuery, Message, TelegramObject


logger = logging.getLogger("vpn_bot.protection")


class AntiFloodMiddleware(BaseMiddleware):
    """
    Защита бота:

    1. Не более N callback-нажатий за окно времени.
    2. Не более N сообщений за окно времени.
    3. Минимальная пауза между кнопками.
    4. Временная блокировка при превышении лимита.
    5. Только одна финансовая операция пользователя одновременно.
    6. Ограничение общего количества одновременно работающих обработчиков.

    Хранилище находится в RAM. Для одного процесса бота этого достаточно.
    После перезапуска временные счётчики сбрасываются.
    """

    SENSITIVE_PREFIXES = (
        "buy:",
        "topup:",
        "sub:extend:",
        "extendplan:",
        "sub:delete",
        "admin_sub:",
    )

    def __init__(self) -> None:
        self.callback_limit = max(
            1,
            int(os.getenv("BOT_CALLBACK_LIMIT_PER_MIN", "30")),
        )
        self.message_limit = max(
            1,
            int(os.getenv("BOT_MESSAGE_LIMIT_PER_MIN", "20")),
        )
        self.window_sec = max(
            10.0,
            float(os.getenv("BOT_RATE_WINDOW_SEC", "60")),
        )
        self.block_sec = max(
            10.0,
            float(os.getenv("BOT_RATE_BLOCK_SEC", "60")),
        )
        self.callback_cooldown = max(
            0.0,
            float(os.getenv("BOT_CALLBACK_COOLDOWN_SEC", "0.5")),
        )
        max_concurrent = max(
            1,
            int(os.getenv("BOT_MAX_CONCURRENT_UPDATES", "20")),
        )

        self.admin_id = int(os.getenv("ADMIN_ID", "0") or 0)

        self._buckets: Dict[
            Tuple[int, str],
            Deque[float],
        ] = defaultdict(deque)

        self._blocked_until: Dict[
            Tuple[int, str],
            float,
        ] = {}

        self._last_callback: Dict[int, float] = {}
        self._active_operations: set[int] = set()
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @staticmethod
    def _get_user_id(event: TelegramObject) -> Optional[int]:
        user = getattr(event, "from_user", None)
        if not user:
            return None
        return int(user.id)

    @staticmethod
    def _event_kind(event: TelegramObject) -> str:
        if isinstance(event, CallbackQuery):
            return "callback"
        if isinstance(event, Message):
            return "message"
        return "other"

    @classmethod
    def _is_sensitive_callback(
        cls,
        event: TelegramObject,
    ) -> bool:
        if not isinstance(event, CallbackQuery):
            return False

        data = event.data or ""
        return data.startswith(cls.SENSITIVE_PREFIXES)

    async def _answer(
        self,
        event: TelegramObject,
        text: str,
        *,
        alert: bool = False,
    ) -> None:
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(
                    text,
                    show_alert=alert,
                )
            elif isinstance(event, Message):
                await event.answer(text)
        except Exception:
            pass

    def _consume_rate_limit(
        self,
        user_id: int,
        kind: str,
        now: float,
    ) -> Tuple[bool, int]:
        key = (user_id, kind)

        blocked_until = self._blocked_until.get(key, 0.0)
        if blocked_until > now:
            return False, max(1, int(blocked_until - now))

        if blocked_until:
            self._blocked_until.pop(key, None)

        bucket = self._buckets[key]
        threshold = now - self.window_sec

        while bucket and bucket[0] <= threshold:
            bucket.popleft()

        limit = (
            self.callback_limit
            if kind == "callback"
            else self.message_limit
        )

        if len(bucket) >= limit:
            self._blocked_until[key] = now + self.block_sec
            bucket.clear()

            logger.warning(
                "Антифлуд: временно заблокирован tg_id=%s, kind=%s",
                user_id,
                kind,
            )

            return False, int(self.block_sec)

        bucket.append(now)
        return True, 0

    async def __call__(
        self,
        handler: Callable[
            [TelegramObject, Dict[str, Any]],
            Awaitable[Any],
        ],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id = self._get_user_id(event)

        if user_id is None:
            async with self._semaphore:
                return await handler(event, data)

        # Админа не блокируем лимитом количества событий,
        # но защита финансовых операций для него остаётся.
        if user_id != self.admin_id:
            kind = self._event_kind(event)
            now = time.monotonic()

            allowed, wait_sec = self._consume_rate_limit(
                user_id,
                kind,
                now,
            )

            if not allowed:
                await self._answer(
                    event,
                    (
                        "Слишком много действий.\n"
                        f"Подожди примерно {wait_sec} сек."
                    ),
                    alert=isinstance(event, CallbackQuery),
                )
                return None

            # Дополнительная защита от сверхбыстрого кликанья.
            if isinstance(event, CallbackQuery):
                previous = self._last_callback.get(user_id, 0.0)
                self._last_callback[user_id] = now

                if (
                    previous
                    and now - previous < self.callback_cooldown
                ):
                    await self._answer(
                        event,
                        "Не так быстро 🙂",
                    )
                    return None

        sensitive = self._is_sensitive_callback(event)

        # Не даём одновременно запустить покупку, продление,
        # удаление или пополнение для одного пользователя.
        if sensitive:
            if user_id in self._active_operations:
                await self._answer(
                    event,
                    "Операция уже выполняется. Подожди.",
                    alert=True,
                )
                return None

            self._active_operations.add(user_id)

        try:
            async with self._semaphore:
                return await handler(event, data)
        finally:
            if sensitive:
                self._active_operations.discard(user_id)
