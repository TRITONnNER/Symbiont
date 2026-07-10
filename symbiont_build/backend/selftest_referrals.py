# backend/selftest_referrals.py — рефералы, репутация, комбо-бонусы, откат фрод-ветки.
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
def reg(nick, invite=None):
    body = {"aliases": [{"value": nick, "kind": "nick"}]}
    if invite: body["invite"] = invite
    return c.post("/v1/account/register", json=body).json()

# Инвайтер получает свой код
A = reg("inviter")
codeA = A["invite_code"]
check(codeA and codeA.startswith("SYM-"), "у аккаунта есть инвайт-код")

# Друг регистрируется по инвайту → оба +3 дня
B = reg("friendB", invite=codeA)
sB = c.get("/v1/billing/status", headers=hdr(B["token"])).json()["subscription"]
check(sB["days_left"] == 3, "приглашённый получил +3 дня за регистрацию")
sA = c.get("/v1/billing/status", headers=hdr(A["token"])).json()["subscription"]
check(sA["days_left"] == 3, "инвайтер получил +3 дня за регистрацию")

# Реферальная инфа инвайтера: 1 приглашён, уровень new, лимит 5
ri = c.get("/v1/referral", headers=hdr(A["token"])).json()
check(ri["invited"] == 1 and ri["reputation"]["level"] == "new", "инфа: 1 приглашён, уровень Новичок")
check(ri["reputation"]["payout_cap_month"] == 5, "лимит выплат новичка = 5")

# Друг покупает Premium-год → инвайтеру +90, другу +30 бонусом (поверх покупки)
before = c.get("/v1/billing/status", headers=hdr(A["token"])).json()["subscription"]["days_left"]
c.post("/v1/billing/purchase", json={"product": "premium_year", "method": "mir"}, headers=hdr(B["token"]))
afterA = c.get("/v1/billing/status", headers=hdr(A["token"])).json()["subscription"]["days_left"]
check(afterA == before + 90, "инвайтер получил +90 за годовую покупку друга")
sB = c.get("/v1/billing/status", headers=hdr(B["token"])).json()["subscription"]
check(sB["days_left"] >= 365 + 30, "друг получил год + бонус 30 за покупку")

# Друг теперь «платящий» → реферал инвайтера сконвертился
ri = c.get("/v1/referral", headers=hdr(A["token"])).json()
check(ri["converted"] == 1, "1 реферал сконвертился (платящий)")

# Лимит выплат: новичок с лимитом 5 — наприглашаем 7, выплат не больше 5
L = reg("limiter")
codeL = L["invite_code"]
for i in range(7):
    reg(f"ref{i}", invite=codeL)
rl = c.get("/v1/referral", headers=hdr(L["token"])).json()
check(rl["invited"] == 7, "приглашать можно неограниченно (7 приглашённых)")
check(rl["reputation"]["payouts_used"] <= 5, f"но выплат не больше лимита 5 (использовано={rl['reputation']['payouts_used']})")

# Комбо-бонус: покупаем 3 месяца подряд → бонус на 3-м (порог 3 мес = +7 дней)
M = reg("combo")
for _ in range(3):
    c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "visa"}, headers=hdr(M["token"]))
sM = c.get("/v1/billing/status", headers=hdr(M["token"])).json()["subscription"]
check(sM["days_left"] == 90 + 7, "комбо: 3 месяца → +7 бонус-дней (всего 97)")

# Откат фрод-ветки: F пригласил G; помечаем F фродом → F и G обнулены, их премиум снят
F = reg("fraudF")
G = reg("fraudG", invite=F["invite_code"])
c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "crypto"}, headers=hdr(G["token"]))
fr = c.post("/v1/admin/fraud", json={"account_id": F["account_id"]},
            headers={"X-Admin-Token": server.ADMIN_TOKEN}).json()
check(F["account_id"] in fr["flagged"] and G["account_id"] in fr["flagged"], "фрод помечает ветку (F и G)")
sG = c.get("/v1/billing/status", headers=hdr(G["token"])).json()["subscription"]
check(sG["tier"] == "free" and sG["days_left"] == 0, "у фрод-ветки премиум обнулён")

# Инвайтер A (не в фрод-ветке) не пострадал
sA = c.get("/v1/billing/status", headers=hdr(A["token"])).json()["subscription"]
check(sA["tier"] != "free" or sA["days_left"] > 0, "честный инвайтер не задет откатом")

print(f"\nВсего проверок: {ok}")
