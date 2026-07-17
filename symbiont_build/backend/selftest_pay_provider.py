# backend/selftest_pay_provider.py — платёжная ЗАГЛУШКА по реальному принципу:
# purchase → pending + checkout_url + qr; confirm (колбэк) → completed; идемпотентно.
import os, tempfile, sys
os.chdir(tempfile.mkdtemp())
os.environ["SYMBIONT_PAY_PROVIDER"] = "mock"          # включаем mock-провайдера ДО импорта server
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

check(server.PAY_PROVIDER == "mock", "провайдер = mock")
r = c.post("/v1/account/register", json={"aliases": [{"value": "buyer", "kind": "nick"}]}).json()
tok, aid = r["token"], r["account_id"]

# 1) «Купить» 100 ч → pending + ссылка/QR + сумма (реальный принцип, не мгновенно)
p = c.post("/v1/billing/purchase", json={"product": "balance_100h", "method": "sbp", "region": "ru"},
           headers=hdr(tok)).json()
check(p["status"] == "pending" and p["provider"] == "mock", "purchase → pending у mock-провайдера")
check(p["checkout_url"].endswith(p["payment_id"]) and p["qr"].startswith("SYMB-PAY:"), "вернулись checkout_url и qr")
check(p["amount"] == 149 and p["currency"] == "RUB", "сумма из экономики: 100 ч = 149 ₽")
pid = p["payment_id"]

# 2) до оплаты — статус pending, баланс не начислен
check(c.get(f"/v1/billing/payment/{pid}", headers=hdr(tok)).json()["status"] == "pending", "статус платежа pending")
check(c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["active_minutes_left"] == 0, "до оплаты баланс 0")

# 3) страница провайдера (mock checkout) отдаётся
page = c.get(f"/v1/billing/mock/checkout/{pid}")
check(page.status_code == 200 and "Оплатить" in page.text, "mock-checkout: страница с кнопкой «Оплатить»")

# 4) колбэк «оплатил» → completed + начислено 6000 минут (реально «купилось»)
cf = c.post(f"/v1/billing/mock/confirm/{pid}").json()
check(cf["status"] == "completed", "confirm → completed")
check(cf["subscription"]["active_minutes_left"] == 6000, "после оплаты начислено 100 ч = 6000 минут")
check(c.get(f"/v1/billing/payment/{pid}", headers=hdr(tok)).json()["status"] == "completed", "статус платежа completed")

# 5) ИДЕМПОТЕНТНОСТЬ: повторный колбэк не начисляет второй раз
cf2 = c.post(f"/v1/billing/mock/confirm/{pid}").json()
check(cf2.get("idempotent") is True, "повторный confirm → idempotent")
check(c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["active_minutes_left"] == 6000,
      "баланс не удвоился при повторном колбэке")

# 6) крипто-скидка −10 % отражается в сумме (149 → 134.1)
pc = c.post("/v1/billing/purchase", json={"product": "balance_100h", "method": "crypto", "region": "ru"},
            headers=hdr(tok)).json()
check(pc["amount"] == 134.1, "крипта: 100 ч со скидкой −10 % = 134.1 ₽")

# 7) международный регион → доллары (300 ч = $4.99)
pu = c.post("/v1/billing/purchase", json={"product": "balance_300h", "method": "visa", "region": "intl"},
            headers=hdr(tok)).json()
check(pu["amount"] == 4.99 and pu["currency"] == "USD", "intl: 300 ч = $4.99")

print(f"\n[pay-provider] {ok}/{ok} проверок зелёные")
