# backend/selftest_key_check.py — публичный чекер ключа (/v1/key/check), без активации.
# Проверяет все статусы (valid/already_redeemed/revoked/expired/not_found/invalid),
# отсутствие авторизации и то, что проверка НИЧЕГО не мутирует.
import os, tempfile
os.chdir(tempfile.mkdtemp())          # чистый каталог: свои json, не трогаем рабочие
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
import server
c = TestClient(server.app)
ADMIN = {"x-admin-token": server.ADMIN_TOKEN}

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1

def issue(**kw):
    r = c.post("/v1/admin/issue", headers=ADMIN, json=kw)
    assert r.status_code == 200, r.text
    return r.json()["code"]

def anon():
    return c.post("/v1/account/anon", json={"label": "t"}).json()["token"]

# 1) свежий ключ → valid, без авторизации
code = issue(plan="pro", grant_days=30, uses=1)
r = c.post("/v1/key/check", json={"code": code})
check(r.status_code == 200, "чекер отвечает 200 без авторизации")
d = r.json()
check(d["status"] == "valid" and d["ok"] is True, "свежий ключ → valid/ok")
check(d["grants"]["days"] == 30 and d["grants"]["tier"] == "pro", "чекер показывает грант (дни/тариф)")
check(d["registered"] is True and d["uses_left"] == 1, "registered + uses_left")

# 2) проверка НЕ мутирует состояние
check(c.post("/v1/key/check", json={"code": code}).json()["status"] == "valid",
      "повторная проверка не погасила ключ")

# 3) после активации → already_redeemed
tok = anon()
rr = c.post("/v1/key/redeem", json={"code": code}, headers={"Authorization": f"Bearer {tok}"})
check(rr.status_code == 200, "redeem валидного ключа → 200")
check(c.post("/v1/key/check", json={"code": code}).json()["status"] == "already_redeemed",
      "погашенный ключ → already_redeemed")

# 4) отозванный ключ → revoked
code2 = issue(plan="pro", grant_days=7, uses=1)
server.issuer.revoke(server.issuer._parse_verify(code2).kid)
check(c.post("/v1/key/check", json={"code": code2}).json()["status"] == "revoked",
      "отозванный ключ → revoked")

# 5) подделка/мусор → invalid
d = c.post("/v1/key/check", json={"code": "SYMB-FAKE-0000.NOPE"}).json()
check(d["status"] == "invalid" and d["valid"] is False, "подделка → invalid")

# 6) подписан нами, но не в реестре этой системы → not_found
code3 = issue(plan="pro", grant_days=15, uses=1)
server.issuer._registry.pop(server.issuer._parse_verify(code3).kid, None)
d = c.post("/v1/key/check", json={"code": code3}).json()
check(d["status"] == "not_found" and d["registered"] is False,
      "валидная подпись, нет в реестре → not_found")

# 7) истёкший ключ → expired (ttl в прошлом — напрямую через issuer)
code4 = server.issuer.issue(plan="pro", grant_days=5, uses=1, ttl_days=-1)
check(c.post("/v1/key/check", json={"code": code4}).json()["status"] == "expired",
      "истёкший ключ → expired")

# 8) многоразовый ключ показывает остаток активаций
code5 = issue(plan="pro", grant_days=1, uses=3)
d = c.post("/v1/key/check", json={"code": code5}).json()
check(d["status"] == "valid" and d["uses_total"] == 3 and "осталось активаций" in d["message"],
      "многоразовый ключ: остаток активаций в message")

print(f"\n=== ИТОГ: {ok} OK, 0 FAIL ===")
