# VPN Bot

Telegram-бот для продажи VPN-подписок через Remnawave.
Оплата: Telegram Stars + Platega (опционально).

## Установка

```bash
git clone <repo> vpn_bot
cd vpn_bot
sudo bash install.sh
```

Installer спросит все нужные переменные, создаст `.env`, поднимет systemd-сервис.

## Управление

```bash
systemctl {start|stop|restart|status} vpn-bot-remnawave
journalctl -u vpn-bot-remnawave -f
```
