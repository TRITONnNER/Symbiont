# backend/selftest_payments.py — жизненный цикл платежа и журнал начислений.
# pending → вебхук провайдера → completed; статус платежа; идемпотентность вебхука;
# приватность (чужой платёж не виден); журнал (/v1/billing/ledger).
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
def pay_sig(body: bytes):
    return hmac.new(server.PAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()

r = c.post("/v1/account/register", json={"aliases": [{"value": "buyer", "kind": "nick"}]}).json()
tok, aid = r["token"], r["account_id"]

# ── SANDBOX: покупка сразу completed и попадает в журнал ──
server.SANDBOX_PAY = True
p = c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "mir"}, headers=hdr(tok)).json()
check(p["status"] == "completed" and p.get("payment_id"), "sandbox: покупка → completed + payment_id")
ps = c.get(f"/v1/billing/payment/{p['payment_id']}", headers=hdr(tok)).json()
check(ps["status"] == "completed" and "account_id" not in ps, "статус платежа виден владельцу (без account_id)")
led = c.get("/v1/billing/ledger", headers=hdr(tok)).json()["entries"]
check(any(e["kind"] == "purchase" and e.get("product") == "premium_month" for e in led),
      "покупка записана в журнал")

# ── PROD: pending → вебхук подтверждает → completed ──
server.SANDBOX_PAY = False
pend = c.post("/v1/billing/purchase", json={"product": "ultimate_month", "method": "sbp"}, headers=hdr(tok)).json()
check(pend["status"] == "pending" and pend.get("payment_id"), "prod: покупка → pending")
pid = pend["payment_id"]
st = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]
check(st["tier"] != "ultimate", "до вебхука тариф не поднят (pending не начисляет)")

body = json.dumps({"payment_id": pid, "status": "completed"}).encode()
check(c.post("/v1/billing/webhook", content=body, headers={"x-pay-sig": "bad"}).status_code == 401,
      "вебхук с плохой подписью → 401")
w = c.post("/v1/billing/webhook", content=body, headers={"x-pay-sig": pay_sig(body)}).json()
check(w["ok"] and w["status"] == "completed", "верный вебхук → completed")
st = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]
check(st["tier"] == "ultimate", "после вебхука тариф ultimate начислен")
check(c.get(f"/v1/billing/payment/{pid}", headers=hdr(tok)).json()["status"] == "completed",
      "статус платежа стал completed")

# ── идемпотентность: повторная доставка вебхука не начисляет дважды ──
days_before = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["days_left"]
w2 = c.post("/v1/billing/webhook", content=body, headers={"x-pay-sig": pay_sig(body)}).json()
check(w2.get("idempotent") is True, "повтор вебхука помечен idempotent")
days_after = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["days_left"]
check(days_before == days_after, "повторный вебхук НЕ начислил повторно")

# ── приватность и ошибки ──
r2 = c.post("/v1/account/register", json={"aliases": [{"value": "stranger", "kind": "nick"}]}).json()
check(c.get(f"/v1/billing/payment/{pid}", headers=hdr(r2["token"])).status_code == 404, "чужой платёж → 404")
b2 = json.dumps({"payment_id": "NOPE", "status": "completed"}).encode()
check(c.post("/v1/billing/webhook", content=b2, headers={"x-pay-sig": pay_sig(b2)}).status_code == 404,
      "вебхук на несуществующий платёж → 404")

# ── failed-вебхук помечает платёж failed, без начисления ──
f = c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "visa"}, headers=hdr(tok)).json()
fb = json.dumps({"payment_id": f["payment_id"], "status": "failed"}).encode()
check(c.post("/v1/billing/webhook", content=fb, headers={"x-pay-sig": pay_sig(fb)}).json()["status"] == "failed",
      "failed-вебхук → status failed")

# ── журнал: спин колеса тоже попадает ──
server.SANDBOX_PAY = True
c.post("/v1/wheel/spin", headers=hdr(tok))
led = c.get("/v1/billing/ledger", headers=hdr(tok)).json()["entries"]
check(any(e["kind"] == "wheel" for e in led), "спин колеса записан в журнал")
check(len(led) >= 2, "в журнале несколько записей (покупки + колесо)")

print(f"\n=== ИТОГ: {ok} OK, 0 FAIL ===")
