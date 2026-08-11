"""Вход: телефон и одноразовый код, пароль сотрудника, сессии, перебор.

Проверяется настоящий HTTP, а не внутренние функции: смысл задачи в том,
что снаружи больше нельзя назваться кем угодно, и убедиться в этом можно
только снаружи.
"""
from __future__ import annotations

import pytest
from conftest import HEADERS, sent_code

from app import auth
from scripts import seed_demo

SLUG = seed_demo.TENANT_SLUG
OTHER_SLUG = seed_demo.TENANT_OTHER_SLUG

OWNER_PHONE = "+77015550110"        # Ерлан Тасмагамбетов, владелец, с паролем
GUARDIAN_PHONE = "+77015552418"     # Гульнара Сагындык, родитель, пароля нет
TEACHER_PHONE = "+77015550001"      # Дмитрий Шарапов
PASSWORD = seed_demo.DEMO_PASSWORD


def ask_code(client, login, tenant=SLUG):
    return client.post(
        "/api/v1/auth/request-code", json={"tenant": tenant, "login": login}
    )


def do_login(client, login, tenant=SLUG, **secret):
    return client.post(
        "/api/v1/auth/login", json={"tenant": tenant, "login": login, **secret}
    )


def login_by_code(client, sql, login, tenant=SLUG):
    assert ask_code(client, login, tenant).status_code == 202
    code = sent_code(sql, login)
    assert code is not None, "код не встал в очередь уведомлений"
    return do_login(client, login, tenant, code=code)


# ---------------------------------------------------------------------------
# Вход по коду
# ---------------------------------------------------------------------------


def test_guardian_logs_in_by_phone_and_code(client, sql):
    """Основной сценарий всей задачи: родитель входит по телефону."""
    response = login_by_code(client, sql, GUARDIAN_PHONE)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["user"]["role"] == "guardian"
    assert body["user"]["name"] == "Гульнара Сагындык"
    assert body["user"]["tenant"]["id"] == seed_demo.TENANT
    # Родителю сразу видно, чьи это дети: двое Сагындык и никого больше.
    assert len(body["user"]["student_ids"]) == 2
    assert body["token"].startswith("rck_sess_")


def test_phone_is_recognised_in_any_format(client, sql):
    """«8 701 555 24 18» и «+7 (701) 555-24-18» — один человек.

    Правило нормализации то же, что у поиска и вебхука (app/phone.py):
    родитель диктует номер так, как привык, и вход обязан это знать.
    """
    assert ask_code(client, "8 (701) 555-24-18").status_code == 202
    code = sent_code(sql, GUARDIAN_PHONE)
    assert do_login(client, "+7 701 555 24 18", code=code).status_code == 200


def test_session_works_as_cookie_without_bearer(client, sql):
    """Браузеру токен в руки не нужен: кука HttpOnly приезжает сама."""
    assert login_by_code(client, sql, GUARDIAN_PHONE).status_code == 200

    cookie = client.cookies.get(auth.COOKIE_NAME)
    assert cookie, "кука сессии не выставлена"

    # Заголовка Authorization здесь нет — работает именно кука.
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "guardian"


def test_cookie_is_httponly_and_samesite(client, sql):
    """XSS не должен уметь прочитать сессию, а чужой сайт — приложить её к POST.

    HttpOnly закрывает первое, SameSite=Lax — второе: браузер не приложит
    куку к межсайтовому POST, а все операции, что-то меняющие, у нас POST,
    PATCH и DELETE. Отдельного CSRF-токена поэтому нет.
    """
    raw = login_by_code(client, sql, GUARDIAN_PHONE).headers["set-cookie"].lower()
    assert "httponly" in raw
    assert "samesite=lax" in raw
    assert "path=/" in raw


def test_code_is_stored_hashed(client, sql):
    """В базе лежит scrypt, а не шесть цифр: дамп не должен пускать в кабинет."""
    ask_code(client, GUARDIAN_PHONE)
    code = sent_code(sql, GUARDIAN_PHONE)
    row = sql.execute(
        "SELECT code_hash FROM auth_code ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert row["code_hash"].startswith("scrypt$")
    assert code not in row["code_hash"]
    assert auth.verify_secret(code, row["code_hash"])


def test_session_token_is_stored_hashed(client, sql):
    """То же и с токеном: в user_session хеш, восстановить токен нечем."""
    token = login_by_code(client, sql, GUARDIAN_PHONE).json()["token"]
    row = sql.execute(
        "SELECT token_hash FROM user_session WHERE token_hash = %s",
        (auth.hash_token(token),),
    ).fetchone()
    assert row is not None
    assert sql.execute(
        "SELECT count(*) AS n FROM user_session WHERE token_hash = %s", (token,)
    ).fetchone()["n"] == 0


def test_code_is_one_time(client, sql):
    """Второй вход тем же кодом не проходит, хотя код ещё не истёк."""
    assert ask_code(client, GUARDIAN_PHONE).status_code == 202
    code = sent_code(sql, GUARDIAN_PHONE)
    assert do_login(client, GUARDIAN_PHONE, code=code).status_code == 200
    assert do_login(client, GUARDIAN_PHONE, code=code).status_code == 401


def test_expired_code_is_refused(client, sql):
    """Протухший код не подходит — даже если он единственный и верный.

    Строка не портится, а СТАРЕЕТ: обе отметки времени сдвигаются на десять
    минут назад разом, и `expires_at > created_at` из схемы остаётся верным.
    Двигать один `expires_at` значило бы проверять умение вставить
    невозможную строку — кода, выданного позже собственного протухания,
    в жизни не бывает, и отказ по такому коду ничего не доказывает.
    """
    ask_code(client, GUARDIAN_PHONE)
    code = sent_code(sql, GUARDIAN_PHONE)
    aged = sql.execute(
        """
        UPDATE auth_code
           SET created_at = created_at - interval '10 minutes',
               expires_at = expires_at - interval '10 minutes'
         RETURNING expires_at < now() AS expired
        """
    ).fetchone()
    sql.commit()
    assert aged["expired"] is True, "код не состарился — тест проверял бы не то"

    response = do_login(client, GUARDIAN_PHONE, code=code)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bad_credentials"


def test_new_code_kills_the_previous_one(client, sql):
    """Два живых кода — вдвое больше подходящих вариантов, чем задумано."""
    ask_code(client, GUARDIAN_PHONE)
    first = sent_code(sql, GUARDIAN_PHONE)
    ask_code(client, GUARDIAN_PHONE)
    second = sent_code(sql, GUARDIAN_PHONE)
    assert first != second

    assert do_login(client, GUARDIAN_PHONE, code=first).status_code == 401
    assert do_login(client, GUARDIAN_PHONE, code=second).status_code == 200


# ---------------------------------------------------------------------------
# Перебор
# ---------------------------------------------------------------------------


def test_brute_force_burns_the_code(client, sql):
    """Пять промахов — и правильный код больше не подойдёт.

    Шесть цифр это миллион вариантов: без этого счётчика перебор занял бы
    минуты, а не годы, и «одноразовый код» защищал бы ровно ни от чего.
    """
    ask_code(client, GUARDIAN_PHONE)
    code = sent_code(sql, GUARDIAN_PHONE)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(auth.CODE_MAX_ATTEMPTS):
        assert do_login(client, GUARDIAN_PHONE, code=wrong).status_code == 401

    assert do_login(client, GUARDIAN_PHONE, code=code).status_code == 401
    assert sql.execute(
        "SELECT attempts FROM auth_code ORDER BY created_at DESC LIMIT 1"
    ).fetchone()["attempts"] == auth.CODE_MAX_ATTEMPTS


def test_failed_attempts_survive_the_rollback(client, sql):
    """Счётчик попыток обязан пережить отказ.

    Если бы 401 откатывал транзакцию целиком, вместе с ней откатывался бы
    и счётчик — то есть перебор снова стал бы бесплатным. Это ровно та
    ошибка, ради которой отказ возвращается значением, а не исключением.
    """
    ask_code(client, GUARDIAN_PHONE)
    do_login(client, GUARDIAN_PHONE, code="000001")
    do_login(client, GUARDIAN_PHONE, code="000002")

    assert sql.execute(
        "SELECT count(*) AS n FROM auth_attempt WHERE kind = 'code_verify' AND NOT ok"
    ).fetchone()["n"] == 2


def test_too_many_code_requests_gets_429(client):
    """Запросить код бесконечно нельзя: иначе SMS школы оплачивает посторонний."""
    for _ in range(auth.CODE_REQUESTS_PER_LOGIN):
        assert ask_code(client, GUARDIAN_PHONE).status_code == 202

    response = ask_code(client, GUARDIAN_PHONE)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_attempts"


def test_repeated_wrong_codes_lock_the_login(client, sql):
    """Новый код после каждых пяти промахов перебор не продлевает."""
    for _ in range(auth.CODE_FAILURES_PER_LOGIN):
        do_login(client, GUARDIAN_PHONE, code="000000")

    assert do_login(client, GUARDIAN_PHONE, code="000000").status_code == 429


def test_wrong_passwords_lock_the_login(client):
    """Пароль выбирает человек — его перебирают по словарю, а не наугад."""
    for _ in range(auth.PASSWORD_FAILURES_PER_LOGIN):
        assert do_login(client, OWNER_PHONE, password="qwerty").status_code == 401

    late = do_login(client, OWNER_PHONE, password=PASSWORD)
    assert late.status_code == 429


# ---------------------------------------------------------------------------
# Пароль сотрудника
# ---------------------------------------------------------------------------


def test_owner_logs_in_by_password(client):
    """Администратор входит по двадцать раз в день: SMS на каждый вход —
    это не безопасность, а счёт от оператора."""
    response = do_login(client, OWNER_PHONE, password=PASSWORD)
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "owner"


def test_password_login_fails_for_account_without_password(client):
    """У родителя пароля нет (password_hash NULL) — и это не пятисотка."""
    response = do_login(client, GUARDIAN_PHONE, password=PASSWORD)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bad_credentials"


def test_code_or_password_but_not_both(client):
    assert do_login(client, OWNER_PHONE).status_code == 400
    both = do_login(client, OWNER_PHONE, password=PASSWORD, code="123456")
    assert both.status_code == 400
    assert both.json()["error"]["code"] == "bad_credentials_form"


# ---------------------------------------------------------------------------
# Чужая школа и несуществующий человек
# ---------------------------------------------------------------------------


def test_unknown_login_answers_exactly_like_a_known_one(client, sql):
    """Форма входа не отвечает на вопрос «ходит ли этот ребёнок в эту школу».

    Ответ совпадает вплоть до маски адреса — она собирается из того, что
    прислали, а не из того, что нашлось в базе.
    """
    known = ask_code(client, GUARDIAN_PHONE).json()
    unknown = ask_code(client, "+77009998877").json()
    assert known == {**unknown, "to": known["to"]}
    assert known["to"] == "+77015 ••• •• 18"
    assert unknown["to"] == "+77009 ••• •• 77"

    # И кода несуществующему человеку, разумеется, никто не слал.
    assert sent_code(sql, "+77009998877") is None


def test_login_of_another_school_does_not_work_here(client, sql):
    """Тот же телефон в чужой школе — это чужой человек.

    Слаг школы обязателен именно поэтому: телефон уникален внутри школы,
    но не между ними.
    """
    assert ask_code(client, GUARDIAN_PHONE, tenant=OTHER_SLUG).status_code == 202
    assert sent_code(sql, GUARDIAN_PHONE) is None
    assert do_login(client, GUARDIAN_PHONE, tenant=OTHER_SLUG, code="123456").status_code == 401


def test_unknown_school_is_404(client):
    response = ask_code(client, GUARDIAN_PHONE, tenant="no-such-school")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_tenant"


# ---------------------------------------------------------------------------
# Выход
# ---------------------------------------------------------------------------


def test_logout_kills_the_session(client, sql):
    token = login_by_code(client, sql, GUARDIAN_PHONE).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    after = client.get("/api/v1/auth/me", headers=headers)
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "bad_session"

    # Строка остаётся: «когда вышли» — такой же факт, как «когда вошли».
    row = sql.execute(
        "SELECT revoked_at FROM user_session WHERE token_hash = %s",
        (auth.hash_token(token),),
    ).fetchone()
    assert row["revoked_at"] is not None


def test_logout_everywhere_kills_the_other_devices(client, sql):
    """Телефон потерян — гасим все сеансы, а не тот, из которого нажали."""
    phone_session = login_by_code(client, sql, GUARDIAN_PHONE).json()["token"]
    laptop_session = login_by_code(client, sql, GUARDIAN_PHONE).json()["token"]
    assert phone_session != laptop_session

    client.post(
        "/api/v1/auth/logout",
        params={"everywhere": True},
        headers={"Authorization": f"Bearer {laptop_session}"},
    )
    for token in (phone_session, laptop_session):
        assert client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401


def test_disabled_account_cannot_use_a_live_session(client, sql):
    """Учётную запись выключили — сессия ещё жива, но входа больше нет."""
    token = login_by_code(client, sql, GUARDIAN_PHONE).json()["token"]
    sql.execute(
        "UPDATE app_user SET is_active = false WHERE id = %s", (seed_demo.GUARDIAN_USER,)
    )
    sql.commit()

    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "user_disabled"


def test_login_is_written_to_the_audit(client, sql):
    login_by_code(client, sql, GUARDIAN_PHONE)
    row = sql.execute(
        "SELECT actor_id, payload FROM audit_log WHERE action = 'auth.login' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert str(row["actor_id"]) == seed_demo.GUARDIAN_USER
    assert row["payload"]["method"] == "code"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/branches", "/api/v1/students", "/api/v1/reports/debts", "/api/v1/auth/me"],
)
def test_nothing_is_readable_without_a_session(client, path):
    assert client.get(path).status_code == 401


def test_health_needs_no_session(client):
    """Проверка живости — для балансировщика, а не для человека."""
    assert client.get("/api/v1/health").status_code == 200


def test_demo_session_still_works(client):
    """Сессии демо-данных — обычные строки user_session, без поблажек в коде."""
    assert client.get("/api/v1/auth/me", headers=HEADERS).json()["role"] == "owner"
