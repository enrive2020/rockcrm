# Продуктовая и клиентская аналитика: события, метрики, дашборд владельца, витрины

Дата: 30.10.2026. Статус: спецификация v1 (для MVP модуля «Аналитика», PRD §4.8, ANL-1…ANL-4).
Связанные документы: `prd.md`, `domain-model.md` (таблицы `order`, `payment`, `tip`, `guest_session`), `ai-agents.md`, `compliance-security.md`, `research/00-strategy.md`, `sales/product-roadmap.md`.

Помета **[оценка]** — наше допущение, проверить на пилотах. Деньги везде в копейках (`bigint`), в UI — рубли.

---

## 1. Цели и два контура аналитики

| Контур | Для кого | Вопрос, на который отвечает | Где живёт |
|---|---|---|---|
| **Клиентская аналитика** (дашборд владельца) | owner, manager заведения | «Сколько я заработал, что даёт QR и ИИ, где теряю деньги» | Админка, Telegram/MAX-дайджест, ИИ-аналитик (ANL-2) |
| **Продуктовая аналитика** (наша) | продукт, продажи, CS | «Работает ли продукт, кто уйдёт, какой модуль окупает внедрение» | Внутренний BI (DataLens), алерты CS |

Главная продающая метрика (из `00-strategy.md`): мы продаём **рост среднего чека, экономию ФОТ и скорость оборота столов**. Поэтому дашборд обязан показывать **атрибутированный эффект Подача.AI в рублях** (§5.4), иначе нечем удерживать клиента при оттоке 20–30%/год.

---

## 2. Ключевые решения

| # | Решение | Обоснование |
|---|---|---|
| D1 | **Хранилище аналитики — ClickHouse (Yandex Managed Service for ClickHouse) в РФ**, OLTP остаётся в PostgreSQL. Репликация бизнес-таблиц через CDC (Debezium → Kafka/Yandex Data Streams → ClickHouse), события фронта — напрямую в наш collector | Объём событий QR (≈ 30–80 событий на сессию гостя) убьёт Postgres; ClickHouse — российская разработка, managed-сервис в РФ, 152-ФЗ контур |
| D2 | **Свой трекинг (first-party collector `t.podacha.ai`), без Google Analytics / Amplitude / Mixpanel** для данных гостей. Яндекс Метрика — только на маркетинговом сайте `site/`, не в QR-меню | Данные должны оставаться в РФ; трансграничная передача ПДн требует уведомления РКН; в QR-меню нужна связь событий с заказом, сторонние счётчики её не дают |
| D3 | **Источник денежной истины — платежи и POS, а не события фронта.** Выручка, чек, чаевые считаются только из `payment`/`order`/POS-импорта; события используются для воронок и атрибуции | События теряются (блокировщики, обрыв сети ~3–8% [оценка]); деньги должны сходиться с кассой и эквайером |
| D4 | **Анонимность по умолчанию**: `anonymous_id` (cookie/localStorage, 1st party, 13 мес.), `guest_id` — только после согласия/логина; телефоны и имена в ClickHouse не попадают, только `guest_key = HMAC(guest_id)` | Минимизация ПДн (152-ФЗ ст. 5); аналитика работает без согласия на маркетинг, т.к. обезличенные технические данные необходимы для исполнения договора-оферты [решение юриста подтвердить] |
| D5 | **Конвенция событий `object_action` в snake_case, прошедшее время** (`menu_item_viewed`, `order_submitted`), строгая схема в JSON Schema-реестре, версия `schema_v` | Предсказуемость для text-to-SQL ИИ-аналитика и валидации на collector |
| D6 | **Бизнес-день заведения = с 06:00 до 05:59 по `venue.timezone`** (настраиваемо `venue.business_day_start`) | Бары работают за полночь; выручка ночи должна попадать в «вчерашнюю смену» |
| D7 | **Апселл ИИ считаем строго: позиция атрибутируется ИИ, только если добавлена в корзину в течение сессии после показа рекомендации ИИ (last-touch, окно = та же `guest_session`, ≤ 3 ч) и заказ оплачен** | Консервативная цифра вызывает доверие владельца; иначе «ИИ принёс 30% выручки» не выдержит проверки |
| D8 | **Инкрементальный эффект — через holdout 10% столовых сессий** (рекомендации не показываются) на первых 60 днях клиента и далее 5% | Отделяет эффект ИИ от сезонности; без контроля «рост чека» неотличим от инфляции +15% |
| D9 | **Витрины = материализованные агрегаты ClickHouse (`mart_*`), ИИ-аналитик и дашборд читают только их** (whitelist представлений, RLS через `organization_id` в обязательном фильтре) | Безопасность text-to-SQL, скорость < 1 с, единые формулы |
| D10 | **Свежесть: дашборд — 15 мин (ANL-1), «живые» плитки смены — 1 мин из Postgres-реплики; доверительные метрики (эффект ИИ, когорты) — раз в сутки в 04:00 МСК** | Баланс стоимости и требований PRD |
| D11 | **Медианы и p90 для скоростей**, средние только для денег | Время оплаты и ответа имеет тяжёлые хвосты |
| D12 | **Чаевые не входят в выручку заведения** и показываются отдельным блоком (доход персонала) | Юридически чаевые — доход сотрудника (`domain-model.md` §4.4) |

---

## 3. Архитектура потока данных

```
QR-меню (web) ─┐                 ┌─> raw.events (ClickHouse, TTL 25 мес.)
Боты TG/VK/MAX ─┼─> collector ────┤
Голосовой ИИ ──┤  (Go, валидация  └─> dead_letter (невалидные, 14 дн.)
Админка ───────┘   схем, обогащение)
PostgreSQL (order, payment, tip, reservation, review, ...) ─CDC─> stg.* ─> core.* ─> mart.*
POS (iiko/Poster: закрытые чеки, вкл. не-QR) ─sync 5 мин─> stg.pos_receipt ──┘
```

| Слой | Префикс | Содержимое | Хранение |
|---|---|---|---|
| Сырой | `raw` | События как пришли + `received_at`, IP обрезан до /24 | 25 мес. |
| Staging | `stg` | Копии таблиц Postgres (ReplacingMergeTree по `updated_at`) | бессрочно (без ПДн) |
| Core | `core` | Очищенные факты: `fct_order`, `fct_order_item`, `fct_payment`, `fct_tip`, `fct_session`, `fct_ai_recommendation`, `fct_review`, `fct_reservation`, `dim_venue`, `dim_menu_item`, `dim_employee`, `dim_date` | бессрочно |
| Mart | `mart` | Агрегаты для дашборда и ИИ | бессрочно |

Collector: `POST /v1/batch` (до 50 событий, gzip), `navigator.sendBeacon` при закрытии вкладки, локальная очередь на 200 событий в IndexedDB при офлайне. Ответ 202 всегда (ошибки в dead_letter), чтобы не ломать UX.

---

## 4. Трекинг-план

### 4.1. Общие поля (envelope)

| Поле | Тип | Обязат. | Описание |
|---|---|---|---|
| `event_id` | UUIDv7 | да | Дедупликация (ReplacingMergeTree по `event_id`) |
| `event_name` | string | да | Из реестра §4.2 |
| `schema_v` | uint16 | да | Версия схемы события |
| `occurred_at` | DateTime64(3, UTC) | да | Время клиента; при расхождении с `received_at` > 24 ч — берём серверное |
| `received_at` | DateTime64(3) | сервер | |
| `organization_id`, `venue_id` | UUID | да | Из QR-токена, не от клиента напрямую (подпись) |
| `table_id`, `qr_code_id` | UUID | нет | Для QR-канала |
| `session_id` | UUID | да | `guest_session.id`; новая сессия после 30 мин бездействия или скана другого стола |
| `anonymous_id` | UUID | да | 1st-party идентификатор устройства |
| `guest_key` | FixedString(64) | нет | HMAC-SHA256(guest_id, tenant_salt), только при согласии |
| `channel` | enum | да | `qr`,`telegram`,`vk`,`max`,`phone_ai`,`waiter_app`,`admin` |
| `platform` | enum | да | `web_ios`,`web_android`,`web_desktop`,`bot`,`voice` |
| `locale` | string | да | `ru`,`en`,`zh`, … |
| `app_version` | string | да | |
| `experiment` | Map(String,String) | нет | `{"ai_upsell":"holdout"}` |
| `props` | JSON | да | Свойства события |

Запрещено в `props`: телефон, email, имя, текст комментариев гостя с ПДн (тексты отзывов идут через Postgres, в ClickHouse — только длина и тональность).

### 4.2. Реестр событий

**Гость — QR-меню и заказ**

| Событие | Когда | Ключевые `props` |
|---|---|---|
| `qr_scanned` | Открытие URL QR (сервер, 302) | `qr_type` (`table`,`venue`,`takeaway`,`pay_only`), `utm_*` |
| `menu_opened` | Первый рендер меню | `load_ms`, `menu_id`, `stop_list_count` |
| `category_viewed` | Категория в зоне видимости ≥ 1 с | `category_id`, `position` |
| `menu_item_viewed` | Открыта карточка блюда | `item_id`, `price`, `source` (`list`,`search`,`ai_reco`,`ai_chat`) |
| `search_performed` | Поиск | `query_len`, `results_count` (сам запрос — только хеш + топ-200 частотных в отдельной таблице) |
| `allergen_filter_applied` | Фильтр | `allergens[]` |
| `cart_item_added` | Добавлено в корзину | `item_id`, `variant_id`, `qty`, `unit_price`, `source`, `reco_id` (если из рекомендации) |
| `cart_item_removed` | Удалено | `item_id`, `qty` |
| `cart_viewed` | Открыта корзина | `items_count`, `subtotal` |
| `ai_reco_shown` | Показан блок рекомендаций ИИ | `reco_id`, `placement` (`item_card`,`cart`,`checkout`,`chat`), `item_ids[]`, `model`, `strategy` (`pairing`,`dessert`,`drink`,`upsize`) |
| `ai_reco_clicked` | Клик по рекомендации | `reco_id`, `item_id`, `rank` |
| `ai_chat_message_sent` | Гость пишет ИИ-официанту | `intent` (класс.), `tokens_in`, `latency_ms` |
| `ai_chat_answered` | Ответ ИИ | `tokens_out`, `latency_ms`, `fallback_to_human` |
| `order_submitted` | Заказ отправлен | `order_id`, `items_count`, `subtotal`, `type` |
| `order_rejected_by_pos` | POS не принял | `order_id`, `reason` |
| `waiter_called` | Вызов официанта | `reason` (`bill`,`help`,`other`) |

**Гость — оплата и чаевые**

| Событие | Когда | `props` |
|---|---|---|
| `bill_opened` | Открыт счёт стола (pay_only или после заказа) | `order_id`, `bill_total`, `source` (`qr`,`push`,`waiter`) |
| `bill_split_selected` | Выбран способ деления | `mode` (`full`,`items`,`equal`,`amount`), `parts` |
| `tip_selected` | Выбор чаевых | `percent` (0,5,10,15,custom), `amount` |
| `payment_method_selected` | | `provider` (`sbp`,`card`,`sber_pay`,`yandex_pay`) |
| `payment_started` | Создан платёж у провайдера | `payment_id`, `amount`, `tip_amount` |
| `payment_succeeded` | **Сервер**, по вебхуку провайдера | `payment_id`, `provider`, `duration_ms` (от `payment_started`) |
| `payment_failed` | Сервер | `payment_id`, `error_code` |
| `receipt_delivered` | Чек 54-ФЗ отправлен | `delivery` (`sms`,`email`,`screen`) |
| `review_prompt_shown` / `review_submitted` | Опрос после оплаты | `rating` 1–5, `has_text`, `routed_to` (`internal`,`yandex_maps`,`2gis`) |

**Брони и каналы (ИИ-администратор)**

| Событие | `props` |
|---|---|
| `conversation_started` | `channel`, `entry` (`button`,`deeplink`,`call`) |
| `ai_intent_detected` | `intent` (`booking`,`menu_question`,`hours`,`complaint`,`other`), `confidence` |
| `reservation_created` | `reservation_id`, `party_size`, `lead_time_h`, `created_by` (`ai`,`staff`,`widget`) |
| `reservation_cancelled` / `reservation_no_show` / `reservation_seated` | `reservation_id` |
| `handoff_to_human` | `reason`, `wait_s` |
| `call_completed` | `duration_s`, `outcome`, `asr_provider` |

**Отзывы, CRM, лояльность, операционка**

| Событие | `props` |
|---|---|
| `external_review_ingested` | `source` (`yandex_maps`,`2gis`,`google`), `rating`, `sentiment` |
| `review_reply_generated` / `review_reply_published` | `review_id`, `edited_by_human` (bool), `time_to_reply_h` |
| `loyalty_enrolled` / `points_earned` / `points_redeemed` | `program_id`, `points` |
| `campaign_sent` / `campaign_message_opened` / `campaign_converted` | `campaign_id`, `channel` |
| `invoice_ocr_processed` | `fields_total`, `fields_corrected`, `ms` |
| `shift_plan_published` | `hours_planned`, `forecast_covers` |

**Пользователь админки (продуктовая аналитика)**

| Событие | `props` |
|---|---|
| `admin_logged_in` | `role` |
| `onboarding_step_completed` | `step` (`menu_imported`,`pos_connected`,`qr_printed`,`payments_enabled`,`bot_connected`,`first_order`) |
| `dashboard_viewed` | `widget_count`, `period` |
| `ai_analyst_question_asked` | `question_len`, `sql_ok`, `latency_ms`, `thumbs` |
| `digest_opened` | `channel` |
| `feature_toggled` | `feature`, `enabled` |
| `menu_item_updated` | `field`, `via_ai` |

### 4.3. Правила качества

| Правило | Порог / действие |
|---|---|
| Доля невалидных событий | < 0,5%; выше — алерт в #data |
| Сверка `order_submitted` (события) с `order` (Postgres) | расхождение < 3% за день |
| Сверка `payment_succeeded` с выпиской эквайера | 100% (сервер-сайд), иначе инцидент |
| Сверка выручки POS с нашей | ±0,5% за бизнес-день по заказам, прошедшим через нас |
| Опоздавшие события | принимаем до 72 ч, витрины пересчитываются за последние 3 дня |

---

## 5. Метрики и формулы

Обозначения: `paid_orders` — заказы с `payment_status IN ('paid','partially_refunded')` за бизнес-день; суммы — за вычетом возвратов; НДС не вычитаем (владелец видит выручку «как в кассе»).

### 5.1. Деньги

| Метрика | Формула | Источник | Комментарий |
|---|---|---|---|
| **Выручка (все каналы)** | Σ `pos_receipt.total` − Σ возвратов | POS-импорт | Включает заказы мимо нас; если POS не подключён — только наша |
| **Выручка через Подача.AI** | Σ `payment.amount` (succeeded) − Σ `refund.amount` | `fct_payment` | Без чаевых |
| **Доля цифровой оплаты** | Выручка через Подача.AI / Выручка (все каналы) | | Ключевая метрика проникновения |
| **Средний чек (AOV)** | Выручка / число оплаченных чеков | | Отдельно: QR-заказы, официант, все |
| **Чек на гостя** | Выручка / Σ `covers` (гостей) | `order.guests_count` или POS | Для ресторанов важнее AOV |
| **Позиций в чеке (UPT)** | Σ qty / число чеков | `fct_order_item` | |
| **Выручка на посадочное место** | Выручка / (`seats` × дни) | `dim_venue.seats` | RevPASH упрощённо |
| **Выручка на час смены (RevPASH)** | Выручка в час / посадочные места | | Для графика смен |
| **Δ к прошлому периоду** | (X_t − X_{t−7д}) / X_{t−7д} | | Сравнение «день к тому же дню недели» |

### 5.2. Воронка QR

| Шаг | Определение (уникальные `session_id`) |
|---|---|
| S1 Скан | `qr_scanned` |
| S2 Меню открыто | `menu_opened` |
| S3 Интерес | ≥ 1 `menu_item_viewed` |
| S4 Корзина | ≥ 1 `cart_item_added` |
| S5 Заказ | `order_submitted` |
| S6 Оплата | `payment_succeeded` с `order_id` сессии |

| Метрика | Формула | Цель MVP [оценка] |
|---|---|---|
| **Конверсия QR → заказ** | S5 / S1 (только `qr_type IN ('table','takeaway')`, сессии без «мусорных» сканов < 3 с) | ≥ 25% (кафе с заказом через QR), ≥ 10% (смешанный сервис) |
| Конверсия QR → оплата | S6 / S1 | |
| Конверсия корзина → заказ | S5 / S4 | ≥ 60% |
| Брошенная корзина | 1 − S5/S4 | |
| Время до заказа | медиана (`order_submitted` − `menu_opened`) | < 6 мин |
| Скорость загрузки меню | p75 `load_ms` | < 2 000 мс на 4G |
| Доля pay-only сканов | сканы `pay_only` / все сканы | |

### 5.3. Скорость оплаты и обслуживания

| Метрика | Формула | Цель [оценка] |
|---|---|---|
| **Время оплаты (гость)** | медиана, p90 (`payment_succeeded.occurred_at` − `bill_opened.occurred_at`) | медиана < 60 с |
| Время провайдера | медиана `payment_succeeded.duration_ms` по `provider` | СБП < 15 с |
| **Время «попросил счёт → оплатил»** | `payment_succeeded` − `waiter_called(reason=bill)`; для сравнения с официантом — из POS: `closed_at − precheck_at` | экономия 5–10 мин на столе |
| Успешность оплаты | succeeded / started | ≥ 95% |
| Доля СБП | Σ amount СБП / Σ amount | Комиссия СБП ниже карт — показываем экономию: Σ amount × (rate_card − rate_sbp) |
| Время отдачи заказа | медиана (`served` − `accepted`) из `order_status_history` | |
| Оборачиваемость стола | число сессий-оплат на стол за бизнес-день | |

### 5.4. ИИ-апселл и эффект

| Метрика | Формула |
|---|---|
| Показы рекомендаций | count `ai_reco_shown` |
| CTR рекомендаций | `ai_reco_clicked` / `ai_reco_shown` |
| Take rate | число позиций, добавленных с `reco_id` / показы |
| **Выручка апселла ИИ (атрибутированная)** | Σ `order_item.amount` для позиций, где `cart_item_added.reco_id IS NOT NULL` в той же сессии, ≤ 3 ч, заказ оплачен, позиция не `voided` |
| Доля апселла в выручке QR | Выручка апселла ИИ / Выручка QR-заказов |
| **Инкрементальный AOV (holdout)** | AOV(treatment) − AOV(holdout); 95% ДИ бутстрепом (1 000 итераций) по сессиям; показываем, только если n ≥ 300 сессий в каждой группе |
| **Инкрементальная выручка ИИ, ₽/мес** | Инкрементальный AOV × число оплаченных QR-чеков treatment за месяц |
| **ROI подписки** | (Инкрементальная выручка × маржинальность блюд (по умолч. 65% = 1 − фудкост 35%, настраиваемо) + экономия ФОТ) / стоимость подписки |
| ИИ-официант: доля вопросов без эскалации | 1 − `handoff_to_human` / `ai_chat_message_sent` (по сессиям) |

Если holdout выключен владельцем, плитка «Эффект ИИ» показывает только атрибутированную выручку с подписью «верхняя оценка».

### 5.5. Чаевые

| Метрика | Формула |
|---|---|
| Сумма чаевых | Σ `tip.amount` (succeeded) |
| **Доля чеков с чаевыми** | чеки с `tip_amount > 0` / оплаченные цифровые чеки |
| Средний % чаевых | Σ tip_amount / Σ payment.amount (по чекам с чаевыми и по всем) |
| Чаевые на сотрудника/час | Σ `tip_allocation.amount` / часы `time_entry` |
| Рейтинг официанта | среднее `tip.rating`, n ≥ 20 |
| Распределение выбора | доли 0/5/10/15/custom из `tip_selected` |

Показываются только owner/manager; сотрудник видит только свои чаевые (в приложении персонала).

### 5.6. Отзывы и репутация

| Метрика | Формула |
|---|---|
| Средний рейтинг (внешний) | среднее `rating` за 90 дн. по источнику; тренд 30 дн. |
| Внутренний CSAT | доля `review_submitted.rating ≥ 4` |
| Перехваченный негатив | отзывы 1–3★, ушедшие во внутренний канал / все 1–3★ (после оплаты) |
| Доля отзывов с ответом | отвечено / получено (за 30 дн.) |
| Время ответа | медиана `time_to_reply_h` | цель < 24 ч |
| Доля ИИ-ответов без правки | `edited_by_human = false` / опубликовано |
| Тональность по темам | доля негативных упоминаний тем (`скорость`,`вкус`,`персонал`,`цена`,`чистота`) — классификатор YandexGPT/GigaChat |

Важно: вести гостей с высокой оценкой на Яндекс Карты, а с низкой — во внутренний канал, может нарушать правила площадок (запрет селективного запроса отзывов). Решение: в UI показываем гостю **оба** варианта при любой оценке, а «перехват» = только сбор обратной связи; формулировку проверить с юристом и правилами Яндекс Карт/2ГИС.

### 5.7. Гости, CRM, лояльность, брони

| Метрика | Формула |
|---|---|
| Узнаваемые гости | доля чеков с `guest_key` |
| Возвратность 30/60/90 | гости с ≥ 2 визитами за окно / гости с визитом |
| Частота визитов | визиты / уникальные гости за 90 дн. |
| LTV гостя 12 мес. | Σ выручки гостя за 12 мес. (когортно) |
| Конверсия кампании | `campaign_converted` (заказ ≤ 7 дн.) / `campaign_sent`; с контрольной группой 10% |
| Брони через ИИ | `reservation_created(created_by=ai)` / все брони |
| Конверсия диалога в бронь | брони / `conversation_started(intent=booking)` |
| No-show rate | no_show / (seated + no_show) |
| Пропущенные звонки, закрытые ИИ | `call_completed` вне часов/при занятой линии |

### 5.8. Продуктовые метрики Подача.AI (внутренние)

| Метрика | Определение |
|---|---|
| **North Star: GMV через Подача.AI** | Σ успешных платежей всех клиентов, ₽/нед |
| Активное заведение (WAV) | ≥ 20 оплаченных цифровых чеков за 7 дн. **или** ≥ 10 ИИ-диалогов |
| Time-to-value | дни от договора до `first_order`; цель ≤ 7 дн. |
| Онбординг-воронка | шаги `onboarding_step_completed` |
| Глубина внедрения | число модулей с активностью за 14 дн. |
| Health score (0–100) | 35% тренд GMV 4 нед + 20% доля цифровой оплаты + 15% активность владельца (дашборд/дайджест) + 15% модули + 15% ошибки POS/оплаты (инверсия). < 40 — риск оттока, задача CS |
| Logo / Net revenue retention | помесячно, когортно |
| LLM-стоимость на заведение | Σ токенов × цена (GigaChat/YandexGPT) / заведения; контроль маржи ≥ 60% |
| Использование ИИ-аналитика | вопросов на активного владельца в неделю; доля `sql_ok` |

---

## 6. Дашборд владельца

### 6.1. Структура экрана (по умолчанию «Вчера», переключатели: сегодня / 7 / 30 дн. / период; точка / все точки)

| Блок | Плитки (значение + Δ к прошлой неделе + спарклайн 8 нед) | Тариф |
|---|---|---|
| 1. Деньги | Выручка; Средний чек; Чек на гостя; Доля цифровой оплаты | все (выручка POS — с подключённой кассой) |
| 2. Эффект Подача.AI | Выручка апселла ИИ; Инкрементальный AOV (с ДИ); Экономия на комиссии СБП; ROI подписки «×N» | Старт+ |
| 3. QR-воронка | Воронка S1–S6 (бар); конверсия QR→заказ; время до заказа | все |
| 4. Оплата | Медиана/p90 времени оплаты; успешность; доли способов | с оплатой |
| 5. Чаевые | Сумма; доля чеков с чаевыми; средний %; топ-5 сотрудников | с чаевыми |
| 6. Отзывы | Рейтинг Яндекс/2ГИС; новые отзывы; без ответа; темы негатива | все |
| 7. Гости и брони | Брони (ИИ/всего); no-show; возвратность 30 дн. | Pro |
| 8. Меню | Топ/антитоп позиций (ABC по выручке и марже); «смотрят, но не заказывают» (просмотры / добавления) | все |
| 9. Аномалии | До 3 карточек (§6.2) | Старт+ |

Состояния: «мало данных» при n < 30 чеков — значение серым с подписью; при отсутствии POS — баннер «Подключите кассу, чтобы видеть всю выручку».

### 6.2. Аномалии и дайджест (ANL-3)

| Правило | Условие |
|---|---|
| Падение выручки | выручка дня < медиана тех же дней недели за 6 нед. − 2×MAD |
| Падение конверсии QR | S5/S1 < 70% от 4-недельного среднего при S1 ≥ 50 |
| Сбой оплаты | успешность < 90% за час при ≥ 10 попытках (алерт сразу, не в дайджест) |
| Ошибки POS | `order_rejected_by_pos` ≥ 3 за час |
| Негатив | ≥ 2 отзывов 1–2★ за сутки или рост темы > 2× |
| Стоп-лист | позиция из топ-10 в стоп-листе > 2 ч в пик |

Дайджест в 9:00 по `venue.timezone` в Telegram/MAX: 5 метрик (выручка, чек, доля цифровой оплаты, чаевые, рейтинг) + до 3 аномалий + 1 рекомендация ИИ-аналитика. Генерация текста — YandexGPT/GigaChat по агрегатам (ПДн не передаются).

---

## 7. Витрины (ClickHouse SQL)

Все витрины содержат `organization_id`, `venue_id`, `business_date`. Доступ: строковые политики ClickHouse (`ROW POLICY ... USING organization_id = getSetting('custom_org_id')`), API выставляет `custom_org_id` из JWT.

### 7.1. Бизнес-день

```sql
-- dim_venue: timezone, business_day_start_hour (по умолчанию 6)
CREATE FUNCTION business_date AS (ts, tz, h) ->
    toDate(toTimeZone(ts, tz) - toIntervalHour(h));
```

### 7.2. `core.fct_order` (из CDC)

```sql
CREATE TABLE core.fct_order
(
    order_id UUID, organization_id UUID, venue_id UUID, table_id Nullable(UUID),
    session_id Nullable(UUID), guest_key Nullable(FixedString(64)), waiter_id Nullable(UUID),
    channel LowCardinality(String), type LowCardinality(String),
    status LowCardinality(String), payment_status LowCardinality(String),
    guests_count UInt16, subtotal Int64, discount_total Int64, total Int64,
    paid_amount Int64, refunded_amount Int64, tip_amount Int64,
    created_at DateTime64(3, 'UTC'), first_paid_at Nullable(DateTime64(3, 'UTC')),
    business_date Date, updated_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(business_date)
ORDER BY (organization_id, venue_id, business_date, order_id);
```

### 7.3. `mart.venue_daily` — денежные метрики

```sql
CREATE MATERIALIZED VIEW mart.venue_daily_mv
REFRESH EVERY 15 MINUTE TO mart.venue_daily AS
SELECT
    organization_id, venue_id, business_date,
    countIf(payment_status IN ('paid','partially_refunded'))           AS paid_orders,
    sumIf(paid_amount - refunded_amount,
          payment_status IN ('paid','partially_refunded'))             AS revenue_digital,
    sumIf(guests_count, payment_status IN ('paid','partially_refunded')) AS covers,
    countIf(channel = 'qr' AND payment_status IN ('paid','partially_refunded')) AS qr_paid_orders,
    sumIf(paid_amount - refunded_amount,
          channel = 'qr' AND payment_status IN ('paid','partially_refunded')) AS revenue_qr,
    sum(tip_amount)                                                     AS tips,
    countIf(tip_amount > 0)                                             AS orders_with_tip
FROM core.fct_order FINAL
WHERE business_date >= today() - 3
GROUP BY organization_id, venue_id, business_date;
```

Плитки (вычисляются в API/представлении `mart.v_owner_kpi`):

```sql
CREATE VIEW mart.v_owner_kpi AS
SELECT d.organization_id, d.venue_id, d.business_date,
    coalesce(p.revenue_pos, d.revenue_digital)                    AS revenue_total,
    d.revenue_digital,
    d.revenue_digital / nullIf(p.revenue_pos, 0)                  AS digital_share,
    coalesce(p.revenue_pos, d.revenue_digital)
        / nullIf(coalesce(p.receipts_pos, d.paid_orders), 0)      AS aov,
    coalesce(p.revenue_pos, d.revenue_digital)
        / nullIf(coalesce(p.covers_pos, d.covers), 0)             AS rev_per_cover,
    d.tips, d.orders_with_tip / nullIf(d.paid_orders, 0)          AS tip_rate,
    d.tips / nullIf(d.revenue_digital, 0)                         AS tip_pct
FROM mart.venue_daily d
LEFT JOIN mart.pos_daily p USING (organization_id, venue_id, business_date);
```

### 7.4. `mart.qr_funnel_daily`

```sql
CREATE MATERIALIZED VIEW mart.qr_funnel_daily_mv
REFRESH EVERY 15 MINUTE TO mart.qr_funnel_daily AS
WITH s AS (
    SELECT organization_id, venue_id, session_id,
        business_date(min(occurred_at), any(v.timezone), any(v.business_day_start_hour)) AS business_date,
        maxIf(1, event_name = 'qr_scanned')         AS s1,
        maxIf(1, event_name = 'menu_opened')        AS s2,
        maxIf(1, event_name = 'menu_item_viewed')   AS s3,
        maxIf(1, event_name = 'cart_item_added')    AS s4,
        maxIf(1, event_name = 'order_submitted')    AS s5,
        maxIf(1, event_name = 'payment_succeeded')  AS s6,
        dateDiff('second', min(occurred_at), max(occurred_at)) AS dur_s,
        anyIf(JSONExtractString(props, 'qr_type'), event_name = 'qr_scanned') AS qr_type
    FROM raw.events e
    JOIN core.dim_venue v USING (venue_id)
    WHERE e.channel = 'qr' AND e.occurred_at >= now() - INTERVAL 4 DAY
    GROUP BY organization_id, venue_id, session_id
)
SELECT organization_id, venue_id, business_date,
    countIf(s1 = 1)                              AS scans,
    countIf(s1 = 1 AND dur_s >= 3)               AS scans_valid,
    countIf(s2 = 1) AS menu_opened, countIf(s3 = 1) AS item_viewed,
    countIf(s4 = 1) AS cart_added,  countIf(s5 = 1) AS ordered, countIf(s6 = 1) AS paid,
    countIf(s5 = 1) / nullIf(countIf(s1 = 1 AND dur_s >= 3 AND qr_type IN ('table','takeaway')), 0)
                                                 AS conv_qr_to_order
FROM s GROUP BY organization_id, venue_id, business_date;
```

Медианы времени до заказа считаются отдельной витриной `mart.qr_timing_daily` через `quantileTDigest`.

### 7.5. `mart.payment_speed_daily`

```sql
CREATE MATERIALIZED VIEW mart.payment_speed_daily_mv
REFRESH EVERY 15 MINUTE TO mart.payment_speed_daily AS
WITH b AS (
    SELECT organization_id, venue_id, session_id,
        JSONExtractString(props, 'order_id') AS order_id,
        minIf(occurred_at, event_name = 'bill_opened')       AS t_bill,
        minIf(occurred_at, event_name = 'payment_succeeded') AS t_paid,
        anyIf(JSONExtractString(props,'provider'), event_name = 'payment_method_selected') AS provider,
        countIf(event_name = 'payment_started')              AS attempts,
        countIf(event_name = 'payment_succeeded')            AS successes
    FROM raw.events
    WHERE event_name IN ('bill_opened','payment_method_selected','payment_started','payment_succeeded')
      AND occurred_at >= now() - INTERVAL 4 DAY
    GROUP BY organization_id, venue_id, session_id, order_id
)
SELECT organization_id, venue_id, toDate(t_bill) AS business_date, provider,
    quantileTDigestIf(0.5)(dateDiff('second', t_bill, t_paid), t_paid > t_bill) AS pay_time_p50_s,
    quantileTDigestIf(0.9)(dateDiff('second', t_bill, t_paid), t_paid > t_bill) AS pay_time_p90_s,
    sum(successes) / nullIf(sum(attempts), 0)  AS payment_success_rate
FROM b WHERE t_bill > 0
GROUP BY organization_id, venue_id, business_date, provider;
```

(В проде `business_date` через функцию §7.1, а не `toDate`.)

### 7.6. `mart.ai_upsell_daily`

```sql
CREATE MATERIALIZED VIEW mart.ai_upsell_daily_mv
REFRESH EVERY 1 DAY OFFSET 4 HOUR TO mart.ai_upsell_daily AS
WITH adds AS (   -- позиции, добавленные из рекомендации
    SELECT session_id,
           JSONExtractString(props,'item_id') AS item_id,
           JSONExtractString(props,'reco_id') AS reco_id,
           occurred_at AS added_at
    FROM raw.events
    WHERE event_name = 'cart_item_added' AND JSONExtractString(props,'reco_id') != ''
      AND occurred_at >= today() - 4
),
shown AS (
    SELECT JSONExtractString(props,'reco_id') AS reco_id, min(occurred_at) AS shown_at,
           any(experiment['ai_upsell']) AS arm
    FROM raw.events WHERE event_name = 'ai_reco_shown' AND occurred_at >= today() - 4
    GROUP BY reco_id
)
SELECT o.organization_id, o.venue_id, o.business_date,
    uniqExact(a.reco_id)                     AS recos_converted,
    count()                                  AS upsell_items,
    sum(oi.amount)                           AS upsell_revenue
FROM adds a
JOIN shown s USING (reco_id)
JOIN core.fct_order o FINAL ON o.session_id = a.session_id
JOIN core.fct_order_item oi FINAL ON oi.order_id = o.order_id AND toString(oi.menu_item_id) = a.item_id
WHERE a.added_at BETWEEN s.shown_at AND s.shown_at + INTERVAL 3 HOUR
  AND o.payment_status IN ('paid','partially_refunded')
  AND oi.status != 'voided'
GROUP BY o.organization_id, o.venue_id, o.business_date;
```

Инкрементальный эффект (holdout), за 28 дней:

```sql
SELECT organization_id, venue_id,
    avgIf(o.total, arm = 'treatment') AS aov_treatment,
    avgIf(o.total, arm = 'holdout')   AS aov_holdout,
    countIf(arm = 'treatment') AS n_t, countIf(arm = 'holdout') AS n_h
FROM core.fct_order o FINAL
JOIN core.fct_session s ON s.session_id = o.session_id   -- arm проставлен в сессии
WHERE o.channel = 'qr' AND o.payment_status = 'paid'
  AND o.business_date >= today() - 28
GROUP BY organization_id, venue_id
HAVING n_t >= 300 AND n_h >= 300;
```
ДИ — бутстреп в Python-джобе (Airflow/Yandex Data Processing), результат в `mart.ai_effect_28d`.

### 7.7. `mart.tips_daily`, `mart.reviews_daily`, `mart.menu_item_daily`

```sql
-- чаевые по сотрудникам
SELECT t.organization_id, t.venue_id, t.business_date, a.employee_id,
       sum(a.amount) AS tips, avg(t.rating) AS avg_rating, count() AS tips_count
FROM core.fct_tip t JOIN core.fct_tip_allocation a USING (tip_id)
WHERE t.status = 'succeeded'
GROUP BY t.organization_id, t.venue_id, t.business_date, a.employee_id;

-- меню: «смотрят, но не заказывают»
SELECT organization_id, venue_id, business_date,
       JSONExtractString(props,'item_id') AS item_id,
       countIf(event_name = 'menu_item_viewed') AS views,
       countIf(event_name = 'cart_item_added')  AS adds,
       adds / nullIf(views, 0)                  AS view_to_add
FROM raw.events
WHERE event_name IN ('menu_item_viewed','cart_item_added')
GROUP BY organization_id, venue_id, business_date, item_id;

-- отзывы
SELECT organization_id, venue_id, business_date, source,
       count() AS reviews, avg(rating) AS avg_rating,
       countIf(replied_at IS NOT NULL) / count() AS reply_rate,
       quantileTDigest(0.5)(dateDiff('hour', created_at, replied_at)) AS reply_time_p50_h,
       countIf(rating <= 3) AS negative
FROM core.fct_review
GROUP BY organization_id, venue_id, business_date, source;
```

### 7.8. Белый список для ИИ-аналитика (ANL-2)

| Представление | Описание для LLM (семантический слой) |
|---|---|
| `mart.v_owner_kpi` | Выручка, чек, чаевые по дням |
| `mart.qr_funnel_daily` | Воронка QR |
| `mart.payment_speed_daily` | Скорость и успешность оплаты |
| `mart.ai_upsell_daily`, `mart.ai_effect_28d` | Эффект ИИ |
| `mart.menu_item_daily` | Продажи и просмотры блюд |
| `mart.tips_daily` | Чаевые по сотрудникам (имена подставляет API после запроса) |
| `mart.reviews_daily` | Отзывы |

Ограничения text-to-SQL: только `SELECT`, парсер AST отклоняет таблицы вне списка, `max_execution_time=5`, `max_result_rows=1000`, `organization_id` подставляется политикой, а не моделью. LLM — YandexGPT Pro / GigaChat Pro (в промпт идут схема и агрегаты, ПДн нет). Каждый ответ показывает период, формулу и SQL по кнопке «Как посчитано».

---

## 8. Приватность и соответствие

| Требование | Реализация |
|---|---|
| 152-ФЗ, локализация | ClickHouse, collector, бэкапы — в РФ (Yandex Cloud ru-central1) |
| Минимизация | В аналитике нет ФИО/телефонов/email; `guest_key` — HMAC с солью тенанта, соль в KMS |
| Удаление по запросу гостя | Удаление `guest_key`→`guest_id` маппинга в Postgres делает аналитические данные обезличенными; плюс `ALTER TABLE ... DELETE WHERE guest_key = …` раз в неделю пакетно |
| Cookies/идентификаторы | Баннер в QR-меню с информированием; маркетинговые идентификаторы и ретаргетинг — только после согласия |
| Сторонние счётчики | Не используются в QR-меню и ботах |
| Доступ сотрудников Подача.AI | Только к агрегатам по тенантам; доступ к сырым событиям — по заявке, журналируется |
| Чаевые и персональная эффективность | Видит owner/manager; сотрудник — только своё |

---

## 9. Внедрение по этапам

| Этап | Срок [оценка] | Состав |
|---|---|---|
| MVP (M) | 4 нед | Collector, события QR и оплаты, `fct_order`/`fct_payment`, `venue_daily`, `qr_funnel_daily`, `payment_speed_daily`, дашборд блоки 1,3,4,5, сверки §4.3 |
| L1 | +4 нед | ИИ-апселл и holdout, отзывы, дайджест и аномалии, ИИ-аналитик по белому списку, health score для CS |
| L2 | +6 нед | Когорты гостей, LTV, кампании с контрольной группой, сравнение точек сети, прогноз (FC-2), RevPASH для графиков смен |

## 10. Открытые вопросы (решены по умолчанию)

| Вопрос | Решение по умолчанию |
|---|---|
| Учитывать ли сервисный сбор в выручке | Да, как в кассе; отдельной строкой в детализации |
| Валюта/НДС | Рубли с НДС, как в чеке |
| Учитывать ли отменённые после оплаты | Вычитаются через `refund` в день возврата, не задним числом |
| Holdout мешает продажам | Владелец может отключить; эффект тогда помечается «верхняя оценка» |
| Нет POS-интеграции | Доля цифровой оплаты не показывается; выручка = наша |
