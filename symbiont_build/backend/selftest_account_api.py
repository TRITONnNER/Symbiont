# backend/selftest_account_api.py — проверки эндпоинтов аккаунтов (register/login/recover).
import os, tempfile
os.chdir(tempfile.mkdtemp())          # чистый каталог: свои json, не трогаем рабочие
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
import server
c = TestClient(server.app)

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1

# 1. Регистрация с любыми алиасами (телефон '+1' и ник)
r = c.post("/v1/account/register", json={"aliases": [{"value": "+1", "kind": "phone"},
                                                     {"value": "CoolDude", "kind": "nick"}]})
check(r.status_code == 200, "регистрация 200")
d = r.json()
check(d["token"] and d["account_id"] and d["recovery_code"], "вернулись token/account_id/recovery")
check(d["subscription"]["plan"] == "free", "новый аккаунт сразу Free")
rec = d["recovery_code"]; aid = d["account_id"]

# 2. Повтор алиаса (с нормализацией '+ 1') — отказ 409
r = c.post("/v1/account/register", json={"aliases": [{"value": "+ 1", "kind": "phone"}]})
check(r.status_code == 409, "повтор '+ 1' → 409 alias_taken")

# 3. Вход по ОДНОМУ нику без секрета — ОТКАЗ (алиас публичен, не является ключом;
#    единственный ключ к беспарольному аккаунту — recovery-код, см. п.4).
r = c.post("/v1/account/login", json={"alias": {"value": "cooldude", "kind": "nick"}})
check(r.status_code == 401, "вход по одному нику (без секрета) → 401")

# 4. Вход по recovery-коду
r = c.post("/v1/account/recover", json={"recovery_code": rec})
check(r.status_code == 200 and r.json()["account_id"] == aid, "вход по recovery → тот же аккаунт")

# 5. Несуществующий алиас → 401
r = c.post("/v1/account/login", json={"alias": {"value": "ghost", "kind": "nick"}})
check(r.status_code == 401, "несуществующий алиас → 401")

# 6. Аккаунт с паролем
r = c.post("/v1/account/register", json={"aliases": [{"value": "alice", "kind": "nick"}],
                                         "password": "s3cret"})
check(r.status_code == 200, "регистрация с паролем 200")
r = c.post("/v1/account/login", json={"alias": {"value": "alice", "kind": "nick"}})
check(r.status_code == 401, "с паролем без пароля → 401")
r = c.post("/v1/account/login", json={"alias": {"value": "alice", "kind": "nick"}, "password": "s3cret"})
check(r.status_code == 200, "с верным паролем → 200")

# 7. Выданный токен работает в существующей авторизации (совместимость)
tok = d["token"]
r = c.patch("/v1/account/label", json={"label": "новый ник"}, headers={"Authorization": f"Bearer {tok}"})
check(r.status_code == 200, "сессионный токен работает с auth (set_label)")

# 8. PoW-челлендж выдаётся
r = c.get("/v1/account/pow")
check(r.status_code == 200 and "challenge" in r.json(), "PoW-челлендж выдаётся")

# 9. Rate-limit по /24 (выкручиваем лимит вниз и долбим)
server.REG_LIMIT_PER_HOUR = 2
server._reg_by_subnet.clear()
codes = [c.post("/v1/account/register",
                json={"aliases": [{"value": f"u{i}", "kind": "nick"}]}).status_code for i in range(4)]
check(codes.count(429) >= 1, f"rate-limit /24 срабатывает (коды={codes})")

print(f"\nВсего проверок: {ok}")
