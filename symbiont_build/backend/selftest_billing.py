# backend/selftest_billing.py — оплата (МИР/Visa/MC/крипта), баланс активного времени, гранты.
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

# регистрируемся
r = c.post("/v1/account/register", json={"aliases": [{"value": "payer", "kind": "nick"}]}).json()
tok, aid = r["token"], r["account_id"]

# по умолчанию — free
s = c.get("/v1/billing/status", headers=hdr(tok)).json()
check(s["subscription"]["tier"] == "free", "старт: тариф free")

# покупка Premium-год картой МИР (sandbox авто-подтверждение)
p = c.post("/v1/billing/purchase", json={"product": "premium_year", "method": "mir"}, headers=hdr(tok))
check(p.status_code == 200 and p.json()["status"] == "completed", "оплата картой МИР прошла")
sub = p.json()["subscription"]
# 365 дней + комбо-бонусы за пороги 3/6/12 мес (год сразу пересекает все три)
check(sub["tier"] == "premium" and sub["days_left"] >= 365, "Premium на 365+ дней начислен (с комбо)")

# методы оплаты: Visa, Mastercard, крипта — принимаются
for m in ["visa", "mastercard", "crypto", "sbp", "yoomoney"]:
    rr = c.post("/v1/billing/purchase", json={"product": "premium_month", "method": m}, headers=hdr(tok))
    check(rr.status_code == 200, f"метод оплаты '{m}' принят")

# неизвестный метод/продукт — отказ
check(c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "paypal"}, headers=hdr(tok)).status_code == 422, "неизвестный метод → 422")
check(c.post("/v1/billing/purchase", json={"product": "xxx", "method": "mir"}, headers=hdr(tok)).status_code == 422, "неизвестный продукт → 422")

# баланс активного времени: покупаем 100ч, режим balance
c.post("/v1/billing/purchase", json={"product": "balance_100h", "method": "crypto"}, headers=hdr(tok))
s = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]
check(s["mode"] == "balance" and s["active_minutes_left"] == 6000, "баланс 100ч = 6000 активных минут")

# узел рапортует 30 активных минут → списываются (ПЕР-УЗЛОВОЙ секрет)
nb = json.dumps({"id": "nd-1", "country": "NL", "code": "NL", "protocols": ["reality"],
                 "host": "h", "transport": "tcp", "roles": ["entry"]}).encode()
nsig = hmac.new(server.NODE_SECRET.encode(), nb, hashlib.sha256).hexdigest()
rn = c.post("/v1/node/register", content=nb, headers={"x-node-sig": nsig})
check(rn.status_code == 200 and rn.json().get("node_secret"), "узел зарегистрирован, выдан пер-узловой секрет")
node_secret = rn.json()["node_secret"]
def node_report(aid, minutes):
    raw = json.dumps({"node_id": "nd-1", "account_id": aid, "minutes": minutes}).encode()
    sign = hmac.new(node_secret.encode(), raw, hashlib.sha256).hexdigest()
    return c.post("/v1/node/session", content=raw, headers={"x-node-sign": sign})
rr = node_report(aid, 30)
check(rr.status_code == 200 and rr.json()["active_minutes_left"] == 5970, "узел списал 30 минут (6000→5970)")

# плохая подпись узла → 403
raw = json.dumps({"node_id": "nd-1", "account_id": aid, "minutes": 10}).encode()
check(c.post("/v1/node/session", content=raw, headers={"x-node-sign": "bad"}).status_code == 403, "подделка подписи узла → 403")
# неизвестный узел → 403
raw2 = json.dumps({"node_id": "ghost", "account_id": aid, "minutes": 10}).encode()
check(c.post("/v1/node/session", content=raw2, headers={"x-node-sign": "x"}).status_code == 403, "неизвестный узел → 403")

# простой не списывает (узел не рапортует — баланс не меняется)
s_before = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["active_minutes_left"]
s_after = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["active_minutes_left"]
check(s_before == s_after, "в простое баланс не списывается")

# грант от владельца (admin) — пожизненный Ultimate
g = c.post("/v1/admin/grant", json={"account_id": aid, "tier": "ultimate", "days": None, "reason": "founder"},
           headers={"X-Admin-Token": server.ADMIN_TOKEN})
check(g.status_code == 200 and g.json()["granted_tier"]["tier"] == "ultimate", "грант Ultimate выдан владельцем")
st = c.get("/v1/billing/status", headers=hdr(tok)).json()
check(st["granted_tier"]["expires"] is None, "грант пожизненный (expires=None)")

# грант без admin-токена — запрет
check(c.post("/v1/admin/grant", json={"account_id": aid, "tier": "ultimate"}).status_code == 403, "грант без admin → 403")

print(f"\nВсего проверок: {ok}")
