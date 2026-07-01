#!/usr/bin/env python3
"""
smoke_test.py — сквозной тест бэкенда «Симбионта» без поднятия сервера (TestClient).
Запуск:  cd backend && python smoke_test.py
Проверяет: аккаунт без данных, ошибки погашения, выпуск+погашение ключа,
идемпотентность, конфликт чужого аккаунта, подпись манифеста, 304, диалог поддержки.
Код возврата 0 = всё ок, иначе 1.
"""
import sys, base64
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import server
from manifest import verify_manifest

PASS, FAIL = "  [OK] ", "  [FAIL] "
errors = 0

def check(name, cond):
    global errors
    print((PASS if cond else FAIL) + name)
    if not cond:
        errors += 1

c = TestClient(server.app)

# 1) анонимный аккаунт
r = c.post("/v1/account/anon", json={"label": "гость-7K2Q"})
check("анонимный аккаунт создан (200)", r.status_code == 200)
tok = r.json().get("token", "")
H = {"Authorization": f"Bearer {tok}"}
check("выдан непрозрачный токен", len(tok) >= 24)
check("план по умолчанию free", r.json()["subscription"]["plan"] == "free")

# 2) неверный ключ → 422
r = c.post("/v1/key/redeem", json={"code": "NOPE.NOPE"}, headers=H)
check("неверный ключ отвергнут (422)", r.status_code == 422)

# 3) запрос без токена → 401
r = c.post("/v1/key/redeem", json={"code": "x.y"})
check("без токена запрещено (401)", r.status_code == 401)

# 4) админ выпускает ключ
r = c.post("/v1/admin/issue", json={"plan": "pro", "grant_days": 30, "uses": 1},
           headers={"x-admin-token": server.ADMIN_TOKEN})
check("ключ выпущен админом (200)", r.status_code == 200)
code = r.json().get("code", "")

# 5) выпуск без админ-токена → 403
r = c.post("/v1/admin/issue", json={"plan": "pro"}, headers={"x-admin-token": "wrong"})
check("выпуск без админ-токена запрещён (403)", r.status_code == 403)

# 6) погашение ключа
r = c.post("/v1/key/redeem", json={"code": code}, headers=H)
check("ключ погашен (200, план pro)", r.status_code == 200 and r.json()["plan"] == "pro")

# 7) повтор тем же аккаунтом — идемпотентно
r = c.post("/v1/key/redeem", json={"code": code}, headers=H)
check("повтор тем же аккаунтом идемпотентен", r.status_code == 200 and r.json().get("idempotent") is True)

# 8) чужой аккаунт тем же ключом → 409
r2 = c.post("/v1/account/anon", json={})
H2 = {"Authorization": f"Bearer {r2.json()['token']}"}
r = c.post("/v1/key/redeem", json={"code": code}, headers=H2)
check("чужой аккаунт получает отказ (409)", r.status_code == 409)

# 9) манифест и проверка подписи
r = c.get("/v1/manifest")
check("манифест отдан (200)", r.status_code == 200)
man = r.json()
pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(c.get("/v1/pubkey").json()["ed25519"]))
check("подпись манифеста валидна", verify_manifest(man, pk))
check("304 при актуальной версии", c.get(f"/v1/manifest?since={man['version']}").status_code == 304)

# 10) поддержка: диалог по токену
c.post("/v1/support/message",
       json={"text": "YouTube не открывается", "diag": {"consent": True, "report": "reachable_but_tls_reset"}},
       headers=H)
msgs = c.get("/v1/support/thread", headers=H).json()["messages"]
check("диалог поддержки ведётся (>=3 сообщений)", len(msgs) >= 3)

print()
if errors:
    print(f"РЕЗУЛЬТАТ: провалено проверок — {errors}")
    sys.exit(1)
print("РЕЗУЛЬТАТ: все проверки пройдены ✔")
sys.exit(0)
