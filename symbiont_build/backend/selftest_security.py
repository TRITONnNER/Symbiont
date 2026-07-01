# backend/selftest_security.py — секреты не из предсказуемых дефолтов; пер-узловые секреты.
import os, tempfile, sys, json, hmac, hashlib
os.chdir(tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# окружение не задаёт секреты → должны сгенерироваться
for k in ("SYMBIONT_ADMIN_TOKEN", "SYMBIONT_NODE_SECRET", "SYMBIONT_WHEEL_SEED"):
    os.environ.pop(k, None)
import server
from fastapi.testclient import TestClient
c = TestClient(server.app)

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1

# 1. секреты НЕ предсказуемые дефолты
check(server.ADMIN_TOKEN not in ("demo-admin-token", "", None) and len(server.ADMIN_TOKEN) >= 16, "ADMIN_TOKEN сгенерирован (не дефолт)")
check(server.NODE_SECRET not in ("demo-node-secret", "", None) and len(server.NODE_SECRET) >= 16, "NODE_SECRET сгенерирован (не дефолт)")
check(server.WHEEL_SEED not in ("dev-wheel-seed", "", None), "WHEEL_SEED сгенерирован (не дефолт)")

# 2. секреты сохранены в реальной БД (persist → SQLite) и стабильны
check(server.persist.exists("secrets.json"), "секреты сохранены в БД (persist)")
saved = server.persist.jload("secrets.json", {})
check(saved.get("admin_token") == server.ADMIN_TOKEN, "ADMIN_TOKEN сохранён стабильно")

# 3. env переопределяет (приоритет окружения)
import importlib
os.environ["SYMBIONT_ADMIN_TOKEN"] = "from-env-XYZ"
importlib.reload(server)
check(server.ADMIN_TOKEN == "from-env-XYZ", "env переопределяет сгенерированный секрет")
del os.environ["SYMBIONT_ADMIN_TOKEN"]
importlib.reload(server)
c = TestClient(server.app)

# 4. пер-узловой секрет выдаётся при регистрации и отличается от enrollment-секрета
nb = json.dumps({"id": "n-1", "country": "NL", "code": "NL", "protocols": ["reality"],
                 "host": "h", "transport": "tcp", "roles": ["entry"]}).encode()
nsig = hmac.new(server.NODE_SECRET.encode(), nb, hashlib.sha256).hexdigest()
rn = c.post("/v1/node/register", content=nb, headers={"x-node-sig": nsig}).json()
check(rn.get("node_secret") and rn["node_secret"] != server.NODE_SECRET, "узлу выдан собственный секрет (≠ enrollment)")

# 5. enrollment-секретом нельзя подписать node/session (нужен пер-узловой)
r = c.post("/v1/account/register", json={"aliases": [{"value": "u", "kind": "nick"}]}).json()
aid = r["account_id"]
raw = json.dumps({"node_id": "n-1", "account_id": aid, "minutes": 5}).encode()
bad = hmac.new(server.NODE_SECRET.encode(), raw, hashlib.sha256).hexdigest()  # enrollment, не пер-узловой
check(c.post("/v1/node/session", content=raw, headers={"x-node-sign": bad}).status_code == 403, "enrollment-секрет не годится для сессии узла")
good = hmac.new(rn["node_secret"].encode(), raw, hashlib.sha256).hexdigest()
check(c.post("/v1/node/session", content=raw, headers={"x-node-sign": good}).status_code == 200, "пер-узловой секрет работает")

print(f"\nВсего проверок: {ok}")
