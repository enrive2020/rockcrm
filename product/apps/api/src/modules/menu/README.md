# Модуль `menu`

Меню, категории, позиции, модификаторы, аллергены, переводы, стоп-лист и публичное меню по QR-токену стола.

## Эндпоинты (префикс `/v1`)

| Метод и путь | Scope |
|---|---|
| `GET /venues/{v}/menu` — полное дерево, включая скрытые позиции | любой сотрудник |
| `GET/POST /venues/{v}/menus`, `PATCH/DELETE /venues/{v}/menus/{id}` | `menu:write` для записи |
| `GET/POST /venues/{v}/menu/categories`, `PATCH/DELETE .../{id}` | `menu:write` |
| `GET/POST /venues/{v}/menu/items` (параметры `category_id`, `q`, `updated_since`, `limit`, `cursor`), `GET/PATCH/DELETE .../{id}` (ETag `"vN"`, `If-Match`, при несовпадении 412) | `menu:write` |
| `PUT /venues/{v}/menu/items/{id}/modifier-groups {group_ids}` | `menu:write` |
| `GET/POST /venues/{v}/menu/modifier-groups`, `PATCH/DELETE .../{id}`, `POST .../{id}/modifiers`, `PATCH/DELETE /venues/{v}/menu/modifiers/{id}` | `menu:write` |
| `GET /venues/{v}/menu/translations?entity_type&entity_id`, `PUT /venues/{v}/menu/translations {items}` | `menu:write` |
| `GET/PUT/POST /venues/{v}/stop-list`, `POST /venues/{v}/stop-list/{id}:lift` | `stoplist:write` |
| `GET /public/menu/{qr_token}?locale=` — без авторизации, `ETag`/304, `Cache-Control: max-age=60` | — |

Другие модули обращаются к модулю через `getMenuService(app.db)`: `checkAvailability` нужен заказам, `replaceStopList(..., 'pos')` — синхронизации с POS, `buildMenu` — гостевому `/guest/menu` и ИИ-официанту.

## Принятые решения

1. **Аутентификация.** Модуль `auth` должен класть в `request.auth` объект `{organizationId, userId, scopes, venueIds}`. Пока этого нет, вне production принимаются заголовки `x-org-id`, `x-user-id` и `x-scopes`. Если точка не входит в `venueIds`, возвращается 404.
2. **QR.** Токен в точку резолвит модуль `tenancy` через `app.decorate('resolveQrToken', fn)`, который возвращает `QrResolution` из `@podacha/shared`. Хеш токена и функция SECURITY DEFINER остаются на его стороне. Резервный резолвер — `setQrResolver` (тесты и демо). Отозванный QR даёт 410 `QR_REVOKED`.
3. **Мягкое удаление.** В `Repo` нет DELETE. Поэтому у `modifier_group`, `modifier`, `item_modifier_group` и `item_allergen` удаление делается через `deleted_at`, а у связующих таблиц суррогатный `id` формируется как `"{item}:{group}"`. **Нужна миграция**: добавить `deleted_at` в эти четыре таблицы и `id text` в связующие таблицы. Другой вариант — добавить `Repo.delete` в `packages/db`.
4. **Аллергены.** Справочник ЕС-14 (`ALLERGEN_CODES`). Строка `allergen` создаётся лениво для каждой организации, потому что memory-Db не умеет глобальные строки с `organization_id = null`.
5. **POS.** Если у позиции задан `external_source`, правка `name`, `price`, `category_id`, `vat_rate` и `marking_required` возвращает 409 `MENU_MANAGED_BY_POS`. Описание, теги, фото, видимость и переводы менять можно. Создавать позиции в меню с `source != manual` нельзя.
6. **412.** Кода `PRECONDITION_FAILED` нет в `ERROR_CODES`, поэтому отдаётся `CONFLICT` со статусом 412.
7. **Стоп-лист.** Запись действует, если `lifted_at` пуст и текущее время попадает в `[valid_from, valid_until)`. Если `balance` (`remaining_qty`) равен null или ≤ 0, позиция недоступна. Если > 0, позиция доступна, а в меню показывается `remaining_qty`. `PUT` заменяет только записи источника `manual`.
8. **Версия меню** — хеш содержимого (sha256 → 22 символа). Любое изменение, включая стоп-лист, меняет ETag.
9. **Расписание меню** (`schedule`) пока не учитывается: показываются все активные меню. Пагинация — смещение в `cursor`. В pg `Repo.list` ограничен 200 строками, поэтому для больших меню понадобятся постраничные запросы.
10. **ID** наружу отдаются как UUID, без префиксов `itm_…`. События `menu.updated` и `stop_list.updated` в outbox пока не пишутся: это сделает модуль `integrations`.
