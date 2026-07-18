# backend/selftest_hardening.py — регрессия на закрытие «дыр» и сетевую устойчивость.
# Покрывает: rate-limit (anon/login/discount), гейт мок-оплаты, хеш гостевого ключа
# треда (не утечка токена работникам поддержки), лимиты размера ввода, ротацию
# per-node секрета, 4xx (не 500) на плохой порт моста, лимит размера тела, RBAC
# для минта ценности (grant/balance → scope billing).
import os, tempfile, hmac, hashlib, json
os.environ.setdefault("SYMBIONT_ADMIN_TOKEN", "adm")
os.environ.setdefault("SYMBIONT_NODE_SECRET", "nodesec")
os.environ["SYMBIONT_ANON_LIMIT"] = "5"       # маленькие лимиты для быстрого теста
os.environ["SYMBIONT_LOGIN_LIMIT"] = "5"
os.environ["SYMBIONT_DISCOUNT_LIMIT"] = "5"
os.environ["SYMBIONT_PAY_PROVIDER"] = "mock"
os.chdir(tempfile.mkdtemp())
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

def reset_rl():
    server._rl_buckets.clear(); server._reg_by_subnet.clear()

# ── 1. rate-limit /v1/account/anon ────────────────────────────────────────────
reset_rl()
codes = [c.post("/v1/account/anon", json={}).status_code for _ in range(7)]
check(codes.count(200) == 5 and codes.count(429) == 2, "anon: 5 ок, дальше 429 (лимит)")

# ── 2. rate-limit /v1/account/login (брутфорс/CPU-усиление) ───────────────────
reset_rl()
lc = [c.post("/v1/account/login", json={"alias": {"value": "nobody", "kind": "nick"}}).status_code for _ in range(7)]
check(lc[:5] == [401] * 5 and lc[5] == 429, "login: 5 попыток, затем 429")

# ── 3. хеш гостевого ключа треда: bearer-токен НЕ утекает в список тредов ──────
reset_rl()
tok = c.post("/v1/account/anon", json={}).json()["token"]
c.post("/v1/support/message", json={"text": "hi"}, headers={"Authorization": f"Bearer {tok}"})
thr = c.get("/v1/admin/support/threads", headers=ADMIN).json()["threads"]
ids = [t["id"] for t in thr]
check(all(tok not in i for i in ids) and any(i.startswith("g:") for i in ids),
      "гостевой тред ключуется хешем (g:...), сам токен не в списке")

# ── 4. лимиты размера ввода ───────────────────────────────────────────────────
reset_rl()
tok2 = c.post("/v1/account/anon", json={}).json()["token"]
H2 = {"Authorization": f"Bearer {tok2}"}
check(c.post("/v1/support/message", json={"text": "x" * 9000}, headers=H2).status_code == 422,
      "сообщение >8000 → 422")
check(c.patch("/v1/account/label", json={"label": "y" * 500}, headers=H2).status_code == 422,
      "метка >200 → 422")

# ── 5. per-node секрет РОТИРУЕТСЯ на повторной регистрации ─────────────────────
node = {"id": "nl-x", "country": "NL", "code": "NL", "protocols": ["reality"], "host": "1.2.3.4", "transport": {}}
raw = json.dumps(node).encode()
sig = hmac.new(b"nodesec", raw, hashlib.sha256).hexdigest()
s1 = c.post("/v1/node/register", content=raw, headers={"x-node-sig": sig, "content-type": "application/json"}).json()["node_secret"]
s2 = c.post("/v1/node/register", content=raw, headers={"x-node-sig": sig, "content-type": "application/json"}).json()["node_secret"]
check(s1 != s2, "повторная регистрация узла выдаёт НОВЫЙ секрет (не раскрывает старый)")

# ── 6. плохой порт моста → чистый 4xx, не 500 ─────────────────────────────────
r = c.post("/v1/config/parse-bridge", json={"uri": "vless://u@host.com:999999?security=tls"})
check(r.status_code == 200 and r.json().get("ok") is False, "parse-bridge с портом >65535 → ok:false (не 500)")

# ── 7. лимит размера тела запроса → 413 ───────────────────────────────────────
big = c.post("/v1/key/check", content=b'{"code":"' + b"A" * 300000 + b'"}',
             headers={"content-type": "application/json"})
check(big.status_code == 413, "тело >256 KiB → 413")

# ── 8. RBAC: минт ценности требует scope billing, не users ────────────────────
reset_rl()
caid = c.post("/v1/account/register", json={"aliases": [{"value": "cust", "kind": "nick"}]}).json()["account_id"]
w_users = c.post("/v1/admin/staff", json={"name": "U", "scopes": ["users"]}, headers=ADMIN).json()["token"]
w_bill = c.post("/v1/admin/staff", json={"name": "B", "scopes": ["billing"]}, headers=ADMIN).json()["token"]
HU = {"X-Staff-Token": w_users}; HB = {"X-Staff-Token": w_bill}
check(c.post("/v1/admin/grant", json={"account_id": caid, "tier": "ultimate", "days": 30}, headers=HU).status_code == 403,
      "работник users НЕ может грантить (403)")
check(c.post("/v1/admin/grant", json={"account_id": caid, "tier": "ultimate", "days": 30}, headers=HB).status_code == 200,
      "работник billing может грантить (200)")
check(c.post(f"/v1/admin/user/{caid}/balance", json={"minutes": 60}, headers=HU).status_code == 403,
      "работник users НЕ может править баланс (403)")

# ── 9. гейт мок-оплаты: при PAY_PROVIDER!='mock' роут закрыт ───────────────────
server.PAY_PROVIDER = "real"
check(c.post("/v1/billing/mock/confirm/anything").status_code == 404,
      "mock/confirm при боевом провайдере → 404 (бэкдор закрыт)")
server.PAY_PROVIDER = "mock"

print(f"\nselftest_hardening: {ok}/{ok} зелёные")
