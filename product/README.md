# Подача.AI — код продукта

Монорепозиторий на npm workspaces, TypeScript, Node 20+. Спецификации лежат в `../docs/product/`, архитектура и соглашения описаны в `../docs/product/ARCHITECTURE.md`.

## Быстрый старт

```bash
cp .env.example .env
docker compose up -d postgres   # PostgreSQL 16; миграции из packages/db/migrations применяются при первом старте
npm install
npm run build                   # все пакеты и приложения
npm test                        # vitest (БД в памяти, внешних сервисов нет)
npm run lint                    # tsc --noEmit
npm run dev:api                 # http://localhost:3000/v1/health
```

Без `DATABASE_URL` API запускается на БД в памяти.

## Структура

| Путь | Что внутри |
|---|---|
| `apps/api` | REST API на Fastify (`/v1`); модули подключаются автоматически из `src/modules/<name>/index.ts` |
| `apps/guest` | Гостевое веб-приложение по QR (Vite + Preact) |
| `apps/admin` | Кабинет владельца и менеджера (Vite + Preact) |
| `apps/bots` | Воркеры каналов Telegram, VK и MAX |
| `packages/shared` | Типы, zod-схемы всех сущностей, деньги, ошибки RFC 9457, UUIDv7 |
| `packages/db` | SQL-миграции, лёгкий слой запросов (pg с RLS и вариант в памяти) |
| `packages/ai` | Интерфейс `LLMProvider`, цепочка fallback, mock, маскирование ПДн, подстановка цен |
| `packages/integrations` | Интерфейсы `PosAdapter` и `PaymentAdapter`, mock-реализации |

zod-схемы генерируются из DDL командой `npm run gen:zod`. Править файл `entities.generated.ts` вручную нельзя.
