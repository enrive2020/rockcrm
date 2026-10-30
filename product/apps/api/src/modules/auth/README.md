# Модули auth + tenancy

Эндпоинты (все под `/v1`):
- `POST /auth/signup` — регистрация владельца и создание организации (требуется `pd_consent: true`).
- `POST /auth/login`, `POST /auth/otp:request`, `POST /auth/otp:verify`, `POST /auth/refresh`, `POST /auth/logout`.
- `POST /auth/switch-organization`, `POST /auth/password`, `POST /auth/invitations:accept`.
- `GET /me`, `GET /roles`, `GET /.well-known/jwks.json`.
- `GET /users`, `POST /users/invitations`, `PATCH /users/:userId`, `DELETE /users/:userId`.
- `GET|PATCH /organization`, `GET|POST /venues`, `GET|PATCH|DELETE /venues/:venueId` (модуль tenancy).

Для других модулей: `import { requireAuth, ctxOf } from '../auth/guard.js'`, затем
`{ preHandler: requireAuth({ scope: 'menu:write', venueParam: 'venueId' }) }` и `ctxOf(req).tenant` для `Db.repo()`.
Если точка чужая или недоступна, ответ 404. Если не хватает scope, ответ 403.

Принятые решения:
1. JWT ES256 сделан на `node:crypto` без зависимостей. Ключ задаётся в `AUTH_JWT_PRIVATE_KEY` (PEM P-256), для ротации есть `AUTH_JWT_PREVIOUS_PUBLIC_KEYS`, разделитель `|||`. В dev ключ эфемерный, в production без ключа сервис не стартует.
2. Access-токен живёт 15 минут, refresh — 30 дней. Refresh непрозрачный, формат `<id>.<secret>`, в БД хранится sha256 секрета. Токен ротируется при каждом использовании. Повторное использование отзывает всю цепочку (family).
3. Пароли хешируются через scrypt (N=16384). OTP — 6 цифр, живёт 5 минут, даётся 5 попыток, не больше 5 запросов за 15 минут. По ответу нельзя понять, зарегистрирован ли телефон.
4. Глобальные таблицы (`app_user`, `user_org`, `refresh_token`, `auth_otp`, `venue_slug`) хранятся в псевдо-арендаторе `PLATFORM_ORG_ID` (нулевой UUID), потому что `Repo` всегда пишет `organization_id`. Подробности в миграции `0002_auth_tenancy.sql`.
5. Роли берутся из DDL. Их соответствие scopes описано в `packages/shared/src/auth-tenancy.ts`. Владелец может всё. Админ может всё, кроме биллинга и экспорта гостей. Менеджер управляет только официантами и поварами своих точек.
6. В `ERROR_CODES` нет кодов `TOKEN_EXPIRED` и `FORBIDDEN_ROLE`, поэтому используются `UNAUTHORIZED` и `FORBIDDEN`.
7. Токен приглашения возвращается в ответе, формат `<org>.<inv>.<secret>`. Доставка по SMS, почте или через бот — задача модуля уведомлений. Если аккаунт уже существует, при принятии приглашения нужно подтвердить пароль.
8. ID отдаются как UUID, без префиксов `ven_`.
9. TODO: зоны, столы и QR (`/venues/:id/tables`, `qr:rotate`), guest JWT, `/auth/token` (client_credentials, L1), PIN-вход, запись в audit_log.
