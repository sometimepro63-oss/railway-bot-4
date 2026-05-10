# Telegram Paywall Bot (Prodamus + Telegram Group)

Production-ready бот для продажи доступа в закрытую Telegram-группу через Prodamus.

## Возможности

- /start, /buy, /profile, /help
- Админ-команды: /admin_stats, /admin_user, /admin_extend, /admin_revoke
- Платёжная ссылка Prodamus с подписью
- Webhook `/webhooks/prodamus` с проверкой подписи и идемпотентностью
- Подписки в PostgreSQL (SQLAlchemy async + asyncpg)
- Авто-истечение подписок раз в час с удалением из группы (ban/unban) и уведомлением
- Оплата через inline-кнопку в Telegram (без показа длинной ссылки текстом)
- Короткая ссылка оплаты через `GET /pay/{order_id}` (редирект на Prodamus)

## Требования

- Python 3.11+
- PostgreSQL
- Бот — администратор закрытой группы и имеет право создавать invite-ссылки

## Конфигурация

1) Скопируйте `.env.example` в `.env` и заполните:

- `BOT_TOKEN`
- `PRODAMUS_SECRET_KEY`
- `PRODAMUS_PAYMENT_PAGE_URL` (полная рабочая ссылка платёжной формы Prodamus; может содержать `orderId` или `paymentLinkId`, например `https://link.payform.ru/?orderId=...`)
- `WEBHOOK_BASE_URL` (публичный base url вашего API, например `https://your-app.up.railway.app`)
- `GROUP_ID` (id группы, обычно отрицательный)
- `ADMIN_IDS` (через запятую)
- `DATABASE_URL`
- `LIFETIME_ACCESS`
- `LOG_LEVEL` (опционально)

2) В Prodamus укажите `urlNotification` на `https://<WEBHOOK_BASE_URL>/webhooks/prodamus`.

## Оплата (короткая ссылка)

Бот показывает кнопку оплаты с короткой ссылкой вида `https://<WEBHOOK_BASE_URL>/pay/<order_id>`.
Этот endpoint делает `302` редирект на полный URL Prodamus, поэтому Telegram показывает аккуратный домен вашего приложения.

Ссылка на оплату действует ограниченное время (переменная `PAYMENT_LINK_TTL_MINUTES`, по умолчанию 30 минут). Если ссылка устарела, нужно нажать “Купить доступ” заново.

Важно: `PRODAMUS_PAYMENT_PAGE_URL` может содержать `orderId` (идентификатор платёжной формы) или `paymentLinkId`, но не должен содержать параметры заказа (`order_id`, `products`, `signature` и т.п.). Если они есть, приложение выкинет их из query перед формированием ссылки.

## Как запустить бота

Бот запускается внутри FastAPI приложения в `startup` (через `Dispatcher.start_polling` в отдельной asyncio-задаче). Это значит:

- один процесс `uvicorn app.main_api:app` поднимает и API, и polling бота
- для Railway важно держать ровно 1 инстанс сервиса (иначе будет несколько polling одновременно)

## Как добавить бота в группу и дать права

1) Добавьте бота в закрытую группу
2) Выдайте боту права администратора с разрешением:

- приглашать пользователей по ссылке (создание invite-ссылок)
- банить пользователей (для удаления при истечении подписки)

## Где взять GROUP_ID

Самый простой способ:

1) Добавьте бота в группу
2) Напишите в группу любое сообщение
3) Откройте `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` и возьмите `chat.id`

Для групп/супергрупп `chat.id` обычно отрицательный.

## Установка и запуск (локально)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Миграции:

```bash
alembic upgrade head
```

Запуск:

```bash
uvicorn app.main_api:app --host 0.0.0.0 --port 8000
```

## Railway

1) Создайте PostgreSQL в Railway и возьмите `DATABASE_URL` или `DATABASE_PUBLIC_URL`
2) В Variables добавьте значения из `.env.example`
3) Убедитесь, что `WEBHOOK_BASE_URL` соответствует публичному домену Railway (без завершающего `/`)
4) В Prodamus установите `urlNotification` = `https://<WEBHOOK_BASE_URL>/webhooks/prodamus`

Если Railway даёт URL вида `postgresql://...`, код сам преобразует его в `postgresql+asyncpg://...`.

Railway env diagnostics enabled.

## Пожизненный доступ

Если `LIFETIME_ACCESS=true`, то доступ выдаётся навсегда:

- подписка создаётся со статусом `active` и `expires_at = NULL`
- задача истечения подписок не трогает таких пользователей
- `ACCESS_DAYS` используется только если `LIFETIME_ACCESS=false`

## Деплой на Railway

1) Создать GitHub repo
2) Загрузить код
3) Подключить repo в Railway
4) Добавить PostgreSQL в Railway
5) Скопировать `DATABASE_URL` из Railway PostgreSQL
6) Добавить Variables:

- `BOT_TOKEN`
- `PRODAMUS_SECRET_KEY`
- `PRODAMUS_PAYMENT_PAGE_URL`
- `WEBHOOK_BASE_URL`
- `GROUP_ID`
- `ADMIN_IDS`
- `DATABASE_URL`
- `PRODUCT_NAME`
- `PRODUCT_PRICE`
- `ACCESS_DAYS`
- `INVITE_LINK_EXPIRE_MINUTES`

7) Задеплоить
8) Проверить `https://ТВОЙ-ДОМЕН.up.railway.app/healthz`
9) В Prodamus указать `urlNotification`:

`https://ТВОЙ-ДОМЕН.up.railway.app/webhooks/prodamus`

10) В Telegram добавить бота админом в закрытую группу

## Важно про 1 инстанс

Бот работает через polling внутри FastAPI процесса. На Railway должен быть только 1 running instance. Если запустить несколько копий, будет конфликт polling.

## Тесты подписи Prodamus

```bash
pytest -q
```
