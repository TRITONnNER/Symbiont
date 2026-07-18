# backend/selftest_audit_fixes3.py — регрессия на правки аудита (3-я волна).
# Покрывает уязвимости, найденные сквозным аудитом:
#   1) вход по ПУБЛИЧНОМУ алиасу без секрета отклонён (беспарольный аккаунт не
#      захватывается по нику; recovery-код — единственный ключ, ARCHITECTURE.md);
#   2) повторное (идемпотентное) погашение одноразового ключа НЕ продлевает подписку;
#   3) фрод-флаг снимает и ПОЖИЗНЕННЫЙ owner-грант (coverage → none, узел не пускает);
#   4) экономика: цена витрины == реально списываемая (правки панели действуют на списание).
import os, tempfile, hmac, hashlib, json
os.environ.setdefault("SYMBIONT_ADMIN_TOKEN", "adm")
os.environ.setdefault("SYMBIONT_NODE_SECRET", "nodesec")
os.chdir(tempfile.mkdtemp())          # чистый каталог: свои json, не трогаем рабочие
import sys
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

def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}

# ── 1) захват аккаунта по публичному нику ─────────────────────────────────────
r = c.post("/v1/account/register", json={"aliases": [{"value": "victim", "kind": "nick"}]})
v_aid = r.json()["account_id"]; v_rec = r.json()["recovery_code"]
c.post("/v1/admin/grant", headers=ADMIN,
       json={"account_id": v_aid, "tier": "ultimate", "days": 365})
# атакующий знает только публичный ник — доступа быть не должно
r = c.post("/v1/account/login", json={"alias": {"value": "victim", "kind": "nick"}})
check(r.status_code == 401, "вход по одному нику (беспарольный аккаунт) → 401")
# владелец аккаунта входит своим recovery-кодом — работает
r = c.post("/v1/account/recover", json={"recovery_code": v_rec})
check(r.status_code == 200 and r.json()["account_id"] == v_aid, "recovery-код по-прежнему пускает владельца")
# аккаунт С паролем: без пароля — отказ, с паролем — вход
c.post("/v1/account/register", json={"aliases": [{"value": "withpw", "kind": "nick"}], "password": "s3cret"})
check(c.post("/v1/account/login", json={"alias": {"value": "withpw", "kind": "nick"}}).status_code == 401,
      "аккаунт с паролем: вход по нику без пароля → 401")
check(c.post("/v1/account/login",
             json={"alias": {"value": "withpw", "kind": "nick"}, "password": "s3cret"}).status_code == 200,
      "аккаунт с паролем: ник+верный пароль → 200")

# ── 2) повторное погашение одноразового ключа не продлевает ───────────────────
code = c.post("/v1/admin/issue", headers=ADMIN,
              json={"plan": "premium", "grant_days": 30, "uses": 1}).json()["code"]
anon = c.post("/v1/account/anon", json={"label": "a"}).json()["token"]
first = c.post("/v1/key/redeem", json={"code": code}, headers=hdr(anon)).json()
check(first["idempotent"] is False, "одноразовый ключ: первое погашение (не идемпотентно)")
paid1 = first["paidUntil"]
for _ in range(3):
    again = c.post("/v1/key/redeem", json={"code": code}, headers=hdr(anon)).json()
check(again["idempotent"] is True and again["paidUntil"] == paid1,
      "повтор погашения идемпотентен и НЕ продлевает paidUntil")

# ── 3) фрод снимает пожизненный грант ─────────────────────────────────────────
node = {"id": "nl-x", "country": "NL", "code": "NL", "protocols": ["reality"],
        "host": "1.2.3.4", "transport": {}}
raw = json.dumps(node).encode()
nsig = hmac.new(b"nodesec", raw, hashlib.sha256).hexdigest()
nsecret = c.post("/v1/node/register", content=raw,
                 headers={"x-node-sig": nsig, "content-type": "application/json"}).json()["node_secret"]
r = c.post("/v1/account/register", json={"aliases": [{"value": "farm", "kind": "nick"}]})
f_aid = r.json()["account_id"]
c.post("/v1/admin/grant", headers=ADMIN,
       json={"account_id": f_aid, "tier": "ultimate", "days": None, "reason": "comp"})
def authorize():
    body = json.dumps({"node_id": "nl-x", "account_id": f_aid}).encode()
    s = hmac.new(nsecret.encode(), body, hashlib.sha256).hexdigest()
    return c.post("/v1/node/authorize", content=body,
                  headers={"x-node-sign": s, "content-type": "application/json"}).json()
check(server._coverage(f_aid) == "lifetime" and authorize()["allowed"], "до фрода: пожизненное покрытие, узел пускает")
c.post("/v1/admin/fraud", headers=ADMIN, json={"account_id": f_aid})
check(server._coverage(f_aid) == "none" and authorize()["allowed"] is False,
      "после фрода: покрытие снято (none), узел НЕ пускает")

# ── 4) цена витрины == списываемая ────────────────────────────────────────────
c.post("/v1/admin/economy", headers=ADMIN,
       json={"economy": {"prices": {"ru": {"premium": {"month": 999}}}}})
shown = c.get("/v1/config/economy").json()["prices"]["ru"]["premium"]["month"]
buyer = c.post("/v1/account/register", json={"aliases": [{"value": "buyer", "kind": "nick"}]}).json()["token"]
charged = c.post("/v1/billing/purchase", headers=hdr(buyer),
                 json={"product": "premium_month", "method": "sbp", "region": "ru"}).json()["amount"]
check(shown == 999 and charged == 999, "правка цены в панели действует и на витрину, и на списание")

print(f"\nselftest_audit_fixes3: {ok}/{ok} зелёные")
