# backend/selftest_balance.py — баланс активного времени: authorize + идемпотентное
# кумулятивное списание + enforcement при нуле + приоритет подписки.
import os, tempfile, sys, json, hmac, hashlib
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

# аккаунт
r = c.post("/v1/account/register", json={"aliases": [{"value": "bal", "kind": "nick"}]}).json()
tok, aid = r["token"], r["account_id"]

# регистрируем узел (пер-узловой секрет для подписи)
nb = json.dumps({"id": "nd-b", "country": "NL", "code": "NL", "protocols": ["reality"],
                 "host": "h", "transport": "tcp", "roles": ["entry"]}).encode()
nsig = hmac.new(server.NODE_SECRET.encode(), nb, hashlib.sha256).hexdigest()
node_secret = c.post("/v1/node/register", content=nb, headers={"x-node-sig": nsig}).json()["node_secret"]

def node_call(path, payload):
    raw = json.dumps(payload).encode()
    sign = hmac.new(node_secret.encode(), raw, hashlib.sha256).hexdigest()
    return c.post(path, content=raw, headers={"x-node-sign": sign})

def authorize():
    return node_call("/v1/node/authorize", {"node_id": "nd-b", "account_id": aid}).json()

def session(stoken, cum):
    return node_call("/v1/node/session", {"node_id": "nd-b", "session_token": stoken, "cum_minutes": cum}).json()

# 1) БЕЗ времени и подписки → authorize отказывает (enforcement при нуле)
a0 = authorize()
check(a0["allowed"] is False and a0["coverage"] == "none", "нет времени/подписки → authorize deny")

# 2) покупаем 100 ч (6000 мин) → authorize разрешает, лиз ≤ 30
c.post("/v1/billing/purchase", json={"product": "balance_100h", "method": "crypto"}, headers=hdr(tok))
a1 = authorize()
check(a1["allowed"] and a1["coverage"] == "balance", "с балансом → authorize allow, coverage=balance")
check(a1["session_token"] and a1["lease_minutes"] == 30, "выдан session_token + лиз 30")
stok = a1["session_token"]
# эмулируем, что сессия идёт уже час → снимаем sanity-cap по wall-clock (в тесте нет sleep)
server.node_sessions[stok]["created_at"] -= 3600

# 3) кумулятивное списание: cum=10 → остаток 5990
s1 = session(stok, 10)
check(s1["active_minutes_left"] == 5990, "cum=10 → списано 10 (6000→5990)")

# 4) ИДЕМПОТЕНТНОСТЬ: повтор того же cum=10 → ничего не списывается второй раз
s2 = session(stok, 10)
check(s2["active_minutes_left"] == 5990, "повтор cum=10 → без двойного списания (идемпотентно)")

# 5) прирост: cum=25 → списываются только +15 (остаток 5975)
s3 = session(stok, 25)
check(s3["active_minutes_left"] == 5975, "cum=25 → доначислен только прирост 15 (5990→5975)")

# 6) чужой/битый session_token → 403
bad = node_call("/v1/node/session", {"node_id": "nd-b", "session_token": "GHOST", "cum_minutes": 5})
check(bad.status_code == 403, "неизвестный session_token → 403")

# 7) приоритет подписки: купим Premium-месяц (дни>0) → часы НЕ тратятся
c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "sbp"}, headers=hdr(tok))
a2 = authorize()
check(a2["coverage"] == "period", "с подпиской coverage=period (приоритет над балансом)")
stok2 = a2["session_token"]
left_before = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["active_minutes_left"]
session(stok2, 20)                                   # сессия 20 минут под активной подпиской
left_after = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["active_minutes_left"]
check(left_before == left_after, "под активной подпиской баланс часов НЕ списывается")

print(f"\n[balance] {ok}/{ok} проверок зелёные")
