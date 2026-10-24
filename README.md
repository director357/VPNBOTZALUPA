<div align="center">

# 🛡️ VPN BOT ZALUPA

**Telegram-бот для продажи VPN-подписок [Remnawave](https://docs.rw)**

Оплата: **Telegram Stars** · **Platega (карта/СБП)** · Реферальная программа · Автопродление

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.29-2CA5E0?logo=telegram&logoColor=white)](https://aiogram.dev)
[![Remnawave](https://img.shields.io/badge/Remnawave-2.x%2B-6366f1)](https://docs.rw)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

</div>


## 📖 О проекте

Полноценный Telegram-бот для автоматической продажи VPN-подписок:

- Пользователь выбирает тариф → платит Stars или картой → получает готовую ссылку + QR-код для подключения.
- Подписка автоматически создаётся в **Remnawave** с заданным лимитом трафика и сроком.
- Продления, конвертация триала в платный тариф, автосписание — всё автоматизировано.
- Админ управляет пользователями, балансами и подписками прямо из бота.
- Все ошибки и бизнес-события уходят во **второго бота** (опционально) — ты всегда знаешь, что происходит.

<div align="center">
  
  # VPN BOT ZALUPA это вам не бедолага, у нас хуже:)
  
</div>



## 💬 Поддержка и сообщество

Вопросы, баги, обсуждения, новости — всё в нашем Telegram-чате:

<div align="center">

[![Telegram Chat](https://img.shields.io/badge/💬_Вступить_в_чат-t.me/vpnbotzalupa-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/vpnbotzalupa)

**[👉 t.me/vpnbotzalupa](https://t.me/vpnbotzalupa))**

</div>

---

### 👤 Для пользователей

| Возможность | Описание |
|---|---|
| 🎁 **Пробный период** | 3 дня бесплатно с лимитом 30 ГБ |
| 💳 **Оплата картой/СБП** | Через Platega — привычный способ для РФ |
| ⭐ **Оплата Telegram Stars** | Без карты, прямо в Telegram |
| 🔗 **QR-код + копирование ссылки** | Для Happ / INCY и других клиентов |
| 📊 **Личный кабинет** | Баланс, история операций, статус подписок |
| 🔄 **Продление в 1 клик** | Продлить по тому же тарифу или сменить на другой |
| 🎁 **Реферальная программа** | 80% от первого пополнения приглашённого |
| 🔔 **Напоминания** | За 3 дня и за сутки до окончания подписки |
| ♻️ **Автопродление** | Если на балансе достаточно Stars |
| 📱 **Мультиустройство** | До 3 активных подписок на аккаунт |

### 🛠 Для администратора

- 👥 Постраничный список пользователей с балансами и подписками
- 🔎 Поиск по `telegram_id`
- 💰 Ручное начисление / списание баланса (с комментарием)
- 🔧 Управление подписками: отключить, удалить, `+7д`, `+30д`, `−7д`, `−30д`
- 🔒 Блокировка всех подписок пользователя одним кликом
- 📣 Массовая рассылка с отчётом о доставке
- 🩺 **Диагностика** — состояние Remnawave, SQLite, памяти, диска, uptime, фоновых функций
- 📊 Общая статистика по боту

### ⚙️ Технические

- 🧠 **AntiFlood** — защита от спама, двойных операций и быстрых кликов
- 🔐 **Идемпотентность платежей** — ни Stars, ни Platega не начислят дважды
- 🔄 **Фоновая сверка** SQLite ↔ Remnawave (опционально, с автофиксом)
- 📝 **Уведомления во второго бота** — все ошибки, покупки, триалы, рефералки
- 🎨 **Кастомный бренд** — задаётся одним полем `BRAND_NAME`

---

### Требования

| Компонент | Минимум | Рекомендуется |
|---|---|---|
| **Python** | 3.10 | 3.12 |
| **RAM** | 512 МБ | 1 ГБ |
| **Диск** | 2 ГБ | 10 ГБ |
| **Remnawave** | любая версия

### 🚀 Установка

```bash
# 1. Клонируй репозиторий
git clone https://github.com/director357/vpn_bot_zalupa.git
cd vpn_bot_zalupa

# 2. Запусти установщик
sudo bash install.sh
```

Installer:

1. Проверит Python (>= 3.10) и systemd
2. **Спросит** все переменные (токены, URL, бренд и т.д.)
3. Сгенерирует `.env` с правами `600`
4. Создаст venv и поставит зависимости из `requirements.txt`
5. Установит systemd-сервис `vpn-bot-remnawave`
6. Запустит бота и покажет статус

### Что спросит installer

```
=== Основное (обязательно) ===
  Токен бота от @BotFather*
  Твой Telegram ID (узнать у @userinfobot)*
  Username поддержки без @

=== Remnawave ===
  URL панели*
  API Token из Remnawave*
  UUID internal squad (опционально)
  Проверять SSL сертификат? [true/false]
  Публичный URL подписок

=== Бренд ===
  Название сервиса (попадёт в тексты бота)

Включить обязательную подписку на инфо-канал? [y/n]
Добавить кнопку «Купить Stars у партнёра»? [y/n]
Подключить оплату картой/СБП через Platega? [y/n]
Отправлять бизнес-уведомления во второго бота? [y/n]
... (юридические ссылки, фоновые процессы)
```

---

## ⚙️ Конфигурация

Все настройки — в `.env`. Полный список переменных смотри в [`.env.example`](.env.example).

### Обязательные

| Переменная | Описание | Где взять |
|---|---|---|
| `BOT_TOKEN` | Токен Telegram-бота | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `ADMIN_ID` | Твой Telegram ID (число) | [@userinfobot](https://t.me/userinfobot) |
| `REMNAWAVE_URL` | URL панели | `https://panel.example.com` |
| `REMNAWAVE_API_TOKEN` | API-токен | Remnawave → **API Tokens** |

### Опциональные

| Переменная | Назначение |
|---|---|
| `BRAND_NAME` | Название сервиса в текстах (по умолчанию `VPN`) |
| `SUPPORT_USERNAME` | Username поддержки без `@` |
| `INFO_CHANNEL_USERNAME` | Обязательная подписка на канал |
| `STARS_PARTNER_URL` | Кнопка «Купить Stars у партнёра» |
| `PLATEGA_*` | Интеграция с Platega (карта/СБП) |
| `ADMIN_NOTIFY_*` | Второй бот для уведомлений |
| `AUTO_RENEW_ENABLED` | Автопродление подписок |
| `RECONCILE_*` | Фоновая сверка SQLite ↔ Remnawave |

### Пример `.env`

```env
# Telegram
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_ID=123456789
SUPPORT_USERNAME=support_channel
BRAND_NAME=MyVPN

# Цены тарифов (в звёздах)
PLAN_TRIAL_TITLE=Пробный 3 дня
PLAN_TRIAL_DAYS=3
PLAN_TRIAL_PRICE=0

PLAN_WEEK_TITLE=7 дней
PLAN_WEEK_DAYS=7
PLAN_WEEK_PRICE=25

PLAN_MONTH_TITLE=30 дней
PLAN_MONTH_DAYS=30
PLAN_MONTH_PRICE=50

PLAN_2MONTH_TITLE=60 дней
PLAN_2MONTH_DAYS=60
PLAN_2MONTH_PRICE=100

# Пополнение через Stars
STARS_TOPUP_AMOUNTS=25,50,100,200

# Remnawave
REMNAWAVE_URL=https://panel.example.com
REMNAWAVE_API_TOKEN=rw_api_xxxxxxxxxxxxxxxxxxxxxxxx
REMNAWAVE_SSL_VERIFY=true

# Platega (опционально)
PLATEGA_MERCHANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
PLATEGA_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
PLATEGA_TOPUPS=100:50,200:100,400:200,800:400

# Фоновые процессы
BACKGROUND_WORKER_ENABLED=true
AUTO_RENEW_ENABLED=true
RECONCILE_ENABLED=true
RECONCILE_AUTO_FIX=false
```
## 🎮 Управление

### systemd-команды

```bash
# Статус
sudo systemctl status vpn-bot-remnawave

# Логи в реальном времени
sudo journalctl -u vpn-bot-remnawave -f

# Перезапуск (после правок .env)
sudo systemctl restart vpn-bot-remnawave

# Остановить / запустить
sudo systemctl stop vpn-bot-remnawave
sudo systemctl start vpn-bot-remnawave
```

## 💳 Интеграция Platega

Если хочешь принимать оплату картой/СБП, нужен **публичный HTTPS-эндпоинт** для callback'ов.

### 1. Настрой Nginx как reverse proxy

```nginx
server {
    listen 443 ssl http2;
    server_name pay.example.com;

    ssl_certificate     /etc/letsencrypt/live/pay.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pay.example.com/privkey.pem;

    location /platega/ {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 2. Пропиши callback URL в кабинете Platega

`https://pay.example.com/platega/callback`

### 3. Задай переменные в `.env`

```env
PLATEGA_MERCHANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
PLATEGA_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
PLATEGA_RETURN_URL=https://t.me/your_bot
PLATEGA_FAILED_URL=https://t.me/your_bot
PLATEGA_TOPUPS=100:50,200:100,400:200,800:400
```

### 4. Перезапусти бота

```bash
sudo systemctl restart vpn-bot-remnawave
```

Проверка callback-сервера:

```bash
curl http://127.0.0.1:8088/platega/health
# → {"ok": true, "platega": true}
```

---

## 🏗 Архитектура

```
zalupabot/
├── bot.py                  # Точка входа, Dispatcher, фоновые задачи
├── handlers.py             # Все aiogram-обработчики (меню, оплата, покупка)
├── keyboards.py            # Инлайн-клавиатуры
├── db.py                   # SQLite: схема, миграции, атомарные операции
├── remna.py                # API-адаптер Remnawave 
├── platega.py              # Platega: API, callback-сервер, идемпотентность
├── reconcile.py            # Фоновая сверка SQLite ↔ Remnawave
├── admin_notify.py         # Второй бот для уведомлений + логирование
├── protection.py           # AntiFlood middleware
├── price.py                # Платежи кнопочки шлепочки
├── install.sh              # Интерактивный установщик
├── requirements.txt        # Зависимости (фиксированные версии)
├── .env.example            # Шаблон конфигурации
└── README.md               # Этот файл
```

### Как обрабатывается покупка

```
User нажимает «Купить 30 дней»
    ↓
AntiFlood: проверка лимитов и активных операций
    ↓
handlers.buy_plan_callback
    ↓
Проверка баланса (до списания)
    ↓
remna.create_user (Remnawave)
    ↓
db.create_subscription (локальная запись)
    ↓
db.change_balance(-price) — списание Stars
    ↓
Отправка URL + QR пользователю
```

**Ключевое:** Stars списываются **только после** успешного создания в wave. Если панель упала — баланс не тронут.

### Как обрабатывается платёж Platega

```
User нажимает «Оплатить картой 200₽»
    ↓
platega.create_payment → получаем transaction_id + payment_url
    ↓
db.create_platega_payment (status=PENDING)
    ↓
User платит на стороне Platega
    ↓
Platega → POST /platega/callback (наш сервер)
    ↓
Проверка заголовков (X-MerchantId + X-Secret)
    ↓
db.process_platega_status (BEGIN IMMEDIATE)
    ├─ Проверка суммы и валюты
    ├─ Идемпотентность по transaction_id
    └─ Зачисление + реферальный бонус
```

**Ключевое:** `BEGIN IMMEDIATE` гарантирует, что параллельные callback'и не зачислят дважды.

---

## 🔒 Безопасность

Проект следует принципам «secure by default»:

| Аспект | Реализация |
|---|---|
| **Секреты** | Только через `.env`, никогда в коде. `.gitignore` защищает от коммита |
| **IDOR** | Все callback'и с `sub_id` проверяют владельца через `sub["user_id"] == user_row["id"]` |
| **XSS в Telegram** | Пользовательские данные в HTML-сообщениях проходят через `html.escape()` |
| **Идемпотентность Stars** | `telegram_payment_charge_id` UNIQUE + `BEGIN IMMEDIATE` |
| **Идемпотентность Platega** | `transaction_id` PRIMARY KEY + `BEGIN IMMEDIATE` |
| **AntiFlood** | Лимиты callback'ов/сообщений, cooldown, блокировка активных операций |
| **systemd hardening** | `NoNewPrivileges=true`, `PrivateTmp=true` |
| **`.env` права** | `chmod 600` при установке и перезапуске |

### Рекомендуется дополнительно
- 🔐 Регулярно делать бэкап `vpn_bot.db` (cron + `sqlite3 .backup`)

---
💰 Цены и тарифы

Все цены задаются в .env в звёздах (★) и автоматически применяются к:

    кнопкам «Купить подписку»

    кнопкам «Продлить» / «Перейти на платный»

    тексту меню покупки

    сумме списания с баланса при покупке/продлении

    суммам пополнения через Telegram Stars

    суммам пополнения через Platega

Меняешь цену в .env → перезапускаешь бота → UI обновился. Код править не нужно.

---

## 🧪 Тестирование

### Проверить, что бот поднялся

```bash
# Статус
sudo systemctl status vpn-bot-remnawave

# Логи (последние 50 строк)
sudo journalctl -u vpn-bot-remnawave -n 50 --no-pager
```

**Хороший вывод:**
```
Бот запущен.
Platega callback server: http://127.0.0.1:8088
Сверка SQLite ↔ Remnawave включена: interval=1800s batch=100 ...
Бизнес-уведомления включены
```

**Плохой вывод (ищи причины):**
```
Traceback (most recent call last):  ← ошибка в коде
ImportError                          ← нет зависимости
NameError                            ← опечатка
Conflict: terminated by other getUpdates request  ← запущено 2 процесса
```
---

## 🩺 Диагностика

### Бот не запускается — `Conflict: terminated by other getUpdates request`

Запущено **два** процесса одновременно.

```bash
ps aux | grep -E "[b]ot\.py"
kill <PID_старый>
sudo systemctl restart vpn-bot-remnawave
```

### `venv/bin/pip: cannot execute: required file not found`

Venv битый (переехал или создан под другую версию Python). `install.sh` сам это ловит и пересоздаёт. Вручную:

```bash
cd /root/vpn_bot_remnawave
mv venv venv.broken.$(date +%s)
sudo bash install.sh
```

### Platega не начисляет баланс

1. Проверь callback URL в кабинете Platega
2. Проверь, что nginx пробрасывает `/platega/`:

```bash
curl -i https://pay.example.com/platega/health
# → {"ok": true, "platega": true}
```

3. Проверь логи:

```bash
sudo journalctl -u vpn-bot-remnawave -f | grep -i platega
```

4. Проверь, что `PLATEGA_MERCHANT_ID` и `PLATEGA_SECRET` в `.env` совпадают с кабинетом

### `Remnawave request failed: HTTP 401`

`REMNAWAVE_API_TOKEN` истёк или неверный. Перегенерируй:

1. Remnawave → **API Tokens** → создай новый
2. Обнови `.env`
3. `sudo systemctl restart vpn-bot-remnawave`

### Бот не отвечает на кнопки

```bash
# Проверь, что процесс жив
sudo systemctl status vpn-bot-remnawave

# Проверь, что нет ошибок в логах
sudo journalctl -u vpn-bot-remnawave -n 100 --no-pager | grep -iE "error|exception"
```

Если видишь `Cannot connect to host api.telegram.org:443` — проверь firewall и DNS на VPS.


---

## ❓ FAQ


**Q: Как сменить бренд?**
A: В `.env` задай `BRAND_NAME=MyVPN` и перезапусти бота.

**Q: Как добавить новый тариф?**
A: В `handlers.py` найди словарь `PLANS` и добавь запись. Затем добавь кнопку в `keyboards.py` → `buy_menu_kb()`.

**Q: Куда деваются данные?**
A: Всё хранится в `vpn_bot.db` (SQLite) рядом с `bot.py`. Регулярно бэкапь:

```bash
sqlite3 vpn_bot.db ".backup '/root/backups/vpn_bot_$(date +%F).db'"
```

**Q: Как отключить проверку подписки на канал?**
A: Оставь `INFO_CHANNEL_USERNAME` пустым — проверка отключится.

**Q: Что делать, если потерял `.env`?**
A: Восстанови из бэкапа или пересоздай через `install.sh`. Токены придётся ввести заново.

---

## 📄 Лицензия

[MIT](LICENSE) — используй, форкай, продавай, модифицируй. Единственное условие — сохрани копирайт.

---

## 🙏 Благодарности

- [Remnawave](https://docs.rw) — за отличную VPN-панель
- Всем, кто форкает и делает бота лучше

---

<div align="center">

**Если проект оказался полезным — поставь ⭐ на GitHub**

[🐛 Нашёл баг?](https://github.com/director357/vpn_bot_zalupa/issues) · [💡 Есть идея?](https://github.com/director357/vpn_bot_zalupa/issues) · [🍴 Форкни](https://github.com/director357/vpn_bot_zalupa/fork)

[![Telegram Chat](https://img.shields.io/badge/💬_Вступить_в_чат-t.me/vpnbotzalupa-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/vpnbotzalupa)

**[👉 t.me/vpnbotzalupa](https://t.me/vpnbotzalupa)**
</div>
