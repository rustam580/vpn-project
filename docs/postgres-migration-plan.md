# Postgres Migration Plan

Цель: перейти с SQLite на Postgres без простоя, когда активная база приблизится к `300+` пользователей.

## Когда мигрировать
- В `admin stats` активных пользователей стабильно `>= 300`.
- Появляются блокировки/замедления SQLite при пиковых оплатах или рассылках.

## Минимальный план
1. Поднять Postgres (Managed или отдельный VM) и создать БД `vpn_bot`.
2. Снять backup SQLite (`/opt/backups/vpn-bot` уже настроен).
3. Экспортировать таблицы `users`, `devices`, `payments`, `referrals`, `known_chats`, `events`.
4. Импортировать в Postgres с сохранением:
   - уникальностей `users.marzban_username`, `devices.marzban_username`,
   - первичных ключей (`users.telegram_id`, `(provider, external_id)`, `(telegram_id, device_id)`).
5. Прогнать smoke-check:
   - `/start`, `/config`, `/buy`, `/check`, `/admin_stats`, `/ops`, `/broadcast_menu`.
6. Переключить приложение на новый DSN и перезапустить `vpn-bot`.
7. Оставить SQLite backup минимум на 7 дней.

## Что держать под контролем после миграции
- Время ответа команд бота.
- Ошибки платежей и статус `processing`.
- Количество `404` от Marzban в логах.

## Примечание
Текущая версия бота работает на SQLite. Документ фиксирует runbook для перехода, чтобы миграция была предсказуемой.
