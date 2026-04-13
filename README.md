# VPN Bot + Marzban

Бот выдает VPN-подписки из Marzban и умеет продлевать доступ после оплаты.

## Что есть в боте
- `🔑 Получить подписку`
- `💳 Купить доступ` (CryptoBot auto / Карта YooKassa)
- `📊 Мой статус` (срок, сколько осталось, трафик)
- `❓ FAQ`
- `🆘 Поддержка`

Админ-команды:
- `/admin_stats`
- `/grant <telegram_id> <days> <gb>`
- `/disable <telegram_id>`
- `/link <telegram_id> <marzban_username>`

## Быстрый запуск
```bash
cd /opt/vpn-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

## Запуск как сервис
```bash
cp deploy/vpn-bot.service.example /etc/systemd/system/vpn-bot.service
systemctl daemon-reload
systemctl enable --now vpn-bot
systemctl status vpn-bot
journalctl -u vpn-bot -f
```

## Важные переменные `.env`
- `TRIAL_DAYS=1`
- `TRIAL_GB=0` (безлимит)
- `PAY_DAYS=30`
- `PAY_GB=0` (безлимит)
- `PAY_RUB=99`

Поддержка:
- `SUPPORT_USERNAME=your_support_username`
- `SUPPORT_TEXT=Напишите нам, поможем с подключением и оплатой.`

Оплата:
- CryptoBot: `CRYPTOBOT_TOKEN`
- Автопроверка CryptoBot: `CRYPTOBOT_POLL_SECONDS` (по умолчанию 45 сек)
- Карта YooKassa:
  - `YOOKASSA_SHOP_ID`
  - `YOOKASSA_SECRET_KEY`
  - `YOOKASSA_RETURN_URL`
- Anti-stuck для `processing`: `PAYMENT_PROCESSING_REQUEUE_SECONDS` (по умолчанию 600 сек)

Миграция на подписки (дожим):
- `SUB_MIGRATION_REMINDER_ENABLED` — включить мягкие напоминания не перешедшим
- `SUB_MIGRATION_REMINDER_INTERVAL_SEC` — период проверки
- `SUB_MIGRATION_REMINDER_COOLDOWN_HOURS` — защита от повторных напоминаний
- `SUBSCRIPTION_HITS_RETENTION_DAYS` — хранение статистики перехода на подписки (дней)

## Панель Marzban
Открывайте:
- `http://<server_ip>/dashboard/`

Если забыли админ-доступ:
```bash
cd /opt/marzban
docker compose exec marzban marzban cli admin create --sudo
```

## Обновление бота на сервере
1. Замените `bot.py` и `.env` при необходимости.
2. Перезапустите:
```bash
systemctl restart vpn-bot
journalctl -u vpn-bot -n 50 --no-pager
```

## Опционально: Gateway для подписок
Если Marzban отдает дубли в подписке, включите локальный gateway:
- сервис: `deploy/vpn-sub-gateway.service.example`
- описание и команды: `docs/subscription-gateway.md`

## Опционально: Website
- Статический сайт лежит в `site/`
- Деплой и Caddy-конфиг: `docs/website.md`
