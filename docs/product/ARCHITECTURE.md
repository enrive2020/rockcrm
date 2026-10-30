# Архитектура кода — Подача.AI

Код лежит в `product/`. Это монорепозиторий на npm workspaces, TypeScript (strict, ESM, NodeNext) и Node 20+. Документ описывает, как устроен код. Что именно строим, описано в `prd.md`, `domain-model.md`, `api-contract.md`, `ai-agents.md`, `integrations.md` и `compliance-security.md`.

## 1. Пакеты и зависимости

```
apps/api ──► packages/{shared, db, ai, integrations}
apps/bots ─► packages/{shared, ai}
apps/guest, apps/admin ─► только HTTP API (/v1)
packages/db, ai, integrations ─► packages/shared
packages/shared ─► zod
```

Правила:
- Пакеты не импортируют приложения.
- `shared` ни от чего внутреннего не зависит.
- Фронтенды общаются с бэкендом только через HTTP по `openapi.yaml`.
- Ядро не знает конкретных вендоров и работает только через интерфейсы `PosAdapter`, `PaymentAdapter`, `LLMProvider` и `ChannelAdapter`.

## 2. Модули API и владельцы

Каждый модуль живёт в своей папке `apps/api/src/modules/<name>/`. Его `index.ts` по умолчанию экспортирует плагин Fastify (`FastifyPluginAsync`). `app.ts` загружает все папки автоматически в алфавитном порядке с префиксом `/v1`. Регистрировать модуль вручную не нужно. Внутри папки модуль раскладывается так: `routes.ts`, `service.ts`, `repo.ts`, `schemas.ts`. Тесты лежат в `apps/api/test/<name>.test.ts`.

| Модуль | Сущности (domain-model.md) | Владелец (зона) | Релиз |
|---|---|---|---|
| `health` | — | platform | есть |
| `auth` | app_user, membership | platform | MVP |
| `tenancy` | organization, brand, venue, zone, venue_table, qr_code | platform | MVP |
| `menu` | menu*, item_*, modifier*, allergen, stop_list_entry, translation | menu | MVP |
| `guest` | guest, guest_contact, consent, guest_session | crm/compliance | MVP |
| `orders` | order, order_item*, order_status_history | ordering | MVP |
| `payments` | payment, payment_split, refund, fiscal_receipt, tip, tip_allocation | payments | MVP |
| `reservations` | reservation, reservation_table, reservation_reminder | booking | MVP |
| `conversations` | channel_account, conversation, message, ai_run | ai | MVP |
| `reviews` | review, review_reply | ai | MVP |
| `integrations` | integration, sync_job, outbox_event | integrations | MVP |
| `loyalty` | loyalty_*, promo_code | crm | R1 (мес. 4–6) |
| `inventory` | supplier, product, invoice, invoice_line | back-office | R1 |
| `staff` | position, employee*, shift*, time_entry, staff_forecast | back-office | R1–R2 |
| `audit` | audit_log, file_object | platform | MVP |

Модули не читают таблицы друг друга напрямую. Если нужны чужие данные, модуль вызывает сервис другого модуля или подписывается на событие из `outbox_event`.

## 3. Соглашения

- **Имена.** Идентификаторы в коде на английском, документы на русском. В БД и API используется snake_case, в TS-коде — camelCase. Строки, приходящие из БД, сохраняют snake_case и типизируются через `@podacha/shared`.
- **Деньги** хранятся целыми копейками (`bigint` в БД, `number` в TS). Числа с плавающей точкой для сумм запрещены.
- **ID** в БД — UUIDv7 (`uuidv7()` из shared). Наружу ID отдаются с префиксом (`ord_…`), как описано в api-contract.md.
- **Мультиарендность.** Любой запрос идёт через `Db.repo()` с `TenantContext`. В Postgres перед запросом выполняется `SET LOCAL app.org_id`, и RLS работает в режиме FORCE. Чужие объекты возвращают 404.
- **Ошибки** бросаются через `AppError(code, status, message)` и превращаются в ответ RFC 9457 с полем `code` и `request_id`. Список кодов лежит в `shared/src/errors.ts`.
- **Валидация.** Вход и выход проверяются zod-схемами из `shared`. Сущности генерируются из DDL (`npm run gen:zod`), а DTO для API пишутся рядом с модулем.
- **Миграции** только вперёд, файлы называются `packages/db/migrations/NNNN_name.sql`. Уже применённую миграцию не правят. Изменили DDL — перегенерируйте zod.
- **ИИ.** ПДн уходят только к RU-провайдерам (`containsPii: true` пропускает остальные). Цены подставляются из БД через `{{price:ID}}`.
- **Интеграции.** Каждый вендор — отдельная реализация интерфейса в `packages/integrations/src/<vendor>/`. Для тестов есть mock. Webhook всегда проверяется через `verifyWebhook`, а статус платежа перезапрашивается.
- **Тесты.** Используется vitest из корня (`npm test`). Внешние сервисы в тестах не вызываются, вместо них mock-адаптеры и `createMemoryDb()`.
- **Проверки:** `npm run build`, `npm test` и `npm run lint` должны проходить перед мерджем.

## 4. Принятые решения (без согласования, можно пересмотреть)

1. Монорепозиторий на npm workspaces без turbo и nx. Порядок сборки задаёт список `workspaces` в корневом `package.json`.
2. ORM нет. Используется тонкий слой поверх `pg`, `Repo` с `TenantContext` и in-memory реализация для тестов вместо pg-mem, потому что pg-mem не поддерживает RLS, EXCLUDE и tstzrange.
3. zod-схемы генерируются из DDL скриптом `scripts/gen-zod.mjs`, поэтому источник правды один — SQL. Для `tstzrange` берётся объект `{from, to}`.
4. Для фронтендов выбран Preact: малый бандл, а гостевое приложение открывается по QR на мобильной сети.
5. В тестах vitest резолвит пакеты через alias на `src`, а в рантайме используется `dist`.
