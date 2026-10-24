import asyncio
import html
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import remna as marzban


logger = logging.getLogger("reconcile")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "vpn_bot.db"

TRIAL_LIMIT_BYTES = 30 * 1024 ** 3


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(
        name,
        "true" if default else "false",
    ).strip().lower()

    return value in {"1", "true", "yes", "on"}


RECONCILE_ENABLED = _env_bool(
    "RECONCILE_ENABLED",
    True,
)

RECONCILE_AUTO_FIX = _env_bool(
    "RECONCILE_AUTO_FIX",
    False,
)

RECONCILE_INTERVAL_SEC = max(
    60,
    int(os.getenv("RECONCILE_INTERVAL_SEC", "1800")),
)

RECONCILE_INITIAL_DELAY_SEC = max(
    5,
    int(os.getenv("RECONCILE_INITIAL_DELAY_SEC", "60")),
)

RECONCILE_BATCH_SIZE = max(
    1,
    min(
        500,
        int(os.getenv("RECONCILE_BATCH_SIZE", "100")),
    ),
)

RECONCILE_CONCURRENCY = max(
    1,
    min(
        20,
        int(os.getenv("RECONCILE_CONCURRENCY", "5")),
    ),
)

RECONCILE_MISSING_THRESHOLD = max(
    1,
    int(os.getenv("RECONCILE_MISSING_THRESHOLD", "3")),
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=20,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _init_tables() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reconcile_state (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reconcile_issues (
                sub_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                issue TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 1,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                last_notified INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        conn.commit()


def _get_cursor() -> int:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT value
            FROM reconcile_state
            WHERE key = 'subscription_cursor'
            """
        ).fetchone()

        return int(row["value"]) if row else 0


def _set_cursor(value: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO reconcile_state (key, value)
            VALUES ('subscription_cursor', ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """,
            (int(value),),
        )
        conn.commit()


def _get_next_batch() -> list[sqlite3.Row]:
    cursor = _get_cursor()

    query = """
        SELECT
            s.id,
            s.user_id,
            s.plan_key,
            s.is_trial,
            s.starts_at,
            s.end_at,
            s.marzban_username,
            s.sub_url,
            s.is_active,
            u.telegram_id
        FROM subscriptions s
        JOIN users u
          ON u.id = s.user_id
        WHERE s.is_active = 1
          AND s.id > ?
        ORDER BY s.id ASC
        LIMIT ?
    """

    with _connect() as conn:
        rows = conn.execute(
            query,
            (cursor, RECONCILE_BATCH_SIZE),
        ).fetchall()

        if rows:
            return rows

        # Закончили проход по таблице — начинаем заново.
        _set_cursor(0)

        return conn.execute(
            query,
            (0, RECONCILE_BATCH_SIZE),
        ).fetchall()


def _first_int(
    data: dict[str, Any],
    *keys: str,
) -> int:
    for key in keys:
        value = data.get(key)

        if value is None:
            continue

        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue

    return 0


def _parse_timestamp(value: Any) -> Optional[int]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        result = int(value)

        # Миллисекунды → секунды.
        if result > 10_000_000_000:
            result //= 1000

        return result

    text = str(value).strip()

    if not text:
        return None

    if text.isdigit():
        return _parse_timestamp(int(text))

    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return int(parsed.timestamp())

    except ValueError:
        return None


def _remote_expire(remote: dict[str, Any]) -> Optional[int]:
    for key in (
        "expireAt",
        "expiresAt",
        "expire",
        "expire_at",
    ):
        parsed = _parse_timestamp(remote.get(key))

        if parsed is not None:
            return parsed

    return None


def _remote_limit(remote: dict[str, Any]) -> int:
    return _first_int(
        remote,
        "trafficLimitBytes",
        "dataLimitBytes",
        "traffic_limit_bytes",
        "data_limit",
    )


def _remote_used(remote: dict[str, Any]) -> int:
    return _first_int(
        remote,
        "usedTrafficBytes",
        "trafficUsedBytes",
        "usedBytes",
        "used_traffic_bytes",
        "used_traffic",
    )


def _record_missing(
    sub_id: int,
    username: str,
) -> tuple[int, bool]:
    now = int(time.time())

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO reconcile_issues (
                sub_id,
                username,
                issue,
                hits,
                first_seen,
                last_seen,
                last_notified
            )
            VALUES (?, ?, 'remote_missing', 1, ?, ?, 0)
            ON CONFLICT(sub_id)
            DO UPDATE SET
                username = excluded.username,
                issue = excluded.issue,
                hits = reconcile_issues.hits + 1,
                last_seen = excluded.last_seen
            """,
            (
                int(sub_id),
                username,
                now,
                now,
            ),
        )

        row = conn.execute(
            """
            SELECT hits, last_notified
            FROM reconcile_issues
            WHERE sub_id = ?
            """,
            (int(sub_id),),
        ).fetchone()

        conn.commit()

    hits = int(row["hits"])
    last_notified = int(row["last_notified"])

    should_notify = (
        hits >= RECONCILE_MISSING_THRESHOLD
        and now - last_notified >= 24 * 60 * 60
    )

    return hits, should_notify


def _mark_missing_notified(sub_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE reconcile_issues
            SET last_notified = ?
            WHERE sub_id = ?
            """,
            (
                int(time.time()),
                int(sub_id),
            ),
        )
        conn.commit()


def _clear_issue(sub_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            DELETE FROM reconcile_issues
            WHERE sub_id = ?
            """,
            (int(sub_id),),
        )
        conn.commit()


async def _check_subscription(
    row: sqlite3.Row,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        sub_id = int(row["id"])
        tg_id = int(row["telegram_id"])
        username = str(row["marzban_username"])
        end_at = int(row["end_at"])
        is_trial = bool(row["is_trial"])
        plan_key = str(row["plan_key"])

        try:
            remote = await marzban.get_user(username)

        except Exception as exc:
            logger.warning(
                "Ошибка проверки %s: %s",
                username,
                exc,
            )

            return {
                "type": "error",
                "username": username,
                "detail": str(exc),
            }

        if not remote:
            hits, should_notify = _record_missing(
                sub_id,
                username,
            )

            return {
                "type": (
                    "missing_alert"
                    if should_notify
                    else "missing"
                ),
                "sub_id": sub_id,
                "tg_id": tg_id,
                "username": username,
                "hits": hits,
                "plan_key": plan_key,
            }

        _clear_issue(sub_id)

        expected_limit = (
            TRIAL_LIMIT_BYTES
            if is_trial
            else 0
        )

        actual_limit = _remote_limit(remote)
        actual_expire = _remote_expire(remote)
        used_traffic = _remote_used(remote)

        remote_status = str(
            remote.get("status") or ""
        ).strip().upper()

        expire_fix: Optional[int] = None
        traffic_fix: Optional[int] = None
        status_fix: Optional[str] = None

        reasons: list[str] = []

        if (
            actual_expire is None
            or abs(actual_expire - end_at) > 120
        ):
            expire_fix = end_at
            reasons.append("срок")

        if actual_limit != expected_limit:
            traffic_fix = expected_limit
            reasons.append("лимит")

        now = int(time.time())

        disabled_statuses = {
            "DISABLED",
            "EXPIRED",
            "ON_HOLD",
        }

        # LIMITED у триала с исчерпанными 30 ГБ — правильный статус,
        # его автоматически не включаем.
        can_enable = (
            not is_trial
            or used_traffic < expected_limit
        )

        if (
            end_at > now
            and can_enable
            and remote_status in disabled_statuses
        ):
            status_fix = "active"
            reasons.append("статус")

        if not reasons:
            return {
                "type": "ok",
                "username": username,
            }

        if not RECONCILE_AUTO_FIX:
            return {
                "type": "drift",
                "tg_id": tg_id,
                "username": username,
                "reasons": reasons,
                "plan_key": plan_key,
            }

        try:
            await marzban.modify_user(
                username,
                expire_ts=expire_fix,
                traffic_limit_bytes=traffic_fix,
                status=status_fix,
            )

        except Exception as exc:
            logger.warning(
                "Не удалось исправить %s: %s",
                username,
                exc,
            )

            return {
                "type": "error",
                "username": username,
                "detail": str(exc),
            }

        return {
            "type": "fixed",
            "tg_id": tg_id,
            "username": username,
            "reasons": reasons,
            "plan_key": plan_key,
        }


async def run_reconciliation_once(
    notifier=None,
) -> dict[str, int]:
    _init_tables()

    rows = _get_next_batch()

    summary = {
        "checked": len(rows),
        "ok": 0,
        "fixed": 0,
        "drift": 0,
        "missing": 0,
        "errors": 0,
    }

    if not rows:
        return summary

    semaphore = asyncio.Semaphore(
        RECONCILE_CONCURRENCY
    )

    results = await asyncio.gather(
        *(
            _check_subscription(row, semaphore)
            for row in rows
        )
    )

    fixed_lines: list[str] = []
    missing_lines: list[str] = []

    for result in results:
        result_type = result["type"]

        if result_type == "ok":
            summary["ok"] += 1

        elif result_type == "fixed":
            summary["fixed"] += 1

            reasons = ", ".join(result["reasons"])

            fixed_lines.append(
                f"• <code>{html.escape(result['username'])}</code>"
                f" — {html.escape(reasons)}"
            )

        elif result_type == "drift":
            summary["drift"] += 1

        elif result_type in {
            "missing",
            "missing_alert",
        }:
            summary["missing"] += 1

            if result_type == "missing_alert":
                missing_lines.append(
                    f"• TG <code>{result['tg_id']}</code>\n"
                    f"  <code>{html.escape(result['username'])}</code>\n"
                    f"  проверок подряд: {result['hits']}"
                )

                _mark_missing_notified(
                    int(result["sub_id"])
                )

        elif result_type == "error":
            summary["errors"] += 1

    _set_cursor(
        max(int(row["id"]) for row in rows)
    )

    if notifier and fixed_lines:
        shown = fixed_lines[:25]
        extra = len(fixed_lines) - len(shown)

        message = (
            "<b>🛠 Исправлена рассинхронизация</b>\n\n"
            + "\n".join(shown)
        )

        if extra > 0:
            message += f"\n\nИ ещё: {extra}"

        await notifier.send_text(message)

    if notifier and missing_lines:
        shown = missing_lines[:20]
        extra = len(missing_lines) - len(shown)

        message = (
            "<b>🚨 Пользователь отсутствует в Remnawave</b>\n\n"
            "Запись в SQLite оставлена без изменений.\n"
            "Автоматически пересоздавать пользователя опасно: "
            "изменится ссылка подписки.\n\n"
            + "\n".join(shown)
        )

        if extra > 0:
            message += f"\n\nИ ещё: {extra}"

        await notifier.send_text(message)

    logger.info(
        "Сверка завершена: checked=%s ok=%s fixed=%s "
        "drift=%s missing=%s errors=%s",
        summary["checked"],
        summary["ok"],
        summary["fixed"],
        summary["drift"],
        summary["missing"],
        summary["errors"],
    )

    return summary


async def reconciliation_loop(notifier=None) -> None:
    if not RECONCILE_ENABLED:
        logger.info("Автоматическая сверка отключена")
        return

    logger.info(
        "Сверка SQLite ↔ Remnawave включена: "
        "interval=%ss batch=%s concurrency=%s auto_fix=%s",
        RECONCILE_INTERVAL_SEC,
        RECONCILE_BATCH_SIZE,
        RECONCILE_CONCURRENCY,
        RECONCILE_AUTO_FIX,
    )

    await asyncio.sleep(
        RECONCILE_INITIAL_DELAY_SEC
    )

    while True:
        try:
            await run_reconciliation_once(notifier)

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Ошибка автоматической сверки"
            )

        await asyncio.sleep(
            RECONCILE_INTERVAL_SEC
        )
