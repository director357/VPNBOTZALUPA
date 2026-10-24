import sqlite3
import threading
from datetime import datetime
from typing import Optional, List, Tuple

DB_PATH = "vpn_bot.db"

_lock = threading.RLock()


def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == column for r in cur.fetchall())


def init_db():
    """Создаёт таблицы и делает безопасные миграции (ALTER TABLE при необходимости)."""
    with _get_conn() as conn, _lock:
        cur = conn.cursor()

        # Users table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0,
                stars_spent INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )

        # Миграции users
        if not _column_exists(cur, "users", "referrer_user_id"):
            cur.execute("ALTER TABLE users ADD COLUMN referrer_user_id INTEGER")
        if not _column_exists(cur, "users", "referral_rewarded"):
            cur.execute("ALTER TABLE users ADD COLUMN referral_rewarded INTEGER NOT NULL DEFAULT 0")

        # Subscriptions table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_key TEXT NOT NULL,
                is_trial INTEGER NOT NULL DEFAULT 0,
                starts_at INTEGER NOT NULL,
                end_at INTEGER NOT NULL,
                marzban_username TEXT NOT NULL,
                sub_url TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        # Transactions table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                type TEXT NOT NULL,
                description TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        # Обработанные платежи Telegram Stars.
        # telegram_payment_charge_id уникален и не позволяет
        # начислить один платёж повторно.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_payment_charge_id TEXT UNIQUE NOT NULL,
                provider_payment_charge_id TEXT,
                user_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                currency TEXT NOT NULL,
                amount INTEGER NOT NULL,
                invoice_payload TEXT,
                status TEXT NOT NULL DEFAULT 'processing',
                transaction_id INTEGER,
                created_at INTEGER NOT NULL,
                credited_at INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            )
            """
        )

        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_processed_payments_user_id
            ON processed_payments(user_id)
            """
        )

        # Платежи Platega. transaction_id выдаёт Platega и используется
        # как идемпотентный ключ callback/status-check.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS platega_payments (
                transaction_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                amount_kopecks INTEGER NOT NULL,
                credit_amount INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'RUB',
                status TEXT NOT NULL DEFAULT 'PENDING',
                payment_url TEXT,
                payload TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                credited_at INTEGER,
                transaction_row_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_row_id) REFERENCES transactions(id)
            )
            """
        )

        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_platega_payments_user_id
            ON platega_payments(user_id)
            """
        )

        # События по подпискам/уведомлениям (для дедупликации)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS subscription_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sub_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                ref_ts INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(sub_id, kind, ref_ts),
                FOREIGN KEY (sub_id) REFERENCES subscriptions(id)
            )
            """
        )

        # Бейзлайн по нодам на начало месяца (для расчёта месячного трафика)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS node_monthly_baselines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_name TEXT NOT NULL,
                year_month TEXT NOT NULL,
                base_total INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(node_name, year_month)
            )
            """
        )

        # Реферальные начисления (чтобы можно было уведомлять отдельно)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referred_user_id INTEGER NOT NULL,
                referrer_user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                notified INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (referred_user_id) REFERENCES users(id),
                FOREIGN KEY (referrer_user_id) REFERENCES users(id)
            )
            """
        )

        conn.commit()


# ---------------- Users ----------------


def get_or_create_user(telegram_id: int):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        if row:
            return row
        now = int(datetime.utcnow().timestamp())
        cur.execute(
            "INSERT INTO users (telegram_id, created_at) VALUES (?, ?)",
            (telegram_id, now),
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        return cur.fetchone()


def get_or_create_user_ex(telegram_id: int):
    """Возвращает (row, created)."""
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        if row:
            return row, False
        now = int(datetime.utcnow().timestamp())
        cur.execute(
            "INSERT INTO users (telegram_id, created_at) VALUES (?, ?)",
            (telegram_id, now),
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        return cur.fetchone(), True


def get_user_by_id(user_id: int):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()


def get_user_by_telegram_id(telegram_id: int):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        return cur.fetchone()


def set_admin(telegram_id: int, is_admin: bool):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        user = get_or_create_user(telegram_id)
        cur.execute(
            "UPDATE users SET is_admin = ? WHERE id = ?",
            (1 if is_admin else 0, user["id"]),
        )
        conn.commit()


def try_set_referrer(new_user_id: int, referrer_telegram_id: int) -> bool:
    """Привязать реферера к НОВОМУ пользователю (один раз).
    new_user_id — internal users.id, referrer_telegram_id — telegram id пригласившего.
    Возвращает True если привязали, иначе False.
    """
    if referrer_telegram_id <= 0:
        return False

    with _get_conn() as conn, _lock:
        cur = conn.cursor()

        # Кто реферер
        cur.execute("SELECT id FROM users WHERE telegram_id = ?", (referrer_telegram_id,))
        ref_row = cur.fetchone()
        if not ref_row:
            return False
        referrer_id = int(ref_row["id"])

        if referrer_id == int(new_user_id):
            return False

        # Привязываем только если ещё не привязан
        cur.execute(
            """
            UPDATE users
            SET referrer_user_id = ?
            WHERE id = ? AND (referrer_user_id IS NULL OR referrer_user_id = 0)
            """,
            (referrer_id, new_user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def change_balance(
    user_id: int,
    delta: int,
    tx_type: str,
    description: str = "",
    *,
    count_as_spent: bool = True,
):
    """Меняет баланс и пишет транзакцию.

    - Всегда пишет запись в transactions.
    - Авто-рефералка начисляется ТОЛЬКО на типы пополнения (см. REFERRAL_TRIGGER_TYPES).
    - stars_spent увеличиваем только когда delta < 0 и count_as_spent=True.
      (Напр., покупки/продления — True; админские корректировки — можно False.)
    """
    REFERRAL_TRIGGER_TYPES = {"topup", "deposit", "user_topup", "refill", "admin_topup"}

    with _get_conn() as conn, _lock:
        cur = conn.cursor()

        cur.execute(
            "SELECT balance, stars_spent, referrer_user_id, referral_rewarded FROM users WHERE id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("User not found")

        new_balance = int(row["balance"]) + int(delta)
        if new_balance < 0:
            raise ValueError("Insufficient balance")

        stars_spent = int(row["stars_spent"])
        if int(delta) < 0 and count_as_spent:
            stars_spent += -int(delta)

        cur.execute(
            "UPDATE users SET balance = ?, stars_spent = ? WHERE id = ?",
            (new_balance, stars_spent, user_id),
        )

        now = int(datetime.utcnow().timestamp())
        cur.execute(
            """
            INSERT INTO transactions (user_id, amount, type, description, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, int(delta), tx_type, description, now),
        )

        # Реферальный бонус: 80% один раз за первое пополнение
        # Начисляем только на триггерные типы пополнения и только если referral_rewarded = 0
        if int(delta) > 0 and tx_type in REFERRAL_TRIGGER_TYPES:
            referrer_id = row["referrer_user_id"]
            rewarded = int(row["referral_rewarded"] or 0)
            if referrer_id and not rewarded:
                bonus = (int(delta) * 80) // 100  # 80% от суммы пополнения
                if bonus > 0:
                    cur.execute("SELECT balance FROM users WHERE id = ?", (int(referrer_id),))
                    ref_bal_row = cur.fetchone()
                    if ref_bal_row:
                        ref_new_balance = int(ref_bal_row["balance"]) + int(bonus)
                        cur.execute(
                            "UPDATE users SET balance = ? WHERE id = ?",
                            (ref_new_balance, int(referrer_id)),
                        )
                        cur.execute(
                            """
                            INSERT INTO transactions (user_id, amount, type, description, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                int(referrer_id),
                                int(bonus),
                                "ref_bonus",
                                "Реферальный бонус 80% (одноразово)",
                                now,
                            ),
                        )
                        cur.execute(
                            "UPDATE users SET referral_rewarded = 1 WHERE id = ?",
                            (user_id,),
                        )
                        cur.execute(
                            """
                            INSERT INTO referral_rewards (referred_user_id, referrer_user_id, amount, created_at, notified)
                            VALUES (?, ?, ?, ?, 0)
                            """,
                            (user_id, int(referrer_id), int(bonus), now),
                        )

        conn.commit()
        return new_balance



def credit_telegram_stars_payment(
    user_id: int,
    telegram_id: int,
    amount: int,
    telegram_payment_charge_id: str,
    *,
    provider_payment_charge_id: str = "",
    invoice_payload: str = "",
):
    """
    Атомарно зачисляет Telegram Stars.

    Возвращает:
        (credited_now, new_balance)

    credited_now=True:
        платёж впервые обработан и баланс начислен.

    credited_now=False:
        этот telegram_payment_charge_id уже был обработан,
        баланс повторно не менялся.
    """
    user_id = int(user_id)
    telegram_id = int(telegram_id)
    amount = int(amount)

    charge_id = str(
        telegram_payment_charge_id or ""
    ).strip()

    provider_charge_id = str(
        provider_payment_charge_id or ""
    ).strip()

    payload = str(invoice_payload or "")

    if amount <= 0:
        raise ValueError(
            "Payment amount must be greater than zero"
        )

    if not charge_id:
        raise ValueError(
            "telegram_payment_charge_id is required"
        )

    with _get_conn() as conn, _lock:
        cur = conn.cursor()

        try:
            # Блокируем запись на время всей денежной операции.
            cur.execute("BEGIN IMMEDIATE")

            now = int(datetime.utcnow().timestamp())

            try:
                cur.execute(
                    """
                    INSERT INTO processed_payments (
                        telegram_payment_charge_id,
                        provider_payment_charge_id,
                        user_id,
                        telegram_id,
                        currency,
                        amount,
                        invoice_payload,
                        status,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, 'XTR', ?, ?, 'processing', ?)
                    """,
                    (
                        charge_id,
                        provider_charge_id,
                        user_id,
                        telegram_id,
                        amount,
                        payload,
                        now,
                    ),
                )

            except sqlite3.IntegrityError:
                existing = cur.execute(
                    """
                    SELECT
                        user_id,
                        telegram_id,
                        currency,
                        amount,
                        status
                    FROM processed_payments
                    WHERE telegram_payment_charge_id = ?
                    """,
                    (charge_id,),
                ).fetchone()

                if not existing:
                    raise

                # Один charge_id не должен внезапно появиться
                # с другим пользователем или суммой.
                if (
                    int(existing["user_id"]) != user_id
                    or int(existing["telegram_id"]) != telegram_id
                    or str(existing["currency"]) != "XTR"
                    or int(existing["amount"]) != amount
                ):
                    raise ValueError(
                        "Payment charge collision: "
                        "stored payment data does not match"
                    )

                balance_row = cur.execute(
                    """
                    SELECT balance
                    FROM users
                    WHERE id = ?
                    """,
                    (user_id,),
                ).fetchone()

                if not balance_row:
                    raise ValueError("User not found")

                # Уже зачисленный платёж просто игнорируем.
                if str(existing["status"]) == "credited":
                    conn.rollback()
                    return False, int(
                        balance_row["balance"]
                    )

                # Такой статус при атомарной транзакции появиться
                # не должен. Не начисляем деньги автоматически.
                raise RuntimeError(
                    "Payment exists but is not credited: "
                    f"status={existing['status']}"
                )

            user = cur.execute(
                """
                SELECT
                    balance,
                    stars_spent,
                    referrer_user_id,
                    referral_rewarded
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()

            if not user:
                raise ValueError("User not found")

            new_balance = int(user["balance"]) + amount

            cur.execute(
                """
                UPDATE users
                SET balance = ?
                WHERE id = ?
                """,
                (
                    new_balance,
                    user_id,
                ),
            )

            cur.execute(
                """
                INSERT INTO transactions (
                    user_id,
                    amount,
                    type,
                    description,
                    created_at
                )
                VALUES (?, ?, 'topup', ?, ?)
                """,
                (
                    user_id,
                    amount,
                    (
                        "Пополнение через Telegram Stars "
                        f"({amount}★)"
                    ),
                    now,
                ),
            )

            transaction_id = int(cur.lastrowid)

            # Сохраняем существующую бизнес-логику:
            # 80% реферального бонуса один раз
            # при первом пользовательском пополнении.
            referrer_id = user["referrer_user_id"]
            rewarded = int(
                user["referral_rewarded"] or 0
            )

            if referrer_id and not rewarded:
                bonus = (amount * 80) // 100

                if bonus > 0:
                    referrer = cur.execute(
                        """
                        SELECT balance
                        FROM users
                        WHERE id = ?
                        """,
                        (int(referrer_id),),
                    ).fetchone()

                    if referrer:
                        referrer_balance = (
                            int(referrer["balance"])
                            + bonus
                        )

                        cur.execute(
                            """
                            UPDATE users
                            SET balance = ?
                            WHERE id = ?
                            """,
                            (
                                referrer_balance,
                                int(referrer_id),
                            ),
                        )

                        cur.execute(
                            """
                            INSERT INTO transactions (
                                user_id,
                                amount,
                                type,
                                description,
                                created_at
                            )
                            VALUES (
                                ?,
                                ?,
                                'ref_bonus',
                                'Реферальный бонус 80% (одноразово)',
                                ?
                            )
                            """,
                            (
                                int(referrer_id),
                                bonus,
                                now,
                            ),
                        )

                        cur.execute(
                            """
                            UPDATE users
                            SET referral_rewarded = 1
                            WHERE id = ?
                            """,
                            (user_id,),
                        )

                        cur.execute(
                            """
                            INSERT INTO referral_rewards (
                                referred_user_id,
                                referrer_user_id,
                                amount,
                                created_at,
                                notified
                            )
                            VALUES (?, ?, ?, ?, 0)
                            """,
                            (
                                user_id,
                                int(referrer_id),
                                bonus,
                                now,
                            ),
                        )

            cur.execute(
                """
                UPDATE processed_payments
                SET
                    status = 'credited',
                    transaction_id = ?,
                    credited_at = ?
                WHERE telegram_payment_charge_id = ?
                """,
                (
                    transaction_id,
                    now,
                    charge_id,
                ),
            )

            conn.commit()

            return True, new_balance

        except Exception:
            conn.rollback()
            raise


def get_balance(user_id: int) -> int:
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        return int(row["balance"]) if row else 0


# ---------------- Subscriptions ----------------


def create_subscription(
    user_id: int,
    plan_key: str,
    is_trial: bool,
    starts_at: int,
    end_at: int,
    marzban_username: str,
    sub_url: str,
):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        now = int(datetime.utcnow().timestamp())
        cur.execute(
            """
            INSERT INTO subscriptions (
                user_id, plan_key, is_trial, starts_at, end_at,
                marzban_username, sub_url, is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                user_id,
                plan_key,
                1 if is_trial else 0,
                starts_at,
                end_at,
                marzban_username,
                sub_url,
                now,
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_user_subscriptions(user_id: int, include_inactive: bool = False):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        if include_inactive:
            cur.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
        else:
            cur.execute(
                """
                SELECT * FROM subscriptions
                WHERE user_id = ? AND is_active = 1
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
        return cur.fetchall()


def get_active_subscriptions_count(user_id: int) -> int:
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS cnt FROM subscriptions
            WHERE user_id = ? AND is_active = 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        return int(row["cnt"]) if row else 0


def get_total_subscriptions_count(user_id: int) -> int:
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM subscriptions WHERE user_id = ?", (user_id,)
        )
        row = cur.fetchone()
        return int(row["cnt"]) if row else 0


def get_subscription_by_id(sub_id: int):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,))
        return cur.fetchone()


def set_subscription_active(sub_id: int, active: bool):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            "UPDATE subscriptions SET is_active = ? WHERE id = ?",
            (1 if active else 0, sub_id),
        )
        conn.commit()


def update_subscription_end(sub_id: int, new_end_ts: int):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            "UPDATE subscriptions SET end_at = ? WHERE id = ?",
            (int(new_end_ts), int(sub_id)),
        )
        conn.commit()


def update_subscription_plan(sub_id: int, plan_key: str, is_trial: bool):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            "UPDATE subscriptions SET plan_key = ?, is_trial = ? WHERE id = ?",
            (plan_key, 1 if is_trial else 0, sub_id),
        )
        conn.commit()


def user_has_trial(user_id: int) -> bool:
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM subscriptions WHERE user_id = ? AND is_trial = 1 LIMIT 1",
            (user_id,),
        )
        return cur.fetchone() is not None


def get_active_subscriptions_with_users():
    """Активные подписки + telegram_id и баланс пользователя (для задач/уведомлений)."""
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                s.*,
                u.telegram_id AS telegram_id,
                u.balance AS user_balance
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.is_active = 1
            ORDER BY s.end_at ASC
            """
        )
        return cur.fetchall()


# ---------------- Events / Notifications ----------------


def mark_subscription_event(sub_id: int, kind: str, ref_ts: int) -> bool:
    """Пишет событие (sub_id, kind, ref_ts). Возвращает True если вставили, False если уже было."""
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        now = int(datetime.utcnow().timestamp())
        cur.execute(
            """
            INSERT OR IGNORE INTO subscription_events (sub_id, kind, ref_ts, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (int(sub_id), str(kind), int(ref_ts), now),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------- Admin / stats ----------------


def get_all_users_with_stats():
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                u.*,
                (SELECT COUNT(*) FROM subscriptions s WHERE s.user_id = u.id) AS total_subs,
                (SELECT COUNT(*) FROM subscriptions s WHERE s.user_id = u.id AND s.is_active = 1) AS active_subs
            FROM users u
            ORDER BY u.created_at ASC
            """
        )
        return cur.fetchall()


def get_users_with_stats_page(limit: int, offset: int = 0, order_desc: bool = True):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        order = "DESC" if order_desc else "ASC"
        cur.execute(
            f"""
            SELECT
                u.*,
                (SELECT COUNT(*) FROM subscriptions s WHERE s.user_id = u.id) AS total_subs,
                (SELECT COUNT(*) FROM subscriptions s WHERE s.user_id = u.id AND s.is_active = 1) AS active_subs
            FROM users u
            ORDER BY u.created_at {order}
            LIMIT ? OFFSET ?
            """,
            (int(limit), int(offset)),
        )
        return cur.fetchall()


def get_users_count() -> int:
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        row = cur.fetchone()
        return int(row["cnt"]) if row else 0



def get_user_transactions_count(user_id: int) -> int:
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM transactions WHERE user_id = ?",
            (int(user_id),),
        )
        row = cur.fetchone()
        return int(row["cnt"]) if row else 0


def get_user_transactions(user_id: int, limit: int = 20, offset: int = 0):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (int(user_id), int(limit), int(offset)),
        )
        return cur.fetchall()



# ---------------- Nodes monthly usage baseline ----------------


def get_node_monthly_baseline(node_name: str, year_month: str) -> Optional[int]:
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            "SELECT base_total FROM node_monthly_baselines WHERE node_name = ? AND year_month = ?",
            (node_name, year_month),
        )
        row = cur.fetchone()
        return int(row["base_total"]) if row else None


def upsert_node_monthly_baseline(node_name: str, year_month: str, base_total: int) -> None:
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        now = int(datetime.utcnow().timestamp())
        cur.execute(
            """
            INSERT INTO node_monthly_baselines (node_name, year_month, base_total, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(node_name, year_month)
            DO UPDATE SET base_total = excluded.base_total, updated_at = excluded.updated_at
            """,
            (str(node_name), str(year_month), int(base_total), now),
        )
        conn.commit()


# ---------------- Referral notifications ----------------


def get_pending_referral_rewards(limit: int = 50):
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT rr.*, 
                   u1.telegram_id AS referred_tg,
                   u2.telegram_id AS referrer_tg
            FROM referral_rewards rr
            JOIN users u1 ON u1.id = rr.referred_user_id
            JOIN users u2 ON u2.id = rr.referrer_user_id
            WHERE rr.notified = 0
            ORDER BY rr.created_at ASC
            LIMIT ?
            """,
            (int(limit),),
        )
        return cur.fetchall()


def mark_referral_reward_notified(reward_id: int) -> None:
    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute(
            "UPDATE referral_rewards SET notified = 1 WHERE id = ?",
            (int(reward_id),),
        )
        conn.commit()


# ---------------- Referral stats ----------------

def get_referral_stats(referrer_user_id: int) -> Tuple[int, int, int]:
    """Возвращает (invited_cnt, rewarded_cnt, earned_stars) для реферера."""
    with _get_conn() as conn, _lock:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                COUNT(*) AS invited,
                SUM(CASE WHEN referral_rewarded = 1 THEN 1 ELSE 0 END) AS rewarded
            FROM users
            WHERE referrer_user_id = ?
            """,
            (int(referrer_user_id),),
        )
        row = cur.fetchone()
        invited = int(row["invited"] or 0) if row else 0
        rewarded = int(row["rewarded"] or 0) if row else 0

        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS earned
            FROM transactions
            WHERE user_id = ? AND type = 'ref_bonus'
            """,
            (int(referrer_user_id),),
        )
        row2 = cur.fetchone()
        earned = int(row2["earned"] or 0) if row2 else 0

        return invited, rewarded, earned


def finalize_paid_renewal(
    *,
    user_id: int,
    sub_id: int,
    new_end_ts: int,
    amount: int,
    tx_type: str,
    description: str,
    plan_key: Optional[str] = None,
    is_trial: Optional[bool] = None,
) -> int:
    """Атомарно меняет срок/тариф, списывает Stars и пишет транзакцию.

    Вызывается только ПОСЛЕ успешного обновления пользователя в Remnawave.
    """
    amount = int(amount)
    if amount < 0:
        raise ValueError("amount must be non-negative")

    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            "SELECT balance, stars_spent FROM users WHERE id = ?",
            (int(user_id),),
        )
        user = cur.fetchone()
        if not user:
            raise ValueError("User not found")
        balance = int(user["balance"])
        if balance < amount:
            raise ValueError("Insufficient balance")

        cur.execute(
            "SELECT id FROM subscriptions WHERE id = ? AND user_id = ?",
            (int(sub_id), int(user_id)),
        )
        if not cur.fetchone():
            raise ValueError("Subscription not found")

        if plan_key is None:
            cur.execute(
                "UPDATE subscriptions SET end_at = ?, is_active = 1 WHERE id = ?",
                (int(new_end_ts), int(sub_id)),
            )
        else:
            cur.execute(
                """
                UPDATE subscriptions
                SET end_at = ?, plan_key = ?, is_trial = ?, is_active = 1
                WHERE id = ?
                """,
                (
                    int(new_end_ts),
                    str(plan_key),
                    1 if bool(is_trial) else 0,
                    int(sub_id),
                ),
            )

        new_balance = balance - amount
        new_spent = int(user["stars_spent"]) + amount
        cur.execute(
            "UPDATE users SET balance = ?, stars_spent = ? WHERE id = ?",
            (new_balance, new_spent, int(user_id)),
        )
        now = int(datetime.utcnow().timestamp())
        cur.execute(
            """
            INSERT INTO transactions (user_id, amount, type, description, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(user_id), -amount, str(tx_type), str(description), now),
        )
        conn.commit()
        return new_balance


# ---------------- Platega ----------------

def create_platega_payment(
    *,
    transaction_id: str,
    user_id: int,
    telegram_id: int,
    amount_kopecks: int,
    credit_amount: int,
    payment_url: str,
    payload: str = "",
):
    tx_id = str(transaction_id or "").strip()
    if not tx_id:
        raise ValueError("transaction_id is required")
    if int(amount_kopecks) <= 0 or int(credit_amount) <= 0:
        raise ValueError("amounts must be positive")

    now = int(datetime.utcnow().timestamp())
    with _get_conn() as conn, _lock:
        conn.execute(
            """
            INSERT INTO platega_payments (
                transaction_id, user_id, telegram_id, amount_kopecks,
                credit_amount, currency, status, payment_url, payload,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'RUB', 'PENDING', ?, ?, ?, ?)
            """,
            (
                tx_id, int(user_id), int(telegram_id), int(amount_kopecks),
                int(credit_amount), str(payment_url or ""), str(payload or ""),
                now, now,
            ),
        )
        conn.commit()


def get_platega_payment(transaction_id: str):
    with _get_conn() as conn, _lock:
        return conn.execute(
            "SELECT * FROM platega_payments WHERE transaction_id = ?",
            (str(transaction_id or "").strip(),),
        ).fetchone()


def process_platega_status(
    *,
    transaction_id: str,
    status: str,
    callback_amount_kopecks: int | None = None,
    currency: str = "RUB",
):
    """
    Идемпотентно применяет статус Platega. Баланс начисляется только один раз
    при CONFIRMED и только при полном совпадении суммы/валюты.
    """
    tx_id = str(transaction_id or "").strip()
    new_status = str(status or "").strip().upper()
    currency = str(currency or "RUB").strip().upper()
    if not tx_id or not new_status:
        raise ValueError("transaction_id/status are required")

    with _get_conn() as conn, _lock:
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            payment = cur.execute(
                "SELECT * FROM platega_payments WHERE transaction_id = ?",
                (tx_id,),
            ).fetchone()
            if not payment:
                raise KeyError(tx_id)

            if currency != str(payment["currency"]).upper():
                raise ValueError("Platega currency mismatch")
            if callback_amount_kopecks is not None:
                reported = int(callback_amount_kopecks)
                expected = int(payment["amount_kopecks"])

                # Platega может включать комиссию плательщика
                # в итоговое поле amount. Поэтому запрещаем только
                # недоплату. Переплата не увеличивает начисление:
                # пользователь получит ровно payment["credit_amount"].
                if reported < expected:
                    raise ValueError(
                        "Platega amount underpayment: "
                        f"expected={expected}, reported={reported}"
                    )

            now = int(datetime.utcnow().timestamp())
            if new_status != "CONFIRMED":
                cur.execute(
                    "UPDATE platega_payments SET status = ?, updated_at = ? "
                    "WHERE transaction_id = ?",
                    (new_status, now, tx_id),
                )
                conn.commit()
                return {
                    "credited_now": False,
                    "new_balance": get_balance(int(payment["user_id"])),
                    "telegram_id": int(payment["telegram_id"]),
                    "credit_amount": int(payment["credit_amount"]),
                    "status": new_status,
                }

            if str(payment["status"]).upper() == "CREDITED" or payment["credited_at"]:
                balance_row = cur.execute(
                    "SELECT balance FROM users WHERE id = ?",
                    (int(payment["user_id"]),),
                ).fetchone()
                conn.rollback()
                return {
                    "credited_now": False,
                    "new_balance": int(balance_row["balance"]),
                    "telegram_id": int(payment["telegram_id"]),
                    "credit_amount": int(payment["credit_amount"]),
                    "status": "CREDITED",
                }

            user = cur.execute(
                """
                SELECT balance, referrer_user_id, referral_rewarded
                FROM users WHERE id = ?
                """,
                (int(payment["user_id"]),),
            ).fetchone()
            if not user:
                raise ValueError("User not found")

            credit_amount = int(payment["credit_amount"])
            new_balance = int(user["balance"]) + credit_amount
            cur.execute(
                "UPDATE users SET balance = ? WHERE id = ?",
                (new_balance, int(payment["user_id"])),
            )
            rub_text = f"{int(payment['amount_kopecks']) / 100:.2f}"
            cur.execute(
                """
                INSERT INTO transactions (
                    user_id, amount, type, description, created_at
                ) VALUES (?, ?, 'topup', ?, ?)
                """,
                (
                    int(payment["user_id"]), credit_amount,
                    f"Пополнение через Platega ({rub_text} RUB → {credit_amount}★)",
                    now,
                ),
            )
            transaction_row_id = int(cur.lastrowid)

            referrer_id = user["referrer_user_id"]
            rewarded = int(user["referral_rewarded"] or 0)
            if referrer_id and not rewarded:
                bonus = (credit_amount * 80) // 100
                if bonus > 0:
                    referrer = cur.execute(
                        "SELECT balance FROM users WHERE id = ?",
                        (int(referrer_id),),
                    ).fetchone()
                    if referrer:
                        cur.execute(
                            "UPDATE users SET balance = ? WHERE id = ?",
                            (int(referrer["balance"]) + bonus, int(referrer_id)),
                        )
                        cur.execute(
                            """
                            INSERT INTO transactions (
                                user_id, amount, type, description, created_at
                            ) VALUES (?, ?, 'ref_bonus',
                                'Реферальный бонус 80% (одноразово)', ?)
                            """,
                            (int(referrer_id), bonus, now),
                        )
                        cur.execute(
                            "UPDATE users SET referral_rewarded = 1 WHERE id = ?",
                            (int(payment["user_id"]),),
                        )
                        cur.execute(
                            """
                            INSERT INTO referral_rewards (
                                referred_user_id, referrer_user_id, amount,
                                created_at, notified
                            ) VALUES (?, ?, ?, ?, 0)
                            """,
                            (int(payment["user_id"]), int(referrer_id), bonus, now),
                        )

            cur.execute(
                """
                UPDATE platega_payments
                SET status = 'CREDITED', updated_at = ?, credited_at = ?,
                    transaction_row_id = ?
                WHERE transaction_id = ?
                """,
                (now, now, transaction_row_id, tx_id),
            )
            conn.commit()
            return {
                "credited_now": True,
                "new_balance": new_balance,
                "telegram_id": int(payment["telegram_id"]),
                "credit_amount": credit_amount,
                "status": "CREDITED",
            }
        except Exception:
            conn.rollback()
            raise
