# backend/selftest_audit_fixes2.py — регрессия на правки аудита (2-я волна).
# Покрывает то, что аудит нашёл незакрытым:
#   1) грант/ключ ПОВЫШАЕТ тир по рангу (premium → ultimate), без понижения;
#   2) /v1/manifest отдаёт 304 БЕЗ тела (корректный conditional-GET);
#   3) спин колеса под локом: N одновременных спинов за день → ровно один успех.
import os, tempfile
os.chdir(tempfile.mkdtemp())          # чистый каталог: свои json, не трогаем рабочие
import sys, threading
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

def register(nick):
    r = c.post("/v1/account/register", json={"aliases": [{"value": nick, "kind": "nick"}]})
    assert r.status_code == 200, r.text
    return r.json()["token"]

def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}

def redeem(tok, code):
    r = c.post("/v1/key/redeem", json={"code": code}, headers=hdr(tok))
    assert r.status_code == 200, r.text
    return r.json()

def tier(tok):
    return c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["tier"]

# ── 1) Повышение тира по рангу (M5) ──────────────────────────────────────────
tok = register("upgrader")
check(tier(tok) == "free", "новый аккаунт — free")

redeem(tok, issue(plan="premium", grant_days=30, uses=1))
check(tier(tok) == "premium", "premium-ключ → premium")

redeem(tok, issue(plan="ultimate", grant_days=30, uses=1))
check(tier(tok) == "ultimate", "ultimate-ключ поверх premium → ultimate (был баг: оставался premium)")

redeem(tok, issue(plan="premium", grant_days=30, uses=1))
check(tier(tok) == "ultimate", "premium-ключ поверх ultimate НЕ понижает тир")

# ── 2) 304 без тела (L3) ─────────────────────────────────────────────────────
man = c.get("/v1/manifest").json()
r304 = c.get(f"/v1/manifest?since={man['version']}")
check(r304.status_code == 304, "актуальная версия → 304")
check(r304.content == b"", "304 не несёт тела (пустой content)")
# свежая версия по-прежнему приходит целиком
check(c.get("/v1/manifest?since=0").status_code == 200, "since=0 → полный манифест")

# ── 3) Спин колеса под локом (H3): N одновременных спинов → ровно один успех ──
sp = register("racer")
N = 12
codes = []
lock = threading.Lock()
def spin():
    s = c.post("/v1/wheel/spin", headers=hdr(sp))
    with lock:
        codes.append(s.status_code)
threads = [threading.Thread(target=spin) for _ in range(N)]
for t in threads: t.start()
for t in threads: t.join()
wins = sum(1 for s in codes if s == 200)
conflicts = sum(1 for s in codes if s == 409)
check(wins == 1, f"ровно один спин прошёл ({wins}/{N} успешных — без лока было бы >1)")
check(conflicts == N - 1, f"остальные {conflicts} спина → 409 already_spun_today")
# журнал показывает ровно одно начисление за колесо (нет двойного начисления)
led = c.get("/v1/billing/ledger", headers=hdr(sp)).json()["entries"]
wheel_entries = [e for e in led if e.get("kind") == "wheel"]
check(len(wheel_entries) == 1, "в журнале ровно одна запись 'wheel' (нет двойного начисления)")

print(f"\n=== ИТОГ: {ok} OK, 0 FAIL ===")
