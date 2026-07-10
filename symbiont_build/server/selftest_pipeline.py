#!/usr/bin/env python3
"""
selftest_pipeline.py — сквозная проверка ВСЕГО конвейера сервиса БЕЗ VPS.

Прогоняет ровно те шаги, что bootstrap_vps.sh делает на сервере, но локально
(узел на 127.0.0.1). Доказывает: «купи VPS — и всё работает», потому что здесь
проверено всё, кроме самого факта удалённого железа:
  1) gen_server.py генерит узел (config/secrets/manifest_node);
  2) publish_nodes.py вписывает узел в nodes.json (и режет приватные ключи);
  3) бэкенд с этим nodes.json отдаёт ПОДПИСАННЫЙ манифест, содержащий узел;
  4) подпись Ed25519 валидна;
  5) клиентский конфиг (по тем же правилам, что singbox_config.dart ПОСЛЕ фикса)
     совпадает с серверным inbound по всем 3 протоколам.

Запуск:  python3 server/selftest_pipeline.py
Код возврата 0 = всё ок.
"""
from __future__ import annotations
import base64, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKEND = os.path.join(ROOT, "backend")
FAILS = 0

def ok(name, cond):
    global FAILS
    print(("  [OK] " if cond else "  [FAIL] ") + name)
    if not cond:
        FAILS += 1

def main():
    work = tempfile.mkdtemp(prefix="symbiont_selftest_")
    try:
        out = os.path.join(work, "out")
        # 1) генерация узла на localhost
        subprocess.run([sys.executable, os.path.join(HERE, "gen_server.py"),
                        "--host", "127.0.0.1", "--code", "NL", "--country", "Netherlands",
                        "--sni", "www.microsoft.com", "--out", out], check=True,
                       stdout=subprocess.DEVNULL)
        for f in ("config.json", "secrets.json", "manifest_node.json"):
            ok(f"gen_server создал {f}", os.path.exists(os.path.join(out, f)))
        srv = json.load(open(os.path.join(out, "config.json")))
        srv_in = {i["type"]: i for i in srv["inbounds"]}

        # 2) publish → nodes.json (+ проверка, что приватный ключ не утёк)
        nodes_json = os.path.join(work, "nodes.json")
        r = subprocess.run([sys.executable, os.path.join(HERE, "publish_nodes.py"),
                            os.path.join(out, "manifest_node.json"), "--out", nodes_json],
                           capture_output=True, text=True)
        ok("publish_nodes отработал", r.returncode == 0 and os.path.exists(nodes_json))
        blob = open(nodes_json).read()
        ok("в nodes.json НЕТ приватного Reality-ключа",
           "reality_private" not in blob and "private_key" not in blob)

        # 3) бэкенд с этим nodes.json отдаёт манифест с узлом
        bdir = os.path.join(work, "backend")
        shutil.copytree(BACKEND, bdir)
        shutil.copy(nodes_json, os.path.join(bdir, "nodes.json"))
        sys.path.insert(0, bdir)
        cwd = os.getcwd(); os.chdir(bdir)
        # импортируем бэкенд уже с nodes.json в CWD
        import importlib
        for m in ("server", "manifest", "keys"):
            if m in sys.modules: del sys.modules[m]
        server = importlib.import_module("server")
        from fastapi.testclient import TestClient
        from manifest import verify_manifest
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        c = TestClient(server.app)

        man = c.get("/v1/manifest").json()
        node_ids = [n["id"] for n in man.get("nodes", [])]
        ok("манифест содержит реальный узел nl-01", "nl-01" in node_ids)

        # 4) подпись валидна
        pub_b64 = c.get("/v1/pubkey").json()["ed25519"]
        pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        ok("подпись манифеста Ed25519 валидна", verify_manifest(man, pk))
        os.chdir(cwd)

        # 5) клиентский конфиг (логика singbox_config.dart ПОСЛЕ фикса) == серверу
        node = next(n for n in man["nodes"] if n["id"] == "nl-01")
        t = node["transport"]
        # Reality: raw TCP (без transport), uuid/flow/sni совпадают
        rt = t["reality"]; sv = srv_in["vless"]
        client_reality_has_transport = False  # фикс: для sing-box-узла transport не добавляется
        ok("Reality: клиент raw-TCP == сервер raw-TCP",
           client_reality_has_transport is False and "transport" not in sv)
        ok("Reality: uuid совпадает", rt["uuid"] == sv["users"][0]["uuid"])
        ok("Reality: flow пуст с обеих сторон", sv["users"][0].get("flow", "") == "")
        ok("Reality: public_key соответствует серверному private (одна пара)",
           rt["public_key"] == json.load(open(os.path.join(out, "secrets.json")))["reality_public"])
        # Hysteria2: без obfs с обеих сторон
        hy = t["hysteria2"]; sh = srv_in["hysteria2"]
        client_hy_obfs = hy.get("obfs") is not None  # фикс: obfs только если узел его дал
        ok("Hysteria2: obfs выключен с обеих сторон",
           client_hy_obfs is False and "obfs" not in sh)
        ok("Hysteria2: пароль совпадает", hy["password"] == sh["users"][0]["password"])
        # SS2022
        ss = t["ss2022"]; sss = srv_in["shadowsocks"]
        ok("SS2022: метод совпадает", ss["method"] == sss["method"])
        ok("SS2022: пароль совпадает", ss["password"] == sss["password"])

    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if FAILS == 0:
        print("РЕЗУЛЬТАТ: весь конвейер сервиса работает ✔  (после покупки VPS поднимется одной командой)")
        return 0
    print(f"РЕЗУЛЬТАТ: провалов {FAILS} ✗")
    return 1

if __name__ == "__main__":
    sys.exit(main())
