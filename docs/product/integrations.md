# Интеграции: POS, эквайринг СБП, мессенджеры

Дата: 30.10.2026. Статус: спецификация v1 (черновик для разработки).
Связанные документы: `research/00-strategy.md`, `sales/product-roadmap.md`, `docs/product/domain-model.md`, `docs/product/openapi.yaml`.

Пометка **[проверить]** означает, что эндпоинт или поле взяты из публичной документации по памяти и не сверены в этой сессии. Перед реализацией адаптера разработчик сверяет их с актуальной документацией вендора и фиксирует версию API в `adapter.manifest`.

---

## 1. Принципы

| # | Принцип | Следствие |
|---|---|---|
| 1 | Касса клиента — источник истины для меню, цен, стоп-листов и чеков | Мы не печатаем фискальный чек сами, если заказ уходит в POS. Меню в нашей БД — кэш с `source=pos` |
| 2 | Все внешние системы подключаются через адаптеры с единым контрактом (раздел 2) | Ядро (QR-меню, AI-официант, бронирование) не знает про iiko/Poster |
| 3 | Всё асинхронно через outbox + очередь | Падение POS не роняет приём заказа. Заказ получает статус `pending_pos` и повторяется |
| 4 | Идемпотентность везде | Наш `order.id` (UUIDv7) передаётся как внешний id / ключ идемпотентности во все API |
| 5 | Данные в РФ (152-ФЗ) | Адаптеры и очереди работают в Yandex Cloud / Selectel (ru-central1). Токены клиентов хранятся в KMS-шифровании. ПДн гостя в POS передаём минимально (имя, телефон только для доставки/брони) |
| 6 | Вебхуки внешних систем проверяются (подпись / IP / секрет) и сразу кладутся в очередь | Ответ 200 за <1 c, обработка асинхронно |
| 7 | Деньги проходят только через лицензированных эквайеров | Мы не платёжный агент; деньги идут на р/с заведения (модель «маркетплейс/сплит» — этап 2) |

---

## 2. Абстракция адаптеров

### 2.1. Типы адаптеров

| Порт (interface) | Назначение | Реализации v1 | Реализации позже |
|---|---|---|---|
| `PosAdapter` | меню, стоп-листы, столы, заказы, статусы | `iiko_cloud`, `poster` | `rkeeper_delivery`, `rkeeper_xml` (через агент), `quick_resto`, `manual` (без кассы) |
| `PaymentAdapter` | СБП/карты, возвраты, чаевые | `yookassa`, `tbank` | `cloudpayments`, `sber` |
| `ChannelAdapter` | входящие/исходящие сообщения ботов | `telegram`, `max`, `vk` | `whatsapp` (СНГ), `voice` (SIP + SpeechKit) |
| `ReviewSourceAdapter` | отзывы | — (см. отдельную спецификацию) | Яндекс Карты, 2ГИС |

### 2.2. Контракт `PosAdapter` (TypeScript-нотация)

```ts
interface PosAdapter {
  readonly kind: 'iiko_cloud' | 'poster' | 'rkeeper_delivery' | 'rkeeper_xml' | 'quick_resto' | 'manual';
  capabilities(): PosCapabilities;               // что поддерживает касса
  testConnection(cfg: PosConfig): Promise<HealthResult>;
  listLocations(): Promise<PosLocation[]>;       // организации / заведения / spots
  fetchMenu(locationId: string): Promise<CanonicalMenu>;
  fetchStopList(locationId: string): Promise<StopListEntry[]>;
  fetchTables?(locationId: string): Promise<PosTable[]>;
  createOrder(o: CanonicalOrder): Promise<PosOrderRef>;      // идемпотентно по o.id
  addItems?(ref: PosOrderRef, items: CanonicalOrderItem[]): Promise<void>; // дозаказ к столу
  getOrderStatus(ref: PosOrderRef): Promise<CanonicalOrderStatus>;
  registerPayment?(ref: PosOrderRef, p: PaymentInfo): Promise<void>;       // отметить оплату СБП в кассе
  parseWebhook?(req: RawRequest): Promise<PosEvent[]>;
}

interface PosCapabilities {
  menuPull: boolean; stopListPull: boolean; stopListPush: boolean /* вебхук */;
  tableOrders: boolean; deliveryOrders: boolean; addItemsToOpenOrder: boolean;
  externalPayment: boolean; orderStatusPush: boolean; modifiers: 'none'|'simple'|'groups';
}
```

### 2.3. Каноническая модель (связь с `domain-model.md`)

| Сущность | Ключевые поля | Маппинг-таблица |
|---|---|---|
| `CanonicalMenu` | categories[], items[], modifierGroups[], version, fetchedAt | `pos_mapping(tenant_id, entity, our_id, pos_id, pos_kind)` |
| `MenuItem` | posId, name, description, price (копейки, int), vat, weight/kcal, imageUrl, modifierGroupIds, tags (аллергены — наши, не из POS) | |
| `StopListEntry` | posItemId, balance (null = полностью стоп) | |
| `CanonicalOrder` | id (UUIDv7), locationId, type: `table`/`pickup`/`delivery`, tableId, items[], comment, guest{name, phone?}, payment{status, method:`sbp`/`card`/`cash_at_table`, amount, tips} | |
| `CanonicalOrderStatus` | `pending_pos` → `accepted` → `cooking` → `ready` → `served`/`delivered` → `closed`; `rejected`, `cancelled`, `error` | таблица маппинга статусов в разделах 3.x |

Все суммы — целые копейки. Наши описания, фото и аллергены хранятся отдельно и **не перетираются** синхронизацией (merge по `posId`).

### 2.4. Синхронизация

| Данные | Механизм | Частота |
|---|---|---|
| Меню | pull по расписанию + ручная кнопка «Синхронизировать» + вебхук где есть | каждые 30 мин; при смене `revision` |
| Стоп-лист | вебхук (iiko) / pull (остальные) | iiko: push + страховочный pull 5 мин; Poster/Quick Resto: pull 2 мин в часы работы |
| Статус заказа | вебхук где есть, иначе polling | polling 15 с первые 10 мин, затем 60 с, стоп через 6 ч |
| Перед оплатой | синхронная проверка стоп-листа по позициям корзины | на каждый checkout (кэш ≤ 60 с) |

### 2.5. Надёжность

- **Outbox:** заказ пишется в БД и в `outbox` одной транзакцией; воркер `pos-dispatcher` отправляет. Ретраи: экспонента 2с·2ⁿ, максимум 8 попыток (~8,5 мин), затем `error` + алерт администратору заведения в Telegram/MAX + печать на «резервный» канал (сообщение официанту в бот).
- **Circuit breaker** на каждую пару (tenant, adapter): 5 ошибок за 60 с → open на 30 с.
- **Rate limits:** токен-бакет на tenant и на вендора (см. таблицы ниже).
- **Секреты:** `integration_credentials` (шифрование KMS, ротация, доступ только у сервиса адаптеров).
- **Наблюдаемость:** каждый вызов логируется в `integration_call_log` (без ПДн, тело маскируется), метрики `pos_request_duration`, `pos_order_create_fail_total` по `kind`.

---

## 3. POS-системы

### 3.1. Сравнение

| Касса | API | Аутентификация | Меню | Стоп-лист | Заказ в зал | Доставка/самовывоз | Статусы | Приоритет |
|---|---|---|---|---|---|---|---|---|
| iiko | iikoCloud API (REST, JSON) | `apiLogin` → bearer-токен (живёт ~1 ч) | да (номенклатура + внешнее меню) | pull + вебхук | да (Table orders) | да (Deliveries) | вебхуки + polling | **P0** |
| Poster | Poster API (REST, JSON) | OAuth 2.0 / `token` аккаунта | да | pull (остатки) + вебхуки об изменениях | через «онлайн-заказы» (incoming orders) | да | вебхук `incoming_order` + polling | **P0** |
| r_keeper | Delivery API (облачный, REST) и XML-интерфейс RK7 (через White Server/агент) | токен Delivery API / логин XML-интерфейса | да | да | XML: да; Delivery API: в основном доставка/самовывоз | да | polling | P1 |
| Quick Resto | REST API платформы (`/platform/online/api`) | Basic auth (логин слоя + ключ) | да | ограниченно [проверить] | ограниченно | заказы через API [проверить] | polling | P2 |
| Без кассы | — | — | ручной ввод / импорт Excel | ручной в админке | заказ приходит в бот официанта/КДС-веб | да | наши | P0 (fallback) |

### 3.2. iiko Cloud API

Базовый URL: `https://api-ru.iiko.services`. Документация: `api-ru.iiko.services` (Swagger). Все запросы — `POST` с JSON, заголовок `Authorization: Bearer <token>`, рекомендуется `Timeout` до 15 с.

| Операция | Метод и путь | Примечания |
|---|---|---|
| Получить токен | `POST /api/1/access_token` `{ "apiLogin": "..." }` | токен кэшируем 50 мин; apiLogin выдаёт владелец в iikoWeb → Облачное API |
| Организации | `POST /api/1/organizations` | `organizationIds`, `returnAdditionalInfo` |
| Терминальные группы | `POST /api/1/terminal_groups` | нужна для создания заказа; проверка онлайна — `POST /api/1/terminal_groups/is_alive` |
| Номенклатура | `POST /api/1/nomenclature` `{ organizationId, startRevision }` | возвращает `groups`, `productCategories`, `products`, `sizes`, `revision`; инкрементально по `startRevision` |
| Внешние меню (рекомендуется) | `POST /api/2/menu` (список), `POST /api/2/menu/by_id` `{ externalMenuId, organizationIds, priceCategoryId }` | меню, собранное в iikoWeb под «Подача» — предпочтительно, т.к. цены/видимость управляет клиент |
| Стоп-листы | `POST /api/1/stop_lists` `{ organizationIds }` | `terminalGroupStopLists[].items[] {productId, balance}`; проверка корзины — `POST /api/1/stop_lists/check` [проверить] |
| Столы | `POST /api/1/reserve/available_restaurant_sections` | `restaurantSections[].tables[]` |
| Заказ к столу | `POST /api/1/order/create` `{ organizationId, terminalGroupId, order: { id, tableIds, items, payments, comment } }` | `order.id` = наш UUID → идемпотентность; ответ — `correlationId` |
| Дозаказ | `POST /api/1/order/add_items` | к открытому заказу стола |
| Статус команды | `POST /api/1/commands/status` `{ organizationId, correlationId }` | `InProgress`/`Success`/`Error` |
| Заказы по id | `POST /api/1/order/by_id` / `POST /api/1/order/by_table` | |
| Доставка/самовывоз | `POST /api/1/deliveries/create`, `.../by_id`, `.../update_order_delivery_status` | `orderServiceType: DeliveryByClient` для самовывоза |
| Типы оплат | `POST /api/1/payment_types` | заводим в iiko тип оплаты «Подача СБП» (external, `isProcessedExternally: true`) |
| Оплата заказа | в `payments[]` при создании либо `POST /api/1/order/change_payments` [проверить] | отмечаем, что заказ оплачен по СБП |
| Вебхуки | `POST /api/1/webhooks/update_settings` `{ organizationId, webHooksUri, authToken, webHooksFilter }` | события `DeliveryOrderUpdate`, `TableOrderUpdate`, `StopListUpdate`, `ReserveUpdate`; проверяем `authToken` в заголовке `Authorization` |
| Брони | `POST /api/1/reserve/create`, `.../status_by_id` | для AI-администратора бронирований (если у клиента модуль iikoReserve) |

Маппинг статусов (Table/Delivery): `Unconfirmed/WaitCooking` → `accepted`; `CookingStarted` → `cooking`; `CookingCompleted` → `ready`; `Delivered`/`Closed` → `served`/`closed`; `Cancelled` → `cancelled`; `creationStatus=Error` → `rejected`.

Лимиты: официальных публичных цифр нет [проверить]; закладываем ≤ 2 rps на организацию, номенклатуру не чаще 1 раза в 5 мин.

### 3.3. Poster API

Базовый URL: `https://joinposter.com/api/{method}?token=...` (метод в формате `сущность.действие`). Партнёрские приложения — OAuth: `https://joinposter.com/api/v2/auth` → `POST https://joinposter.com/api/v2/auth/access_token` (`application_id`, `application_secret`, `code`) → `access_token` аккаунта. Регистрируем приложение в маркетплейсе Poster (dev.joinposter.com).

| Операция | Метод | Примечания |
|---|---|---|
| Заведения | `GET access.getSpots` | spot = точка |
| Категории | `GET menu.getCategories` | |
| Товары и техкарты | `GET menu.getProducts` (`type=products|batchtickets`) | цены `price[spot_id]` в копейках; `spots[].visible` — видимость в точке; модификаторы `group_modifications` |
| Один товар | `GET menu.getProduct?product_id=` | |
| Остатки (стоп-лист) | `GET storage.getStorageLeftovers` | нулевой остаток или `hidden` в точке → стоп |
| Столы и залы | `GET spots.getTableHallTables`, `spots.getSpotTablesHalls` | |
| Онлайн-заказ | `POST incomingOrders.createIncomingOrder` `{ spot_id, phone, first_name, products:[{product_id, modificator_id, count}], comment, service_mode (1 — в зале, 2 — с собой, 3 — доставка), table_id? }` | заказ попадает в кассу как «онлайн-заказ», кассир принимает |
| Статус онлайн-заказа | `GET incomingOrders.getIncomingOrder?incoming_order_id=` | `status`: 0 новый, 1 принят, 7 отклонён; после принятия — `transaction_id` |
| Чек | `GET dash.getTransaction?transaction_id=` | статус оплаты/закрытия |
| Оплата | передача `payment` в `createIncomingOrder` (`type`, `sum`, `currency`) [проверить] | отмечаем предоплату СБП |
| Вебхуки | настраиваются в кабинете приложения; POST JSON `{ account, object, object_id, action, verify, data }` | `verify = md5(account;object;object_id;action;data;time;application_secret)` [проверить порядок]; объекты `incoming_order`, `product`, `storage`/`stock`, `transaction` |

Маппинг статусов: incoming 0 → `pending_pos`; 1 → `accepted`; 7 → `rejected`; транзакция закрыта → `closed`. Стадий «готовится/готов» Poster в API не отдаёт — статус кухни показываем только у iiko.

### 3.4. r_keeper (UCS)

Два пути; выбираем по инсталляции клиента.

| Вариант | Где работает | Что даёт | Как подключаемся |
|---|---|---|---|
| **r_keeper Delivery API** (облачный, лицензия «Delivery»/«Агрегаторы») | облако UCS | меню, стоп-лист, создание заказа доставки/самовывоза, статусы | REST, токен выдаётся в r_keeper Store / Delivery-кабинете [проверить]; методы вида `GetMenu`, `GetStopList`, `CreateOrder`, `GetOrder` |
| **XML-интерфейс RK7** (через White Server / XML-агент) | локальный сервер заведения | полный доступ: меню, столы, заказ в зал, оплата внешним типом | HTTPS-запросы `POST https://<host>:<port>/rk7api/v0/xmlinterface.xml`, Basic auth; запросы `<RK7Query><RK7CMD CMD="GetRefData" RefName="MENUITEMS"/></RK7Query>`, `CMD="GetOrderMenu"`, `CMD="CreateOrder"`, `CMD="SaveOrder"`, `CMD="GetOrder"`, `CMD="GetOrderList"` |

Решение: в v1 (P1) делаем **Delivery API** для облачных клиентов; для XML ставим наш лёгкий агент `podacha-edge-agent` (Docker/Windows service) в сети заведения, который держит исходящее WebSocket-соединение к нам (не требует белого IP) и проксирует XML-команды. Стоп-лист в RK7 — `GetRefData` по `MENUITEMS` с остатками / `GetOrderMenu` (доступность на станции) [проверить]. Лицензию XML-интерфейса клиент покупает у дилера UCS — указываем в коммерческом предложении.

### 3.5. Quick Resto

Базовый URL: `https://<layer>.quickresto.ru/platform/online/api/`. Аутентификация Basic (логин облака + API-пароль из кабинета), заголовок `Connection: keep-alive`.

| Операция | Запрос | Примечания |
|---|---|---|
| Список блюд | `GET list?moduleName=warehouse.nomenclature.dish&className=ru.edgex.quickresto.modules.warehouse.nomenclature.dish.Dish` | [проверить className] |
| Категории | `GET list?moduleName=warehouse.nomenclature.dish&className=...DishCategory` | |
| Объект | `GET read?moduleName=...&objectId=` | |
| Заказы/чеки (чтение) | `GET list?moduleName=front.orders` | для аналитики |
| Создание заказа | через модуль доставки/«Онлайн-заказы» [проверить] | если недоступно — режим «заказ в бот официанта» |

P2: только импорт меню + чтение чеков для аналитики; заказы — через fallback.

### 3.6. Режим «без кассы / касса не поддерживается»

Заказ отображается в веб-КДС (`/staff`) и дублируется в рабочий чат официантов (Telegram/MAX) с кнопками «Принял», «Готово», «Отдано». Оплата СБП проходит через наш эквайринг; фискализация — облачная касса эквайера (ЮKassa «Чеки от ЮKassa» / T‑Bank «Т‑Чеки»/ АТОЛ Онлайн) по 54-ФЗ.

---

## 4. Эквайринг и СБП

### 4.1. Сравнение провайдеров

| Провайдер | СБП через API | Ставка СБП (ориентир) | Вебхуки | Чеки 54-ФЗ | Сплит/маркетплейс | Приоритет |
|---|---|---|---|---|---|---|
| ЮKassa | да, `payment_method_data.type = "sbp"` | 0,4–0,7% [проверить] | HTTP-уведомления, проверка по IP + повторный GET | да (Чеки от ЮKassa, интеграция с онлайн-кассой) | да (ЮKassa для платформ, `transfers`) | **P0** |
| Т‑Банк (Tinkoff) Acquiring | да, `Init` → `GetQr` | 0,4–0,7% [проверить] | `NotificationURL`, подпись `Token` (SHA-256) | да (`Receipt` в Init) | да (Мультирасчёты) | **P0** |
| CloudPayments | да (СБП в виджете, API `/payments/qr/sbp/image`) [проверить] | 0,4–0,7% [проверить] | Pay/Fail/Check-уведомления, HMAC-SHA256 в `Content-HMAC` | да (CloudKassir) | ограниченно | P1 |

Комиссия СБП для ТСП по правилам НСПК ограничена 0,4–0,7% в зависимости от MCC; для общепита (MCC 5812/5814) ориентир 0,4–0,7%. Сверить с тарифами банка клиента.

Модель: **каждое заведение подключает свой магазин** у эквайера (деньги на р/с заведения, мы — технический интегратор с ключами магазина). Вариант «платформа со сплитом» (наш агентский договор) — этап 2 после юр. проверки (115-ФЗ, агентская схема).

### 4.2. ЮKassa

Базовый URL `https://api.yookassa.ru/v3`, Basic auth `shopId:secretKey`, обязательный заголовок `Idempotence-Key` (= наш `payment.id`).

| Операция | Запрос |
|---|---|
| Создать платёж СБП | `POST /payments` `{ amount:{value:"1250.00",currency:"RUB"}, capture:true, payment_method_data:{type:"sbp"}, confirmation:{type:"redirect", return_url}, description, metadata:{order_id, tenant_id}, receipt? }` → `confirmation.confirmation_url` (deeplink/QR СБП) |
| Статус | `GET /payments/{id}` |
| Возврат | `POST /refunds` `{ payment_id, amount }` |
| Вебхуки | настраиваются в ЛК или `POST /webhooks` (для партнёрских OAuth-токенов); события `payment.succeeded`, `payment.canceled`, `refund.succeeded` |
| Проверка вебхука | подписи нет → проверяем IP из списка ЮKassa (185.71.76.0/27, 185.71.77.0/27, 77.75.153.0/25, 77.75.156.11, 77.75.156.35, 77.75.154.128/25, 2a02:5180::/32 [проверить]) **и** делаем `GET /payments/{id}` перед сменой статуса |

### 4.3. Т‑Банк (T‑Kassa)

Базовый URL `https://securepay.tinkoff.ru/v2/`. Подпись `Token` = SHA-256 от конкатенации значений корневых параметров + `Password`, отсортированных по ключу.

| Операция | Запрос |
|---|---|
| Инициализация | `POST /Init` `{ TerminalKey, Amount (копейки), OrderId, Description, NotificationURL, SuccessURL, DATA:{...}, Receipt?, Token }` → `PaymentId` |
| QR / ссылка СБП | `POST /GetQr` `{ TerminalKey, PaymentId, DataType:"PAYLOAD"\|"IMAGE", Token }` → payload `https://qr.nspk.ru/...` |
| Статус | `POST /GetState` |
| Отмена/возврат | `POST /Cancel` |
| Уведомления | POST JSON на `NotificationURL`: `Status` = `CONFIRMED`/`REJECTED`/`REFUNDED`, проверяем `Token`; отвечаем телом `OK` |

### 4.4. CloudPayments

Базовый URL `https://api.cloudpayments.ru`, Basic auth `PublicId:ApiSecret`. СБП — `POST /payments/qr/sbp/image` (`Amount`, `Currency`, `InvoiceId`, `Description`, `Scheme: "charge"`) [проверить], статус — `POST /payments/find` по `InvoiceId`. Уведомления Check/Pay/Fail/Refund: подпись `Content-HMAC` = Base64(HMAC-SHA256(body, ApiSecret)); ответ `{"code":0}`.

### 4.5. Чаевые

v1: чаевые добавляются к сумме платежа отдельной позицией `tips` в нашей модели; в чек 54-ФЗ не входят (передаём как отдельный платёж/агентская схема — **решение юриста**). Рекомендуемый вариант v1 — партнёрский сервис чаевых (Нетмонет / «Чаевые» Т‑Банка / CloudTips) по ссылке на официанта: деньги не проходят через ТСП. Своя схема — этап 2.

### 4.6. Поток оплаты

```
Гость → QR-меню → checkout → проверка стоп-листа → PaymentAdapter.create (Idempotence-Key=payment.id)
     → deeplink СБП (банк-приложение) → вебхук провайдера → верификация → payment.status=succeeded
     → outbox: PosAdapter.createOrder (или registerPayment к уже созданному заказу) → уведомление гостю
```
Если POS отклонил заказ после оплаты → автоматический возврат + сообщение гостю и администратору. Таймаут ожидания оплаты 15 мин, затем `cancelled`.

---

## 5. Каналы (мессенджеры)

### 5.1. Сравнение

| Канал | API | Приём событий | Проверка подлинности | Особенности | Приоритет |
|---|---|---|---|---|---|
| Telegram | Bot API `https://api.telegram.org/bot<token>/<method>` | `setWebhook` (url, `secret_token`, `allowed_updates`) | заголовок `X-Telegram-Bot-Api-Secret-Token` | лимиты ~30 msg/s на бота, 1 msg/s в чат; Mini App для меню; замедление в РФ — держим fallback-ссылку на веб | **P0** |
| MAX | MAX Bot API `https://platform-api.max.ru` (заголовок `Authorization: <token>`) [проверить формат] | `POST /subscriptions` (webhook URL, `update_types`) или long polling `GET /updates` | секрет в URL подписки + проверка IP [проверить] | бот регистрируется через @MasterBot / платформу для бизнеса (нужна верификация юрлица); `POST /messages?chat_id=`, inline-клавиатуры, мини-приложения | **P0** |
| VK | VK API `https://api.vk.com/method/<method>?v=5.199` | Callback API (группа) | `confirmation` при подключении + поле `secret` в каждом событии | `messages.send` требует `random_id`; ключ сообщества с правом messages; лимит ~20 rps на сообщество | P1 |
| Телефон | SIP-транк (Mango/Билайн/МТС Exolve) + Yandex SpeechKit | вебхук провайдера телефонии | IP + подпись провайдера | для AI-администратора бронирования | P1 |

### 5.2. Ключевые методы

| Действие | Telegram | MAX | VK |
|---|---|---|---|
| Отправить текст | `sendMessage` | `POST /messages?chat_id=` `{text, attachments}` | `messages.send` (`peer_id`, `message`, `random_id`) |
| Кнопки | `reply_markup.inline_keyboard` | attachment `inline_keyboard` | `keyboard` (JSON, `inline:true`) |
| Нажатие кнопки | `callback_query` → `answerCallbackQuery` | `message_callback` → `POST /answers` | `message_event` → `messages.sendMessageEventAnswer` |
| Новое сообщение | `message` | `message_created` | `message_new` |
| Старт бота | `/start <payload>` (deeplink со столом/заведением) | `bot_started` (payload) | `ref` в ссылке `vk.me/<group>?ref=` |
| Мини-приложение | Web App (`initData`, HMAC-проверка) | MAX Mini App [проверить] | VK Mini Apps (проверка `sign`) |

### 5.3. Контракт `ChannelAdapter`

```ts
interface ChannelAdapter {
  readonly kind: 'telegram' | 'max' | 'vk' | 'whatsapp' | 'voice';
  verify(req: RawRequest): boolean;
  parse(req: RawRequest): InboundEvent[];        // {conversationId, user{externalId,name}, text?, callbackData?, payload?}
  send(to: ConversationRef, msg: OutboundMessage): Promise<SentRef>; // text, buttons[], media[]
  ack?(event: InboundEvent): Promise<void>;      // answerCallbackQuery / POST /answers
}
```
Один `OutboundMessage` рендерится в каждый канал; кнопок > лимита канала → разбиение на страницы. Исходящие идут через очередь с лимитером по (bot, chat). Модель ИИ (YandexGPT/GigaChat) видит только канонические события.

---

## 6. Безопасность и 152-ФЗ

| Требование | Реализация |
|---|---|
| Локализация | все адаптеры, очереди, логи — в РФ-регионе |
| Минимизация | в POS и эквайер — только нужное для заказа/чека (телефон/e-mail для чека 54-ФЗ — с согласия) |
| Секреты | KMS, доступ по сервисной роли, ротация при отключении интеграции |
| Вебхуки | отдельный поддомен `hooks.podacha.ai/{provider}/{tenant_hook_id}` с непредсказуемым id, проверка подписи/секрета/IP, rate limit |
| Аудит | `integration_call_log` 90 дней, без тел с ПДн |

## 7. План работ

| Этап | Состав | Оценка |
|---|---|---|
| M1 | Ядро адаптеров, outbox, `manual`, iiko (меню, стоп, заказ к столу, вебхуки), ЮKassa СБП, Telegram + MAX | 6–8 нед. [оценка] |
| M2 | Poster, Т‑Банк, VK, чаевые через партнёра | 4 нед. |
| M3 | r_keeper Delivery API + edge-agent для XML, CloudPayments, телефония | 6 нед. |
| M4 | Quick Resto, сплит-платежи, WhatsApp (СНГ) | по спросу |

## 8. Принятые решения (ADR-кратко)

1. Единые порты `PosAdapter`/`PaymentAdapter`/`ChannelAdapter` + каноническая модель; ядро не знает вендоров.
2. Касса — источник истины для меню/цен/стопов; наш контент (фото, описания, аллергены) хранится поверх и не перетирается.
3. Приоритет POS: iiko и Poster (P0), r_keeper (P1), Quick Resto (P2), режим «без кассы» с веб-КДС и ботом официанта — всегда.
4. iiko: через внешнее меню `/api/2/menu/by_id`, заказ `order/create` с нашим UUID, вебхуки для стопов и статусов.
5. r_keeper XML — через собственный edge-agent с исходящим соединением (без белого IP у клиента).
6. Outbox + ретраи + circuit breaker; оплата без принятого POS-заказа автоматически возвращается.
7. СБП через ЮKassa и Т‑Банк (P0), CloudPayments (P1); каждое заведение — свой магазин, мы не держим деньги.
8. Вебхуки эквайеров всегда перепроверяются запросом статуса (у ЮKassa нет подписи).
9. Чаевые v1 — через партнёрский сервис; своя схема — после юр. заключения.
10. Каналы Telegram + MAX (P0), VK (P1), телефон через SpeechKit; единый `ChannelAdapter`, исходящие через очередь с лимитерами.
