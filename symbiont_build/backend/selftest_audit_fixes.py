# backend/selftest_audit_fixes.py — регрессы на баги, найденные аудитом бэкенда.
# Каждый тест воспроизводит конкретный сценарий и проверяет, что он больше не бьёт.
import os, tempfile, sys, json, hmac, hashlib
os.chdir(tempfile.mkdtemp())
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
def hdr(t): return {"Authorization": f"Bearer {t}"}
def reg(nick, invite=None):
    body = {"aliases": [{"value": nick, "kind": "nick"}]}
    if invite: body["invite"] = invite
    return c.post("/v1/account/register", json=body).json()

# узел с пер-узловым секретом (для node/session)
NODE = {"id": "nd-a", "country": "Netherlands", "code": "NL", "protocols": ["reality"],
        "host": "1.2.3.4", "transport": {"reality": {}}, "roles": ["edge"]}
_raw = json.dumps(NODE).encode()
_nsig = hmac.new(server.NODE_SECRET.encode(), _raw, hashlib.sha256).hexdigest()
node_secret = c.post("/v1/node/register", content=_raw, headers={"x-node-sig": _nsig}).json()["node_secret"]
def node_session(aid, minutes):
    body = json.dumps({"node_id": "nd-a", "account_id": aid, "minutes": minutes}).encode()
    sign = hmac.new(node_secret.encode(), body, hashlib.sha256).hexdigest()
    return c.post("/v1/node/session", content=body, headers={"x-node-sign": sign})

# #3 — битое/чужое тело node/session → 422, НЕ 500 (и не падает до авторизации)
check(c.post("/v1/node/session", content=b"{not json", headers={"x-node-sign": "x"}).status_code == 422,
      "#3 node/session битое тело → 422 (был 500)")
check(c.post("/v1/node/session", content=b"[1,2,3]", headers={"x-node-sign": "x"}).status_code == 422,
      "#3 node/session массив вместо объекта → 422")

# #1 — отрицательные minutes НЕ начисляют баланс
u = reg("payer1"); tok, aid = u["token"], u["account_id"]
c.post("/v1/billing/purchase", json={"product": "balance_100h", "method": "crypto"}, headers=hdr(tok))
bal0 = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["active_minutes_left"]
node_session(aid, -100000)
bal1 = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["active_minutes_left"]
check(bal1 <= bal0, "#1 отрицательные minutes НЕ начисляют баланс")
node_session(aid, 10)
bal2 = c.get("/v1/billing/status", headers=hdr(tok)).json()["subscription"]["active_minutes_left"]
check(bal2 == bal1 - 10, "#1 нормальное списание работает (−10)")

# #5 — аноним на /v1/wheel и /v1/referral → 400, НЕ 500
anon = c.post("/v1/account/anon", json={"label": "a"}).json()["token"]
check(c.get("/v1/wheel", headers=hdr(anon)).status_code == 400, "#5 аноним /v1/wheel → 400 (был 500)")
check(c.get("/v1/referral", headers=hdr(anon)).status_code == 400, "#5 аноним /v1/referral → 400 (был 500)")

# #2 — многоразовый ключ привязан к АККАУНТУ: перелогин не тратит новые uses
code = c.post("/v1/admin/issue", headers=ADMIN, json={"plan": "pro", "grant_days": 10, "uses": 3}).json()["code"]
u2 = reg("multi")
r1 = c.post("/v1/key/redeem", json={"code": code}, headers=hdr(u2["token"]))
check(r1.status_code == 200 and r1.json()["idempotent"] is False, "#2 многоразовый ключ: первое погашение")
# перелогин защищённым путём — recovery-код (вход по одному нику отклоняется)
relog = c.post("/v1/account/login", json={"recovery_code": u2["recovery_code"]}).json()
r2 = c.post("/v1/key/redeem", json={"code": code}, headers=hdr(relog["token"]))
check(r2.status_code == 200 and r2.json()["idempotent"] is True,
      "#2 тот же аккаунт после перелогина → идемпотентно (не тратит use)")

# #7 — оплата ключом видна в billing (идентити-слой), переживает перелогин
st = c.get("/v1/billing/status", headers=hdr(relog["token"])).json()["subscription"]
check(st["days_left"] >= 10, "#7 оплата ключом отражена в billing (days_left>=10)")

# #4 — проваленный платёж нельзя позже «доплатить» вебхуком
server.SANDBOX_PAY = False
def pay_wh(body):
    raw = json.dumps(body).encode()
    sig = hmac.new(server.PAY_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return c.post("/v1/billing/webhook", content=raw, headers={"x-pay-sig": sig})
pid = c.post("/v1/billing/purchase", json={"product": "premium_month", "method": "sbp"},
             headers=hdr(u2["token"])).json()["payment_id"]
pay_wh({"payment_id": pid, "status": "failed"})
d_before = c.get("/v1/billing/status", headers=hdr(u2["token"])).json()["subscription"]["days_left"]
w = pay_wh({"payment_id": pid, "status": "completed"})
check(w.json().get("idempotent") is True, "#4 completed после failed → отклонён (idempotent)")
d_after = c.get("/v1/billing/status", headers=hdr(u2["token"])).json()["subscription"]["days_left"]
check(d_before == d_after, "#4 проваленный платёж НЕ начислен позже")
server.SANDBOX_PAY = True

# #6 — фрод-инвайтер не зарабатывает на новых регистрациях
inv = reg("fraudster"); inv_aid = inv["account_id"]; invite = inv["invite_code"]
c.post("/v1/admin/fraud", json={"account_id": inv_aid}, headers=ADMIN)
d_i0 = server.idaccounts[inv_aid].get("sub", {}).get("days_left", 0)
reg("victim", invite=invite)
d_i1 = server.idaccounts[inv_aid].get("sub", {}).get("days_left", 0)
check(d_i1 == d_i0, "#6 фрод-инвайтер НЕ получает дни за новую регистрацию")

# #9 — повреждённый стор не теряется молча (уходит в .corrupt), не роняет старт
bad = os.path.join(os.getcwd(), "corrupt_store.json")
open(bad, "w").write("{ not valid json ")
res = server.persist.jload(bad, {"default": True})
check(res == {"default": True}, "#9 битый стор → default (не падаем)")
check(os.path.exists(bad + ".corrupt"), "#9 битый стор отложен в .corrupt (данные не потеряны)")

print(f"\n=== ИТОГ: {ok} OK, 0 FAIL ===")
