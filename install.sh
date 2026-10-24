#!/usr/bin/env bash
#
# VPN Bot — интерактивный установщик.
#
# Спрашивает все нужные переменные, валидирует, генерирует .env,
# создаёт venv, ставит зависимости, настраивает systemd, запускает.
#
# Использование:
#   sudo bash install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR}"
SERVICE_NAME="vpn-bot-remnawave"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE="$INSTALL_DIR/.env"

if [[ -t 1 ]]; then
  RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
  BLUE=$'\033[0;34m'; CYAN=$'\033[0;36m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

log()  { echo "${BLUE}[*]${NC} $*"; }
ok()   { echo "${GREEN}[✓]${NC} $*"; }
warn() { echo "${YELLOW}[!]${NC} $*"; }
err()  { echo "${RED}[✗]${NC} $*" >&2; }
die()  { err "$*"; exit 1; }

# ---------- Проверки окружения ----------
check_root() {
  [[ $EUID -eq 0 ]] || die "Запусти от root: sudo bash $0"
}

check_python() {
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Не найден $PYTHON_BIN"
  local ver
  ver=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  local major="${ver%%.*}" minor="${ver##*.}"
  (( major > 3 )) || { (( major == 3 )) && (( minor >= 10 )); } \
    || die "Нужен Python >= 3.10, найден $ver"
  ok "Python $ver"
}

check_systemd() {
  command -v systemctl >/dev/null || die "systemd не найден"
  ok "systemd"
}

# ---------- Ввод ----------
declare -A ANSWERS

# ask <переменная> <вопрос> <дефолт> [обязательная:y/n] [секрет:y/n]
ask() {
  local var="$1" prompt="$2" default="${3:-}" required="${4:-n}" secret="${5:-n}"
  local value=""
  local input

  while true; do
    local hint=""
    [[ -n "$default" ]] && hint=" ${CYAN}[$default]${NC}"
    [[ "$required" == "y" ]] && hint+="${RED}*${NC}"

    if [[ "$secret" == "y" ]]; then
      read -r -s -p "  ${BOLD}${prompt}${NC}${hint}: " input
      echo
    else
      read -r -p "  ${BOLD}${prompt}${NC}${hint}: " input
    fi

    value="${input:-$default}"

    if [[ -z "$value" && "$required" == "y" ]]; then
      err "Это поле обязательно, попробуй ещё раз."
      continue
    fi

    ANSWERS["$var"]="$value"
    return 0
  done
}

# ask_yes_no <переменная> <вопрос> <дефолт:y/n>
ask_yes_no() {
  local var="$1" prompt="$2" default="${3:-n}"
  local input
  while true; do
    read -r -p "  ${BOLD}${prompt}${NC} ${CYAN}[${default}]${NC}: " input
    input="${input:-$default}"
    case "${input,,}" in
      y|yes|д|да) ANSWERS["$var"]="y"; return 0 ;;
      n|no|н|нет) ANSWERS["$var"]="n"; return 0 ;;
      *) err "Ответь y или n" ;;
    esac
  done
}

# ask_choice <переменная> <вопрос> <варианты через пробел> <дефолт>
ask_choice() {
  local var="$1" prompt="$2" choices="$3" default="$4"
  local input
  while true; do
    echo "  ${BOLD}${prompt}${NC}"
    echo "    Варианты: ${CYAN}${choices}${NC}"
    read -r -p "  Выбор ${CYAN}[${default}]${NC}: " input
    input="${input:-$default}"
    for c in $choices; do
      if [[ "$input" == "$c" ]]; then
        ANSWERS["$var"]="$c"
        return 0
      fi
    done
    err "Нет такого варианта: $input"
  done
}

# ---------- Валидация форматов ----------
validate_bot_token() {
  local v="${ANSWERS[BOT_TOKEN]}"
  [[ "$v" =~ ^[0-9]{6,12}:[A-Za-z0-9_-]{20,}$ ]] \
    || die "BOT_TOKEN не похож на токен Telegram (ожидается 123456:ABC...)"
}

validate_admin_id() {
  local v="${ANSWERS[ADMIN_ID]}"
  [[ "$v" =~ ^[0-9]+$ ]] || die "ADMIN_ID должен быть числом (telegram id)"
}

validate_url() {
  local v="$1" name="$2"
  [[ "$v" =~ ^https?:// ]] || die "$name должен начинаться с http:// или https://"
}

# Публичный username канала/юзера: 4-32 символа, латиница/цифры/_
validate_username() {
  local v="$1" name="$2" allow_empty="${3:-y}"
  if [[ -z "$v" && "$allow_empty" == "y" ]]; then
    return 0
  fi
  [[ "$v" =~ ^[A-Za-z0-9_]{4,32}$ ]] \
    || die "$name: только латиница/цифры/_, 4-32 символа, без @"
}

# PLATEGA_TOPUPS: RUB:STARS через запятую
validate_topups() {
  local v="${ANSWERS[PLATEGA_TOPUPS]}"
  [[ "$v" =~ ^[0-9]+:[0-9]+(,[0-9]+:[0-9]+)*$ ]] \
    || die "PLATEGA_TOPUPS: формат RUB:STARS,RUB:STARS (напр. 100:25,200:50,400:100,800:200)"
}

# ---------- Диалог: основное ----------
ask_core() {
  echo
  echo "${BOLD}=== Основное (обязательно) ===${NC}"
  echo

  ask BOT_TOKEN   "Токен бота от @BotFather" "" y y
  validate_bot_token

  ask ADMIN_ID    "Твой Telegram ID (узнать у @userinfobot)" "" y n
  validate_admin_id

  ask SUPPORT_USERNAME "Username поддержки без @ (Enter = пропустить)" "" n n
  validate_username "${ANSWERS[SUPPORT_USERNAME]}" "SUPPORT_USERNAME" y

  echo
  echo "${BOLD}=== Remnawave ===${NC}"
  echo

  ask REMNAWAVE_URL        "URL панели (напр. https://panel.example.com)" "" y n
  validate_url "${ANSWERS[REMNAWAVE_URL]}" "REMNAWAVE_URL"

  ask REMNAWAVE_API_TOKEN  "API Token из Remnawave" "" y y

  ask REMNAWAVE_INTERNAL_SQUAD_UUID \
      "UUID internal squad (Enter = пропустить)" "" n n

  ask_choice REMNAWAVE_SSL_VERIFY \
      "Проверять SSL сертификат Remnawave?" "true false" "true"

  ask REMNAWAVE_SUBSCRIPTION_BASE_URL \
      "Публичный URL подписок (Enter = = REMNAWAVE_URL)" \
      "${ANSWERS[REMNAWAVE_URL]}" n n
}

# ---------- Автоопределение версии Remnawave ----------
detect_remnawave_generation() {
  echo
  echo "${BOLD}=== Определение версии Remnawave ===${NC}"
  echo

  local url="${ANSWERS[REMNAWAVE_URL]}"
  local token="${ANSWERS[REMNAWAVE_API_TOKEN]}"
  local verify="${ANSWERS[REMNAWAVE_SSL_VERIFY]}"

  log "Запрашиваю /api/system/metadata…"

  local response
  response=$(
    "$PYTHON_BIN" - "$url" "$token" "$verify" <<'PYEOF' 2>/dev/null
import json
import ssl
import sys
import urllib.request

url, token, verify = sys.argv[1], sys.argv[2], sys.argv[3]
endpoint = url.rstrip("/") + "/api/system/metadata"

req = urllib.request.Request(
    endpoint,
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    },
)
ctx = None if verify == "true" else ssl._create_unverified_context()

try:
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
        response_obj = payload.get("response") if isinstance(payload, dict) else payload
        version = ""
        if isinstance(response_obj, dict):
            version = str(response_obj.get("version") or "")
        print(version)
except Exception:
    sys.exit(1)
PYEOF
  )

  local generation=""
  local detected_version=""

  if [[ -n "$response" ]]; then
    detected_version="$response"
    local major="${detected_version%%.*}"
    if [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 3 )); then
      generation="3.x+"
    else
      generation="2.x"
    fi
    ok "Remnawave версия: ${detected_version} → поколение API: ${generation}"
  else
    warn "Не удалось определить версию автоматически."
    warn "Возможные причины: нет сети, неверный API-токен, SSL-ошибка, панель недоступна."
    warn "Можно указать поколение вручную."
    ANSWERS[REMNAWAVE_API_GENERATION]=""
    ask_choice REMNAWAVE_API_GENERATION \
        "Поколение API Remnawave (2.x = uuid, 3.x+ = numeric id):" \
        "2.x 3.x+" "2.x"
    return 0
  fi

  ANSWERS[REMNAWAVE_API_GENERATION]="$generation"

  ask_yes_no CONFIRM_GENERATION \
      "Поколение API ${generation} определено верно?" "y"

  if [[ "${ANSWERS[CONFIRM_GENERATION]}" != "y" ]]; then
    ask_choice REMNAWAVE_API_GENERATION \
        "Укажи поколение API вручную (2.x = uuid, 3.x+ = numeric id):" \
        "2.x 3.x+" "$generation"
  fi
}

# ---------- Диалог: бренд ----------
ask_brand() {
  echo
  echo "${BOLD}=== Бренд ===${NC}"
  echo
  ask BRAND_NAME "Название сервиса (бренд, попадёт в тексты бота)" \
      "VPN" n n
}

# ---------- Диалог: подписка на канал ----------
ask_info_channel() {
  echo
  ask_yes_no ENABLE_INFO_CHANNEL \
      "Включить обязательную подписку на инфо-канал?" "n"

  if [[ "${ANSWERS[ENABLE_INFO_CHANNEL]}" == "y" ]]; then
    ask INFO_CHANNEL_USERNAME "Username канала без @ (напр. my_channel)" "" y n
    validate_username "${ANSWERS[INFO_CHANNEL_USERNAME]}" "INFO_CHANNEL_USERNAME" n

    ask INFO_CHANNEL_URL "URL канала (Enter = https://t.me/<username>)" \
        "https://t.me/${ANSWERS[INFO_CHANNEL_USERNAME]}" n n
    validate_url "${ANSWERS[INFO_CHANNEL_URL]}" "INFO_CHANNEL_URL"
  else
    ANSWERS[INFO_CHANNEL_USERNAME]=""
    ANSWERS[INFO_CHANNEL_URL]=""
  fi
}

# ---------- Диалог: партнёрская ссылка Stars ----------
ask_stars_partner() {
  echo
  ask_yes_no ENABLE_STARS_PARTNER \
      "Добавить кнопку «Купить Stars у партнёра» в главное меню?" "n"

  if [[ "${ANSWERS[ENABLE_STARS_PARTNER]}" == "y" ]]; then
    ask STARS_PARTNER_URL \
        "URL партнёрской ссылки на Stars (напр. https://t.me/starsov?start=rXXX)" \
        "" y n
    validate_url "${ANSWERS[STARS_PARTNER_URL]}" "STARS_PARTNER_URL"
  else
    ANSWERS[STARS_PARTNER_URL]=""
  fi
}

# ---------- Диалог: Platega ----------
ask_platega() {
  echo
  ask_yes_no ENABLE_PLATEGA "Подключить оплату картой/СБП через Platega?" "n"

  if [[ "${ANSWERS[ENABLE_PLATEGA]}" == "y" ]]; then
    ask PLATEGA_MERCHANT_ID "Merchant ID из Platega" "" y n
    ask PLATEGA_SECRET      "Secret из Platega" "" y y

    ask PLATEGA_RETURN_URL  "URL возврата после оплаты (напр. https://t.me/your_bot)" "" y n
    validate_url "${ANSWERS[PLATEGA_RETURN_URL]}" "PLATEGA_RETURN_URL"

    ask PLATEGA_FAILED_URL  "URL при ошибке (Enter = = return URL)" \
        "${ANSWERS[PLATEGA_RETURN_URL]}" n n
    validate_url "${ANSWERS[PLATEGA_FAILED_URL]}" "PLATEGA_FAILED_URL"

    ask PLATEGA_TOPUPS "Суммы (RUB:STARS через запятую)" \
        "100:25,200:50,400:100,800:200" n n
    validate_topups
  else
    ANSWERS[PLATEGA_MERCHANT_ID]=""
    ANSWERS[PLATEGA_SECRET]=""
    ANSWERS[PLATEGA_RETURN_URL]=""
    ANSWERS[PLATEGA_FAILED_URL]=""
    ANSWERS[PLATEGA_TOPUPS]="100:25,200:50,400:100,800:200"
  fi
}

# ---------- Диалог: админ-уведомления ----------
ask_admin_notify() {
  echo
  ask_yes_no ENABLE_ADMIN_NOTIFY \
      "Отправлять бизнес-уведомления во второго бота?" "n"

  if [[ "${ANSWERS[ENABLE_ADMIN_NOTIFY]}" == "y" ]]; then
    ask ADMIN_NOTIFY_BOT_TOKEN "Токен второго бота (от @BotFather)" "" y y
    ask ADMIN_NOTIFY_CHAT_ID   "Chat ID для уведомлений (твой telegram_id или -100...)" "" y n
  else
    ANSWERS[ADMIN_NOTIFY_BOT_TOKEN]=""
    ANSWERS[ADMIN_NOTIFY_CHAT_ID]=""
  fi
}

# ---------- Диалог: юр. ссылки ----------
ask_legal() {
  echo
  echo "${BOLD}=== Юридические ссылки (Enter = оставить дефолт) ===${NC}"
  echo
  ask LEGAL_PRIVACY_URL "Политика конфиденциальности" \
      "https://telegra.ph/" n n
  validate_url "${ANSWERS[LEGAL_PRIVACY_URL]}" "LEGAL_PRIVACY_URL"

  ask LEGAL_TERMS_URL   "Пользовательское соглашение" \
      "https://telegra.ph/" n n
  validate_url "${ANSWERS[LEGAL_TERMS_URL]}" "LEGAL_TERMS_URL"
}

# ---------- Диалог: фоновые процессы ----------
ask_background() {
  echo
  echo "${BOLD}=== Фоновые процессы ===${NC}"
  echo

  ask_yes_no BACKGROUND_WORKER_ENABLED \
      "Включить фоновую проверку подписок (напоминания, автопродление)?" "y"

  ask_yes_no AUTO_RENEW_ENABLED \
      "Автопродление подписок при достаточном балансе?" "y"

  if [[ "${ANSWERS[AUTO_RENEW_ENABLED]}" == "y" ]]; then
    ask_yes_no AUTO_RENEW_FORCE_MONTH \
        "Автопродление ВСЕГДА на 30 дней (даже если тариф 7/60 дней)?" "n"
  else
    ANSWERS[AUTO_RENEW_FORCE_MONTH]="n"
  fi

  ask_yes_no RECONCILE_ENABLED \
      "Включить фоновую сверку SQLite ↔ Remnawave?" "y"

  if [[ "${ANSWERS[RECONCILE_ENABLED]}" == "y" ]]; then
    ask_yes_no RECONCILE_AUTO_FIX \
        "Автоматически исправлять рассинхронизацию (ОПАСНО: может включить отключённые вручную подписки)?" "n"
  else
    ANSWERS[RECONCILE_AUTO_FIX]="n"
  fi
}

# ---------- Генерация .env ----------
generate_env() {
  log "Генерирую $ENV_FILE"

  local bg_worker auto_renew force_month
  local recon_enabled recon_autofix

  [[ "${ANSWERS[BACKGROUND_WORKER_ENABLED]}" == "y" ]] && bg_worker="true" || bg_worker="false"
  [[ "${ANSWERS[AUTO_RENEW_ENABLED]}"        == "y" ]] && auto_renew="true" || auto_renew="false"
  [[ "${ANSWERS[AUTO_RENEW_FORCE_MONTH]}"    == "y" ]] && force_month="true" || force_month="false"
  [[ "${ANSWERS[RECONCILE_ENABLED]}"         == "y" ]] && recon_enabled="true" || recon_enabled="false"
  [[ "${ANSWERS[RECONCILE_AUTO_FIX]}"        == "y" ]] && recon_autofix="true" || recon_autofix="false"

  # Пишем через printf, чтобы символы $ и ` в значениях не интерпретировались.
  {
    printf '# Сгенерировано install.sh %s\n\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    printf '# ============================================================\n'
    printf '#  Telegram Bot\n'
    printf '# ============================================================\n'
    printf 'BOT_TOKEN=%s\n'           "${ANSWERS[BOT_TOKEN]}"
    printf 'ADMIN_ID=%s\n'            "${ANSWERS[ADMIN_ID]}"
    printf 'SUPPORT_USERNAME=%s\n'    "${ANSWERS[SUPPORT_USERNAME]}"
    printf 'BRAND_NAME=%s\n'          "${ANSWERS[BRAND_NAME]}"
    printf '\n'

    printf '# ============================================================\n'
    printf '#  Обязательная подписка на канал\n'
    printf '# ============================================================\n'
    printf 'INFO_CHANNEL_USERNAME=%s\n' "${ANSWERS[INFO_CHANNEL_USERNAME]}"
    printf 'INFO_CHANNEL_URL=%s\n'      "${ANSWERS[INFO_CHANNEL_URL]}"
    printf '\n'

    printf '# ============================================================\n'
    printf '#  Юридические ссылки\n'
    printf '# ============================================================\n'
    printf 'LEGAL_PRIVACY_URL=%s\n' "${ANSWERS[LEGAL_PRIVACY_URL]}"
    printf 'LEGAL_TERMS_URL=%s\n'   "${ANSWERS[LEGAL_TERMS_URL]}"
    printf '\n'

    printf '# ============================================================\n'
    printf '#  Партнёрская ссылка Stars\n'
    printf '# ============================================================\n'
    printf 'STARS_PARTNER_URL=%s\n' "${ANSWERS[STARS_PARTNER_URL]}"
    printf '\n'

    printf '# ============================================================\n'
    printf '#  Remnawave\n'
    printf '# ============================================================\n'
    printf 'REMNAWAVE_URL=%s\n'                    "${ANSWERS[REMNAWAVE_URL]}"
    printf 'REMNAWAVE_API_TOKEN=%s\n'              "${ANSWERS[REMNAWAVE_API_TOKEN]}"
    printf 'REMNAWAVE_INTERNAL_SQUAD_UUID=%s\n'    "${ANSWERS[REMNAWAVE_INTERNAL_SQUAD_UUID]}"
    printf 'REMNAWAVE_SSL_VERIFY=%s\n'             "${ANSWERS[REMNAWAVE_SSL_VERIFY]}"
    printf 'REMNAWAVE_API_GENERATION=%s\n'         "${ANSWERS[REMNAWAVE_API_GENERATION]}"
    printf 'REMNAWAVE_SUBSCRIPTION_BASE_URL=%s\n'  "${ANSWERS[REMNAWAVE_SUBSCRIPTION_BASE_URL]}"
    printf '\n'

    printf '# ============================================================\n'
    printf '#  Platega\n'
    printf '# ============================================================\n'
    printf 'PLATEGA_MERCHANT_ID=%s\n' "${ANSWERS[PLATEGA_MERCHANT_ID]}"
    printf 'PLATEGA_SECRET=%s\n'      "${ANSWERS[PLATEGA_SECRET]}"
    printf 'PLATEGA_API_BASE=%s\n'    "https://app.platega.io"
    printf 'PLATEGA_TOPUPS=%s\n'      "${ANSWERS[PLATEGA_TOPUPS]}"
    printf 'PLATEGA_RETURN_URL=%s\n'  "${ANSWERS[PLATEGA_RETURN_URL]}"
    printf 'PLATEGA_FAILED_URL=%s\n'  "${ANSWERS[PLATEGA_FAILED_URL]}"
    printf 'PLATEGA_CALLBACK_HOST=%s\n' "127.0.0.1"
    printf 'PLATEGA_CALLBACK_PORT=%s\n' "8088"
    printf '\n'

    printf '# ============================================================\n'
    printf '#  Админ-уведомления\n'
    printf '# ============================================================\n'
    printf 'ADMIN_NOTIFY_BOT_TOKEN=%s\n' "${ANSWERS[ADMIN_NOTIFY_BOT_TOKEN]}"
    printf 'ADMIN_NOTIFY_CHAT_ID=%s\n'   "${ANSWERS[ADMIN_NOTIFY_CHAT_ID]}"
    printf '\n'

    printf '# ============================================================\n'
    printf '#  Фоновые процессы\n'
    printf '# ============================================================\n'
    printf 'BACKGROUND_WORKER_ENABLED=%s\n' "$bg_worker"
    printf 'AUTO_RENEW_ENABLED=%s\n'        "$auto_renew"
    printf 'AUTO_RENEW_FORCE_MONTH=%s\n'    "$force_month"
    printf 'RECONCILE_ENABLED=%s\n'         "$recon_enabled"
    printf 'RECONCILE_AUTO_FIX=%s\n'        "$recon_autofix"
  } > "$ENV_FILE"

  chmod 600 "$ENV_FILE"
  ok ".env создан (права 600)"
}

# ---------- Сводка перед записью ----------
show_summary() {
  echo
  echo "${BOLD}=== Сводка ===${NC}"
  echo "  BRAND_NAME:           ${ANSWERS[BRAND_NAME]}"
  echo "  BOT_TOKEN:            ${ANSWERS[BOT_TOKEN]:0:10}…"
  echo "  ADMIN_ID:             ${ANSWERS[ADMIN_ID]}"
  echo "  SUPPORT_USERNAME:     ${ANSWERS[SUPPORT_USERNAME]:-(не задан)}"
  echo "  REMNAWAVE_URL:        ${ANSWERS[REMNAWAVE_URL]}"
  echo "  REMNAWAVE_TOKEN:      ${ANSWERS[REMNAWAVE_API_TOKEN]:0:10}…"
  echo "  SSL verify:           ${ANSWERS[REMNAWAVE_SSL_VERIFY]}"
  echo "  Remnawave поколение:  ${ANSWERS[REMNAWAVE_API_GENERATION]}"
  echo "  Инфо-канал:           ${ANSWERS[INFO_CHANNEL_USERNAME]:-(выкл)}"
  echo "  Партнёрская Stars:    ${ANSWERS[STARS_PARTNER_URL]:-(выкл)}"
  echo "  Platega:              $( [[ -n "${ANSWERS[PLATEGA_MERCHANT_ID]}" ]] && echo "вкл (${ANSWERS[PLATEGA_MERCHANT_ID]})" || echo "выкл" )"
  echo "  Админ-уведомления:    $( [[ -n "${ANSWERS[ADMIN_NOTIFY_BOT_TOKEN]}" ]] && echo "вкл" || echo "выкл" )"
  echo "  Фоновый worker:       ${ANSWERS[BACKGROUND_WORKER_ENABLED]}"
  echo "  Автопродление:        ${ANSWERS[AUTO_RENEW_ENABLED]}"
  echo "  Автопрод. на 30 дней: ${ANSWERS[AUTO_RENEW_FORCE_MONTH]}"
  echo "  Сверка:               ${ANSWERS[RECONCILE_ENABLED]}"
  echo "  Автоисправление:      ${ANSWERS[RECONCILE_AUTO_FIX]}"
  echo

  ask_yes_no CONFIRM "Всё верно, записываем .env?" "y"
  [[ "${ANSWERS[CONFIRM]}" == "y" ]] || die "Отменено пользователем."
}

# ---------- venv + зависимости ----------
setup_venv() {
  cd "$INSTALL_DIR"

  [[ -f requirements.txt ]] || die "Нет requirements.txt в $INSTALL_DIR"

  # ---- Проверка целостности существующего venv ----
  # Venv не переносимый: если папку скопировали/переименовали,
  # shebang в bin/pip указывает на несуществующий путь.
  if [[ -d venv ]]; then
    local venv_ok=1

    if ! ./venv/bin/pip --version >/dev/null 2>&1; then
      venv_ok=0
      warn "venv/bin/pip не запускается"
    fi

    if [[ "$venv_ok" == "1" ]] && ! ./venv/bin/python -c "import sys" >/dev/null 2>&1; then
      venv_ok=0
      warn "venv/bin/python не запускается"
    fi

    if [[ "$venv_ok" == "1" ]]; then
      local pip_shebang
      pip_shebang=$(head -1 ./venv/bin/pip 2>/dev/null || echo "")
      local expected_shebang="#!${INSTALL_DIR}/venv/bin/python"

      if [[ "$pip_shebang" != "$expected_shebang"* ]]; then
        venv_ok=0
        warn "shebang pip указывает не туда: $pip_shebang"
        warn "Ожидалось начало: $expected_shebang"
      fi
    fi

    if [[ "$venv_ok" == "0" ]]; then
      local backup="venv.broken.$(date +%s)"
      warn "venv нерабочий — пересоздаю. Старый сохранён в $backup"
      mv venv "$backup"
    fi
  fi

  if [[ -d venv ]]; then
    log "venv рабочий — обновляю зависимости"
  else
    log "Создаю venv"
    "$PYTHON_BIN" -m venv venv
  fi

  ./venv/bin/pip install --upgrade pip >/dev/null
  log "Ставлю зависимости (может занять минуту)…"
  ./venv/bin/pip install -r requirements.txt
  ok "Зависимости установлены"
}

# ---------- systemd ----------
setup_systemd() {
  local unit="/etc/systemd/system/${SERVICE_NAME}.service"

  log "Создаю systemd unit → $unit"

  cat > "$unit" <<EOF
[Unit]
Description=Telegram VPN Bot - Remnawave Production
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME" >/dev/null
  ok "systemd unit установлен"
}

# ---------- Запуск ----------
start_service() {
  log "Запускаю $SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  sleep 3

  if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Сервис запущен"
  else
    err "Сервис не запустился. Логи:"
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager
    exit 1
  fi
}

# ---------- main ----------
main() {
  echo
  echo "${BOLD}========================================${NC}"
  echo "${BOLD}  VPN Bot — установка${NC}"
  echo "${BOLD}  Директория: $INSTALL_DIR${NC}"
  echo "${BOLD}========================================${NC}"

  check_root
  check_python
  check_systemd

  SKIP_ENV=0
  if [[ -f "$ENV_FILE" ]]; then
    echo
    warn ".env уже существует: $ENV_FILE"
    ask_yes_no OVERWRITE_ENV "Перезаписать его?" "n"
    if [[ "${ANSWERS[OVERWRITE_ENV]}" != "y" ]]; then
      log "Оставляю .env как есть, пропускаю мастер настройки"
      SKIP_ENV=1
    else
      cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%s)"
      log "Старый .env сохранён как ${ENV_FILE}.bak.*"
    fi
  fi

  if [[ "${SKIP_ENV}" != "1" ]]; then
    ask_core
    detect_remnawave_generation
    ask_brand
    ask_info_channel
    ask_stars_partner
    ask_platega
    ask_admin_notify
    ask_legal
    ask_background
    show_summary
    generate_env
  fi

  setup_venv
  setup_systemd
  start_service

  echo
  echo "${GREEN}${BOLD}=== Установка завершена ===${NC}"
  echo
  echo "  Логи в реальном времени:"
  echo "    journalctl -u $SERVICE_NAME -f"
  echo
  echo "  Управление:"
  echo "    systemctl {start|stop|restart|status} $SERVICE_NAME"
  echo
  echo "  Файл конфигурации:"
  echo "    $ENV_FILE"
  echo
}

main "$@"
