#!/usr/bin/env python3
"""
selftest_persistence.py — проверка (без VPS):
  1) ПЕРСИСТЕНТНОСТЬ: аккаунт, активированный ключ и диалог поддержки переживают
     рестарт бэкенда (имитируем перезапуском модуля с теми же файлами на диске);
  2) САМОРЕГИСТРАЦИЯ узла: HMAC-подписанный POST /v1/node/register вписывает узел в
     подписанный манифест; плохая подпись → 401; РФ-exit → 403; РФ-relay → ок.

Запуск:  python3 server/selftest_persistence.py     (код 0 = всё ок)
"""
from __future__ import annotations
import base64, hashlib, hmac, importlib, json, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKEND = os.path.join(ROOT, "backend")
NODE_SECRET = "demo-node-secret"
FAILS = 0

def ok(name, cond):
    global FAILS
    print(("  [OK] " if cond else "  [FAIL] ") + name)
    if not cond: FAILS += 1

def fresh_server(bdir):
    """(Пере)импортирует бэкенд с CWD=bdir — читает состояние из БД symbiont_state.db."""
    global NODE_SECRET
    for m in ("server", "manifest", "keys", "persist"):
        if m in sys.modules: del sys.modules[m]
    os.chdir(bdir)
    mod = importlib.import_module("server")
    NODE_SECRET = mod.NODE_SECRET   # секрет генерируется/читается из secrets.json (стабилен между рестартами)
    return mod

def signed(node: dict):
    raw = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return raw, hmac.new(NODE_SECRET.encode(), raw, hashlib.sha256).hexdigest()

def main():
    work = tempfile.mkdtemp(prefix="symbiont_persist_")
    bdir = os.path.join(work, "backend"); shutil.copytree(BACKEND, bdir)
    sys.path.insert(0, bdir); cwd = os.getcwd()
    from fastapi.testclient import TestClient
    try:
        # ── ПЕРСИСТЕНТНОСТЬ ──
        server = fresh_server(bdir)
        c = TestClient(server.app)
        tok = c.post("/v1/account/anon", json={"label": "гость"}).json()["token"]
        code = c.post("/v1/admin/issue", json={"plan": "pro", "grant_days": 30, "uses": 1},
                      headers={"x-admin-token": server.ADMIN_TOKEN}).json()["code"]
        r = c.post("/v1/key/redeem", headers={"authorization": f"Bearer {tok}"}, json={"code": code})
        ok("ключ погашен (план pro)", r.status_code == 200 and r.json()["plan"] == "pro")
        c.post("/v1/support/message", headers={"authorization": f"Bearer {tok}"}, json={"text": "тест-сообщение"})
        ok("состояние сохранено в реальную БД (symbiont_state.db)",
           os.path.exists(os.path.join(bdir, "symbiont_state.db")))

        # ── РЕСТАРТ ──
        server = fresh_server(bdir); c = TestClient(server.app)
        ok("аккаунт пережил рестарт (label принят)",
           c.patch("/v1/account/label", headers={"authorization": f"Bearer {tok}"}, json={"label": "x"}).status_code == 200)
        ri = c.post("/v1/key/redeem", headers={"authorization": f"Bearer {tok}"}, json={"code": code})
        ok("реестр ключей пережил рестарт (повтор идемпотентен)",
           ri.status_code == 200 and ri.json().get("idempotent") is True)
        th = c.post("/v1/support/message", headers={"authorization": f"Bearer {tok}"}, json={"text": "ещё"})
        thread = c.get("/v1/support/thread", headers={"authorization": f"Bearer {tok}"}).json()["messages"]
        ok("диалог поддержки пережил рестарт", any(m["text"] == "тест-сообщение" for m in thread))

        # ── САМОРЕГИСТРАЦИЯ ──
        node = {"id": "nl-09", "country": "Netherlands", "code": "NL", "protocols": ["reality"],
                "roles": ["edge"], "host": "203.0.113.99", "transport": {"reality": {"server": "203.0.113.99"}}}
        raw, sig = signed(node)
        rr = c.post("/v1/node/register", data=raw, headers={"content-type": "application/json", "x-node-sig": sig})
        ok("узел зарегистрирован по HMAC (200)", rr.status_code == 200 and rr.json()["id"] == "nl-09")
        man = c.get("/v1/manifest").json()
        ok("узел появился в манифесте", any(n["id"] == "nl-09" for n in man["nodes"]))
        from manifest import verify_manifest
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = base64.b64decode(c.get("/v1/pubkey").json()["ed25519"])
        ok("подпись манифеста валидна после регистрации",
           verify_manifest(man, Ed25519PublicKey.from_public_bytes(pub)))

        # плохая подпись
        bad = c.post("/v1/node/register", data=raw, headers={"content-type": "application/json", "x-node-sig": "deadbeef"})
        ok("плохая HMAC-подпись отклонена (401)", bad.status_code == 401)
        # РФ-exit запрещён
        ru_exit = {**node, "id": "ru-x", "code": "RU", "roles": ["edge"]}
        raw2, sig2 = signed(ru_exit)
        re_ = c.post("/v1/node/register", data=raw2, headers={"content-type": "application/json", "x-node-sig": sig2})
        ok("РФ-exit отклонён (403)", re_.status_code == 403)
        # РФ-relay разрешён
        ru_relay = {**node, "id": "ru-relay-09", "code": "RU", "roles": ["relay"]}
        raw3, sig3 = signed(ru_relay)
        rl = c.post("/v1/node/register", data=raw3, headers={"content-type": "application/json", "x-node-sig": sig3})
        ok("РФ-relay разрешён (200)", rl.status_code == 200)
    finally:
        os.chdir(cwd); shutil.rmtree(work, ignore_errors=True)

    print()
    print("РЕЗУЛЬТАТ: персистентность и саморегистрация работают ✔" if FAILS == 0 else f"РЕЗУЛЬТАТ: провалов {FAILS} ✗")
    return 0 if FAILS == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
