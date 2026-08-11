"""Вход, сессии и одноразовые коды.

До этого модуля тенант и пользователь приезжали заголовками `X-Tenant-Id`
и `X-User-Id`. Их знает любой, кто открыл вкладку разработчика, и подставить
чужую школу было делом одной строки в консоли: изоляция строк защищала
от кривого запроса, а не от подделанного заголовка.

Теперь тенант — следствие входа, а не утверждение клиента. Браузер предъявляет
непрозрачный токен сессии, приложение находит по нему строку `user_session`
и только оттуда узнаёт школу и человека. Подменить школу теперь нельзя, не имея
чужого токена, а токена в базе нет — есть его хеш.

Ключи внешних источников (`api_keys.py`) остаются как были: они для систем,
а не для людей, и вход по телефону им не нужен.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
from typing import Any

import psycopg

from .errors import ApiError

# ---------------------------------------------------------------------------
# Сроки и лимиты. Собраны здесь, а не разбросаны по коду: это те числа,
# которые школа однажды попросит подвинуть, и искать их придётся в одном месте.
# ---------------------------------------------------------------------------

COOKIE_NAME = "rockcrm_session"
SESSION_PREFIX = "rck_sess"
# Тридцать дней. Родитель заходит в кабинет раз в месяц — посмотреть остаток
# перед продлением; сессия на сутки означала бы вход по коду при каждом визите,
# то есть SMS за каждый просмотр расписания.
SESSION_TTL = dt.timedelta(days=30)

CODE_LENGTH = 6
# Пять минут: столько идёт SMS и столько человек ищет телефон. Час жизни кода
# означал бы час, в течение которого его можно перебирать.
CODE_TTL = dt.timedelta(minutes=5)
# Промахов по одному коду. Шесть цифр — это миллион вариантов, и без этого
# счётчика перебор занял бы минуты. Лимит стоит именно на коде, а не только
# на телефоне: лимит на телефон обходится запросом нового кода, лимит на код — нет.
CODE_MAX_ATTEMPTS = 5

# Окно, в котором считаются попытки. Одно на все виды — разные окна пришлось бы
# объяснять, а выигрыша они не дают.
THROTTLE_WINDOW = dt.timedelta(minutes=15)
# Кодов на один телефон за окно. Три — это «не пришло, пришлите ещё раз»
# дважды; четвёртый запрос за четверть часа означает не забывчивость.
CODE_REQUESTS_PER_LOGIN = 3
# Кодов с одного адреса: защита от перебора телефонов школы, когда каждый
# номер пробуют по одному разу и в лимит на телефон не упираются никогда.
CODE_REQUESTS_PER_IP = 20
# Неудачных проверок кода на телефон поверх счётчика самого кода: без этого
# перебор продолжался бы запросом нового кода после каждых пяти промахов.
CODE_FAILURES_PER_LOGIN = 10
# Неудачных попыток пароля. Пароль есть только у сотрудников, и он выбран
# человеком — то есть перебираем по словарю.
PASSWORD_FAILURES_PER_LOGIN = 5

# ---------------------------------------------------------------------------
# Хеширование
# ---------------------------------------------------------------------------

# scrypt из стандартной библиотеки, без новой зависимости. Параметры —
# рекомендация RFC 7914 для интерактивного входа: около 60 мс и 16 МБ памяти
# на проверку. Медленно ровно настолько, чтобы перебор словаря стоил дорого,
# и быстро ровно настолько, чтобы вход не ощущался задержкой.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_SECRET_BYTES = 32


def hash_secret(raw: str) -> str:
    """Хеш пароля или одноразового кода: scrypt с солью, в одну строку.

    Соль лежит рядом с хешем — так делают все, и это не секрет: её задача
    в том, чтобы одинаковые пароли двух человек дали разные хеши, а радужная
    таблица перестала работать. Параметры тоже записаны в строку: когда
    их придётся поднять, старые хеши обязаны продолжать проверяться.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        raw.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=_SCRYPT_N * _SCRYPT_R * 256,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_secret(raw: str | None, stored: str | None) -> bool:
    """Сверка с хешем. Ложь на любой мусор, а не исключение.

    Пустой `stored` — это учётная запись без пароля (вход только по коду),
    и она обязана отвечать «не подошло», а не падать пятисоткой: иначе
    по коду ответа видно, у кого пароль есть, а у кого нет.
    """
    if not raw or not stored:
        return False
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            raw.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p),
            maxmem=int(n) * int(r) * 256,
        )
    except (ValueError, TypeError):
        return False
    # Сравнение постоянного времени: обычное «==» останавливается на первом
    # различии, и по времени ответа хеш подбирается побайтно.
    return hmac.compare_digest(actual, expected)


def hash_token(raw: str) -> str:
    """SHA-256 для токена сессии — и это осознанно другой хеш, чем у пароля.

    Медленный хеш защищает то, что можно перебрать. Токен — 32 случайных байта,
    перебирать нечего, зато проверяется он на КАЖДОМ запросе и ищется
    по индексу: scrypt здесь стоил бы 60 мс на каждое обращение к API
    и всё равно ничего бы не добавил. Ровно то же рассуждение, что
    и у ключей внешних источников (api_keys.hash_key).
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    """Токен сессии. Префикс отличает его от ключа источника при разборе логов."""
    return f"{SESSION_PREFIX}_{secrets.token_urlsafe(_SECRET_BYTES)}"


def new_code() -> str:
    """Шестизначный код. secrets, а не random: random предсказуем по двум
    предыдущим значениям, и код соседа вычислялся бы, а не подбирался."""
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


# ---------------------------------------------------------------------------
# Школа и учётная запись
# ---------------------------------------------------------------------------


def tenant_by_slug(cur: psycopg.Cursor, slug: str) -> dict[str, Any] | None:
    """Школа по слагу. Единственный запрос входа, который идёт до set_config.

    Политик уровня строк на `tenant` нет (db/005_rls.sql): приложение читает
    эту таблицу как раз затем, чтобы узнать тенанта. Слаг приходит от клиента,
    но выбрать по нему можно только существующую школу — а дальше всё, что
    человек увидит, ограничено тем, есть ли у него в этой школе учётная запись.
    """
    cur.execute(
        "SELECT id, slug, name, timezone FROM tenant WHERE slug = %s AND is_active",
        (slug,),
    )
    return cur.fetchone()


def user_by_login(cur: psycopg.Cursor, login: str) -> dict[str, Any] | None:
    """Учётная запись по логину или по телефону человека.

    Два условия, а не одно: `app_user.login` может быть почтой сотрудника,
    а телефон при этом лежит в `person`. Родитель называет телефон и ничего
    больше не знает — и именно телефон обязан работать для всех ролей.
    """
    cur.execute(
        """
        SELECT u.id, u.tenant_id, u.person_id, u.login, u.password_hash,
               u.role, u.is_active, p.phone,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS name
        FROM app_user u
        JOIN person p ON p.id = u.person_id
        WHERE u.login = %(login)s OR p.phone = %(login)s
        ORDER BY (u.login = %(login)s) DESC
        LIMIT 1
        """,
        {"login": login},
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Ограничение попыток
# ---------------------------------------------------------------------------


def record_attempt(
    cur: psycopg.Cursor,
    tenant_id: str,
    kind: str,
    subject: str,
    ip: str | None,
    ok: bool,
) -> None:
    """Попытка входа в журнал. Пишется и удачная, и неудачная.

    Удачные нужны не для лимитов, а для ответа на вопрос «кто заходил
    в мой кабинет»: без них журнал показывает только тех, у кого не вышло.
    """
    cur.execute(
        """
        INSERT INTO auth_attempt (tenant_id, kind, subject, ip, ok)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (tenant_id, kind, subject, ip, ok),
    )


def _count_attempts(
    cur: psycopg.Cursor,
    kind: str,
    *,
    subject: str | None = None,
    ip: str | None = None,
    only_failed: bool = False,
) -> int:
    cur.execute(
        """
        SELECT count(*) AS n
        FROM auth_attempt
        WHERE kind = %(kind)s
          AND created_at > now() - %(window)s::interval
          AND (%(subject)s::text IS NULL OR subject = %(subject)s::text)
          AND (%(ip)s::inet    IS NULL OR ip = %(ip)s::inet)
          AND (NOT %(failed)s OR NOT ok)
        """,
        {
            "kind": kind,
            "window": THROTTLE_WINDOW,
            "subject": subject,
            "ip": ip,
            "failed": only_failed,
        },
    )
    return int(cur.fetchone()["n"])


def _too_many(message: str) -> ApiError:
    """429, а не 401. Разница важна: 401 человек читает как «код не тот»
    и набирает следующий, а 429 говорит, что дело не в коде."""
    return ApiError(429, "too_many_attempts", message)


def guard_code_request(cur: psycopg.Cursor, subject: str, ip: str | None) -> None:
    """Лимит на выдачу кодов. Считается до того, как известно, есть ли такой
    пользователь: иначе перебор телефонов ограничен не был бы ничем."""
    if _count_attempts(cur, "code_request", subject=subject) >= CODE_REQUESTS_PER_LOGIN:
        raise _too_many(
            "Код уже запрашивали несколько раз подряд. Подождите 15 минут "
            "или войдите по паролю."
        )
    if ip and _count_attempts(cur, "code_request", ip=ip) >= CODE_REQUESTS_PER_IP:
        raise _too_many("Слишком много запросов кода с этого адреса. Подождите 15 минут.")


def guard_code_verify(cur: psycopg.Cursor, subject: str) -> None:
    if (
        _count_attempts(cur, "code_verify", subject=subject, only_failed=True)
        >= CODE_FAILURES_PER_LOGIN
    ):
        raise _too_many("Слишком много неверных кодов. Подождите 15 минут и запросите новый.")


def guard_password(cur: psycopg.Cursor, subject: str) -> None:
    if (
        _count_attempts(cur, "password", subject=subject, only_failed=True)
        >= PASSWORD_FAILURES_PER_LOGIN
    ):
        raise _too_many("Слишком много неверных попыток. Подождите 15 минут.")


# ---------------------------------------------------------------------------
# Одноразовый код
# ---------------------------------------------------------------------------


def issue_code(
    cur: psycopg.Cursor, tenant_id: str, user: dict[str, Any], ip: str | None
) -> str:
    """Выдаёт новый код, гасит прежние и ставит сообщение в очередь.

    Прежние коды гасятся намеренно: два живых кода на одного человека — это
    вдвое больше вариантов, которые подойдут, и вдвое меньше смысла у счётчика
    попыток. Живым остаётся последний.
    """
    cur.execute(
        """
        UPDATE auth_code SET consumed_at = now()
        WHERE user_id = %s AND consumed_at IS NULL AND expires_at > now()
        """,
        (user["id"],),
    )
    code = new_code()
    cur.execute(
        """
        INSERT INTO auth_code (tenant_id, user_id, code_hash, expires_at, ip)
        VALUES (%s, %s, %s, now() + %s::interval, %s)
        """,
        (tenant_id, user["id"], hash_secret(code), CODE_TTL, ip),
    )
    _enqueue_code(cur, tenant_id, user, code)
    return code


def _enqueue_code(
    cur: psycopg.Cursor, tenant_id: str, user: dict[str, Any], code: str
) -> None:
    """Кладёт код в очередь уведомлений — ту же, в которой живут напоминания
    об уроке. Воркера, который относит их в SMS, пока нет (см. README), но
    выдумывать второй канал доставки ради входа незачем: очередь и есть канал.

    Открытый код лежит в очереди до отправки, и это не противоречит хешу
    в `auth_code`: хеш защищает долговременное хранилище (дамп базы месячной
    давности не даёт войти), а строка очереди — это само сообщение, которое
    через минуту уедет человеку и должно быть стёрто отправщиком.
    """
    address = user.get("phone") or user["login"]
    cur.execute(
        """
        INSERT INTO notification (tenant_id, person_id, channel, template, payload, to_address)
        VALUES (%s, %s, 'sms', 'auth_code', %s, %s)
        """,
        (
            tenant_id,
            user["person_id"],
            json.dumps(
                {"code": code, "expires_in_min": int(CODE_TTL.total_seconds() // 60)},
                ensure_ascii=False,
            ),
            address,
        ),
    )


def consume_code(cur: psycopg.Cursor, user_id: str, code: str) -> bool:
    """Проверяет код и гасит его. True — код подошёл.

    Считает попытку ДО проверки: иначе прерванный на середине запрос
    (сеть, перезапуск) давал бы бесплатную попытку, и счётчик обходился бы
    обрывом соединения.
    """
    cur.execute(
        """
        SELECT id, code_hash, attempts
        FROM auth_code
        WHERE user_id = %s AND consumed_at IS NULL AND expires_at > now()
          AND attempts < %s
        ORDER BY created_at DESC
        LIMIT 1
        FOR UPDATE
        """,
        (user_id, CODE_MAX_ATTEMPTS),
    )
    row = cur.fetchone()
    if row is None:
        # Протухший, уже использованный, сожжённый попытками и вовсе
        # не выданный код отвечают одинаково: разница между ними — подсказка
        # тому, кто перебирает.
        return False

    cur.execute("UPDATE auth_code SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
    if not verify_secret(code, row["code_hash"]):
        return False
    cur.execute("UPDATE auth_code SET consumed_at = now() WHERE id = %s", (row["id"],))
    return True


# ---------------------------------------------------------------------------
# Сессии
# ---------------------------------------------------------------------------


def create_session(
    cur: psycopg.Cursor,
    tenant_id: str,
    user_id: str,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, dict[str, Any]]:
    """Новая сессия. Возвращает (открытый токен, строка сессии).

    Открытый токен существует ровно здесь и в ответе на вход. В базу уходит
    только хеш, восстановить токен из неё нечем — как и ключ источника.
    """
    token = new_session_token()
    cur.execute(
        """
        INSERT INTO user_session (tenant_id, user_id, token_hash, expires_at, ip, user_agent)
        VALUES (%s, %s, %s, now() + %s::interval, %s, %s)
        RETURNING id, tenant_id, user_id, issued_at, expires_at
        """,
        (tenant_id, user_id, hash_token(token), SESSION_TTL, ip, (user_agent or "")[:500]),
    )
    # Строка забирается ДО следующего запроса: курсор один, и UPDATE ниже
    # затёр бы результат RETURNING.
    session = cur.fetchone()
    cur.execute("UPDATE app_user SET last_login_at = now() WHERE id = %s", (user_id,))
    return token, session


def session_by_token(cur: psycopg.Cursor, raw: str) -> dict[str, Any] | None:
    """Находит живую сессию по предъявленному токену.

    Запрос идёт ДО set_config: тенант — это то, что сессия сообщает, а не то,
    что о ней известно заранее. Ровно тот же единственный обоснованный случай,
    что и у ключей источников, поэтому политик на user_session нет.
    """
    cur.execute(
        """
        SELECT id, tenant_id, user_id, expires_at
        FROM user_session
        WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()
        """,
        (hash_token(raw),),
    )
    return cur.fetchone()


# Как часто обновляется отметка «последний запрос». Писать её на КАЖДЫЙ запрос
# значило бы превратить любое чтение расписания в запись и упереться в диск
# на ровном месте; точность до пяти минут для ответа «когда заходили»
# избыточна и так.
TOUCH_INTERVAL = dt.timedelta(minutes=5)


def touch_session(cur: psycopg.Cursor, session_id: str) -> None:
    cur.execute(
        "UPDATE user_session SET last_seen_at = now() "
        "WHERE id = %s AND last_seen_at < now() - %s::interval",
        (session_id, TOUCH_INTERVAL),
    )


def revoke_session(cur: psycopg.Cursor, session_id: str) -> None:
    """Выход. Строка не удаляется: «когда вышли» — такой же факт, как «когда
    вошли», и половина пары в журнале бесполезна."""
    cur.execute(
        "UPDATE user_session SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
        (session_id,),
    )


def revoke_user_sessions(cur: psycopg.Cursor, user_id: str) -> int:
    """Погасить все сессии человека — «выйти на всех устройствах»."""
    cur.execute(
        "UPDATE user_session SET revoked_at = now() "
        "WHERE user_id = %s AND revoked_at IS NULL",
        (user_id,),
    )
    return cur.rowcount


def mask_phone(phone: str | None) -> str:
    """Телефон в ответе на запрос кода: «+7 701 ••• •• 18».

    Показать целиком нельзя — тогда форма входа превращается в справочник
    телефонов школы. Не показать вовсе тоже нельзя: человек с двумя номерами
    должен понять, на какой из них смотреть.
    """
    if not phone:
        return "…"
    return f"{phone[:6]} ••• •• {phone[-2:]}" if len(phone) > 8 else "…"
