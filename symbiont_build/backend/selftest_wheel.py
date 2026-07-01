# backend/selftest_wheel.py — колесо фортуны: ежедневный бонус, детерминизм, один спин в день.
import os, tempfile, sys
os.chdir(tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
import server
c = TestClient(server.app)

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1
def hdr(t): return {"Authorization": f"Bearer {t}"}

r = c.post("/v1/account/register", json={"aliases": [{"value": "spinner", "kind": "nick"}]}).json()
tok, aid = r["token"], r["account_id"]

# инфо: колесо доступно, сегменты есть
w = c.get("/v1/wheel", headers=hdr(tok)).json()
check(w["available"] is True and len(w["segments"]) >= 4, "колесо доступно, сегменты заданы")

# баланс дней до спина
d0 = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["days_left"]

# спин: начисляет приз
s = c.post("/v1/wheel/spin", headers=hdr(tok))
check(s.status_code == 200, "спин прошёл (200)")
sj = s.json()
seg = sj["prize"]
check(seg["kind"] == "days" and seg["amount"] >= 1, "приз — дни")
d1 = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["days_left"]
check(d1 == d0 + seg["amount"], f"приз начислен (дни {d0}→{d1})")

# детерминизм: индекс совпадает с пересчётом hash(аккаунт:дата:сид)
expect = server._wheel_index(aid, server._today())
check(sj["index"] == expect, "результат детерминирован (provably fair)")

# повторный спин в тот же день — 409
s2 = c.post("/v1/wheel/spin", headers=hdr(tok))
check(s2.status_code == 409, "повторный спин в тот же день → 409")

# инфо после спина: недоступно, показывает сегодняшний результат
w2 = c.get("/v1/wheel", headers=hdr(tok)).json()
check(w2["available"] is False and w2["today_index"] == sj["index"], "после спина: недоступно, виден результат дня")

# другой аккаунт — свой независимый результат
r2 = c.post("/v1/account/register", json={"aliases": [{"value": "spinner2", "kind": "nick"}]}).json()
e2 = server._wheel_index(r2["account_id"], server._today())
check(0 <= e2 < len(w["segments"]), "у другого аккаунта свой валидный индекс")

print(f"\nВсего проверок: {ok}")
