# backend/selftest_node_health.py — само-починка узлов (heartbeat + свип протухших).
# Регистрация → узел в манифесте; протух → выпал (версия ↑); heartbeat → вернулся
# (версия ↑, живая загрузка); подпись/неизвестный узел; юр-предохранитель РФ; ручной узел.
import os, tempfile, sys, json, hmac, hashlib
os.chdir(tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
import server
server.NODE_STALE_SEC = 3          # короткий порог протухания для теста
c = TestClient(server.app)

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1

def reg(node):
    raw = json.dumps(node).encode()
    sig = hmac.new(server.NODE_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return c.post("/v1/node/register", content=raw, headers={"x-node-sig": sig})

def hb(nid, secret, load=None):
    body = {"node_id": nid}
    if load is not None:
        body["load_pct"] = load
    raw = json.dumps(body).encode()
    sign = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return c.post("/v1/node/heartbeat", content=raw, headers={"x-node-sign": sign})

def manifest():
    m = c.get("/v1/manifest").json()
    return m["version"], {n["id"]: n for n in m["nodes"]}

NODE = {"id": "nl-h", "country": "Netherlands", "code": "NL", "protocols": ["reality"],
        "host": "203.0.113.9", "transport": {"reality": {}}, "roles": ["edge"], "loadPct": 20}

# 1) регистрация → узел в манифесте, выдан пер-узловой секрет
r = reg(NODE)
check(r.status_code == 200 and r.json().get("node_secret"), "регистрация узла → 200 + node_secret")
node_secret = r.json()["node_secret"]
v1, nodes = manifest()
check("nl-h" in nodes, "узел появился в подписанном манифесте")

# 2) протух (last_seen в прошлом) → выпал из манифеста, версия выросла
server._node_health["nl-h"]["last_seen"] = 0
v2, nodes = manifest()
check("nl-h" not in nodes, "протухший узел выпал из манифеста (само-починка)")
check(v2 > v1, f"версия выросла при выпадении узла ({v1}→{v2})")

# 3) heartbeat вернул узел + обновил живую загрузку
rr = hb("nl-h", node_secret, load=42)
check(rr.status_code == 200 and rr.json()["revived"] is True, "heartbeat вернул узел (revived=true)")
v3, nodes = manifest()
check("nl-h" in nodes and nodes["nl-h"]["loadPct"] == 42, "узел вернулся + живая загрузка 42%")
check(v3 > v2, f"версия выросла при возврате узла ({v2}→{v3})")

# 4) heartbeat: плохая подпись / неизвестный узел → 403
check(c.post("/v1/node/heartbeat", content=b'{"node_id":"nl-h"}',
             headers={"x-node-sign": "bad"}).status_code == 403, "heartbeat с плохой подписью → 403")
check(c.post("/v1/node/heartbeat", content=json.dumps({"node_id": "ghost"}).encode(),
             headers={"x-node-sign": "x"}).status_code == 403, "heartbeat неизвестного узла → 403")

# 5) юр-предохранитель: РФ-exit при регистрации по-прежнему запрещён
ru = {"id": "ru-x", "country": "Russia", "code": "RU", "protocols": ["reality"],
      "host": "5.6.7.8", "transport": {"reality": {}}, "roles": ["edge"]}
check(reg(ru).status_code == 403, "РФ-exit при регистрации → 403 (предохранитель цел)")

# 6) узел без health (опубликован вручную) — свип его НЕ трогает
data = server.persist.jload("nodes.json", {"nodes": []})
data["nodes"].append({"id": "manual-1", "country": "Finland", "code": "FI",
                      "protocols": ["reality"], "host": "9.9.9.9",
                      "transport": {"reality": {}}, "roles": ["edge"]})
server.persist.jsave("nodes.json", data)
check("manual-1" in server._healthy_ids(), "узел без heartbeat (ручной) остаётся живым")

print(f"\n=== ИТОГ: {ok} OK, 0 FAIL ===")
