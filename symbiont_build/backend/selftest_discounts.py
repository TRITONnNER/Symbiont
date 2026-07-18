# backend/selftest_discounts.py — скидки (кампании/коды) + одноразовые пакеты + login-gate.
import os, tempfile, sys, time
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
def admin(): return {"X-Admin-Token": server.ADMIN_TOKEN}

r = c.post("/v1/account/register", json={"aliases": [{"value": "disc", "kind": "nick"}]}).json()
tok, aid = r["token"], r["account_id"]

# ── LOGIN-GATE: покупка без токена аккаунта — запрет ─────────────────────────────
check(c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "sbp"}).status_code in (400, 401),
      "login-gate: покупка без авторизации отклонена")

# ── ОДНОРАЗОВЫЙ ПАКЕТ 20 ч ───────────────────────────────────────────────────────
p1 = c.post("/v1/billing/purchase", json={"product": "balance_20h", "method": "sbp"}, headers=hdr(tok))
check(p1.status_code == 200 and p1.json()["status"] == "completed", "пробные 20 ч куплены (1-й раз)")
check(p1.json()["amount"] == 39, "цена пробных 20 ч = 39 ₽")
p2 = c.post("/v1/billing/purchase", json={"product": "balance_20h", "method": "sbp"}, headers=hdr(tok))
check(p2.status_code == 409, "повторная покупка 20 ч → 409 (одноразовый пакет)")

# ── АДМИН создаёт скидку-КАМПАНИЮ (авто, −20% на всё) ─────────────────────────────
camp = c.post("/v1/admin/discount", json={"percent": 20, "scope": "all", "label": "Запуск −20%"},
              headers=admin())
check(camp.status_code == 200 and camp.json()["discount"]["id"], "владелец создал кампанию −20% на всё")
# витрина видит активную кампанию
lc = c.get("/v1/config/discounts").json()["campaigns"]
check(any(x["percent"] == 20 for x in lc), "витрина: кампания −20% видна")
# покупка premium_month (199) с авто-кампанией → 159.2
pm = c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "sbp"}, headers=hdr(tok)).json()
check(pm["amount"] == 159.2 and pm["discount"]["percent"] == 20, "авто-кампания −20%: 199 → 159.2 ₽")

# ── СКИДКА НА КОНКРЕТНЫЙ ТАРИФ ────────────────────────────────────────────────────
# отзовём общую кампанию, создадим скидку только на ultimate
did = camp.json()["discount"]["id"]
c.post(f"/v1/admin/discount/{did}/revoke", headers=admin())
c.post("/v1/admin/discount", json={"percent": 50, "scope": {"tiers": ["ultimate"]}, "label": "Ultimate −50"},
       headers=admin())
u = c.post("/v1/billing/purchase", json={"product": "ultimate_month", "method": "sbp"}, headers=hdr(tok)).json()
check(u["amount"] == 174.5, "скидка на тариф: ultimate_month 349 → 174.5 ₽ (−50%)")
pm2 = c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "sbp"}, headers=hdr(tok)).json()
check(pm2.get("discount") is None and pm2["amount"] == 199, "скидка на ultimate НЕ применяется к premium")

# ── КОД-КУПОН (вводится вручную), с лимитом на аккаунт ────────────────────────────
c.post("/v1/admin/discount", json={"percent": 30, "code": "SUMMER30", "scope": "all",
                                    "max_per_account": 1, "label": "SUMMER30"}, headers=admin())
# проверка кода до оплаты
chk = c.post("/v1/billing/discount/check", json={"code": "SUMMER30", "product": "premium_year"},
             headers=hdr(tok)).json()
check(chk["ok"] and chk["final_amount"] == round(1490 * 0.7, 2), "проверка купона: premium_year 1490 → 1043.0")
# несуществующий код при покупке → 422
bad = c.post("/v1/billing/purchase", json={"product": "premium_year", "method": "sbp", "discount_code": "NOPE"},
             headers=hdr(tok))
check(bad.status_code == 422, "неизвестный код → 422")
# покупка с кодом
pc = c.post("/v1/billing/purchase", json={"product": "premium_year", "method": "sbp", "discount_code": "SUMMER30"},
            headers=hdr(tok)).json()
check(pc["amount"] == 1043.0, "покупка с кодом SUMMER30: 1490 → 1043.0 ₽")
# повторно тем же кодом (лимит 1 на аккаунт) → купон больше не применяется → 422
pc2 = c.post("/v1/billing/purchase", json={"product": "premium_year", "method": "sbp", "discount_code": "SUMMER30"},
             headers=hdr(tok))
check(pc2.status_code == 422, "повторное применение купона (лимит 1/аккаунт) → 422")

# ── ОКНО ВРЕМЕНИ: просроченная скидка не применяется ─────────────────────────────
past = c.post("/v1/admin/discount", json={"percent": 90, "code": "EXPIRED", "expires_at": 1},
              headers=admin())
ce = c.post("/v1/billing/discount/check", json={"code": "EXPIRED", "product": "premium_month"},
            headers=hdr(tok)).json()
check(ce["ok"] is False, "просроченная скидка (expires_at в прошлом) не применяется")

print(f"\n[discounts] {ok}/{ok} проверок зелёные")
