# backend/selftest_identity.py — проверки крипто-личности и алиасов (Этап 1).
import os, tempfile
from identity import (new_secret, account_id, recovery_code, secret_from_recovery,
                      normalize_alias, alias_hmac, hash_password, verify_password,
                      pow_ok, solve_pow, AccountStore)

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1

# 1. Крипто-личность: id детерминирован, recovery восстанавливает секрет
s = new_secret()
check(len(s) == 32, "секрет 256 бит")
check(account_id(s) == account_id(s), "account_id детерминирован")
check(account_id(new_secret()) != account_id(s), "разные секреты → разные id")
rc = recovery_code(s)
check(secret_from_recovery(rc) == s, "recovery-код восстанавливает секрет")
check(account_id(secret_from_recovery(rc)) == account_id(s), "recovery → тот же account_id")

# 2. Нормализация алиасов: '+1' и '+ 1' совпадают; '+1' и '1' различаются
check(normalize_alias("+1", "phone") == normalize_alias("+ 1", "phone"), "'+1' == '+ 1'")
check(normalize_alias("+9966", "phone") == "+9966", "'+9966' сохранён")
check(normalize_alias("+1", "phone") != normalize_alias("1", "phone"), "'+1' != '1' (плюс значим)")
check(alias_hmac("Bob", "nick") == alias_hmac("bob", "nick"), "ник регистронезависим")
check(alias_hmac("a@b.c", "email") != alias_hmac("a@b.d", "email"), "разные почты → разные хеши")

# 3. HMAC скрывает значение (в хеше нет исходной строки)
h = alias_hmac("secret@mail.com", "email")
check("secret@mail.com" not in h and len(h) == 64, "алиас хранится как HMAC, не в открытую")

# 4. Пароль: верный проходит, неверный — нет
ph = hash_password("hunter2")
check(verify_password("hunter2", ph), "верный пароль проходит")
check(not verify_password("wrong", ph), "неверный пароль отклонён")

# 5. PoW: решение проходит проверку, мусор — нет
chal = "challenge-123"
sol = solve_pow(chal, 12)
check(pow_ok(chal, sol, 12), "решённый PoW проходит")
check(not pow_ok(chal, "0", 24), "нерешённый PoW (24 бита) отклонён")

# 6. Хранилище: регистрация, дубль-алиас, вход алиасом и recovery
with tempfile.TemporaryDirectory() as d:
    st = AccountStore(os.path.join(d, "acc.json"))
    r = st.register([("+1", "phone"), ("cooldude", "nick")])
    aid = r["account_id"]
    check(aid and r["recovery_code"], "регистрация вернула id и recovery")
    try:
        st.register([("+ 1", "phone")]); dup = False
    except ValueError as e:
        dup = (str(e) == "alias_taken")
    check(dup, "повтор '+ 1' отклонён как занятый (нормализация работает)")
    check(st.login_alias("+1", "phone") == aid, "вход по телефону-алиасу")
    check(st.login_alias("COOLDUDE", "nick") == aid, "вход по нику (регистронезависимо)")
    check(st.login_recovery(r["recovery_code"]) == aid, "вход по recovery-коду")
    check(st.login_alias("nope", "nick") is None, "несуществующий алиас → None")
    check(st.accounts[aid]["tier"] == "free", "новый аккаунт сразу Free (нет 'неактивированного')")

    # 7. Пароль на аккаунте
    r2 = st.register([("alice", "nick")], password="s3cret")
    aid2 = r2["account_id"]
    check(st.login_alias("alice", "nick") is None, "с паролем без пароля — отказ")
    check(st.login_alias("alice", "nick", "s3cret") == aid2, "с верным паролем — вход")
    check(st.login_alias("alice", "nick", "bad") is None, "с неверным паролем — отказ")

    # 8. Персистентность: перезагрузка хранилища
    st2 = AccountStore(os.path.join(d, "acc.json"))
    check(st2.login_alias("+1", "phone") == aid, "после перезагрузки вход работает")
    check(len(st2.accounts) == 2, "оба аккаунта сохранились")

    # 9. Несколько алиасов на один аккаунт
    st2.add_alias(aid, "+9966", "phone")
    check(st2.login_alias("+9966", "phone") == aid, "доп. алиас ведёт на тот же аккаунт")

print(f"\nВсего проверок: {ok}")
