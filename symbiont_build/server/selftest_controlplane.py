#!/usr/bin/env python3
"""
selftest_controlplane.py — проверка control-plane манифеста (без VPS):
  1) роли узла (node→edge / relay→relay+whiteIp);
  2) ЮРИДИЧЕСКИЙ предохранитель — отказ генерировать/публиковать публичный exit в РФ;
  3) canary-распределение — доля клиентов в волне ≈ rollout% (тот же FNV-1a, что в клиенте);
  4) монотонность — старый/равный манифест не применяется (защита от отката);
  5) rollout в подписанном теле + операторский /v1/admin/rollout (re-sign, версия не растёт).

Запуск:  python3 server/selftest_controlplane.py     (код 0 = всё ок)
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
    if not cond: FAILS += 1

def gen(out, *extra):
    return subprocess.run([sys.executable, os.path.join(HERE, "gen_server.py"),
                           "--host", "203.0.113.10", "--out", out, *extra],
                          capture_output=True, text=True)

def canary_bucket(token: str) -> int:
    """Точная копия _canaryBucket из app_state.dart (FNV-1a 32-bit)."""
    h = 0x811c9dc5
    for b in token.encode("utf-8"):
        h = (h ^ b) & 0xffffffff
        h = (h * 0x01000193) & 0xffffffff
    return h % 100

def main():
    work = tempfile.mkdtemp(prefix="symbiont_cp_")
    try:
        # 1) РОЛИ
        o1 = os.path.join(work, "node"); gen(o1, "--code", "NL", "--country", "Netherlands", "--role", "node")
        n1 = json.load(open(os.path.join(o1, "manifest_node.json")))
        ok("node → roles=['edge']", n1["roles"] == ["edge"] and not n1.get("whiteIp"))
        o2 = os.path.join(work, "relay"); gen(o2, "--code", "IS", "--country", "Iceland", "--role", "relay")
        n2 = json.load(open(os.path.join(o2, "manifest_node.json")))
        ok("relay → roles=['relay'] + whiteIp", n2["roles"] == ["relay"] and n2.get("whiteIp") is True)

        # 2) ЮР-ПРЕДОХРАНИТЕЛЬ
        r = gen(os.path.join(work, "ru_exit"), "--code", "RU", "--country", "Russia", "--role", "node")
        ok("gen_server ОТКАЗывает в РФ-exit (node, code=RU)", r.returncode != 0 and "ОТКАЗ" in (r.stdout + r.stderr))
        r2 = gen(os.path.join(work, "ru_relay"), "--code", "RU", "--country", "Russia", "--role", "relay")
        ok("gen_server РАЗРЕШАЕТ relay в РФ (с предупреждением)", r2.returncode == 0)
        # publish_nodes: РФ-exit, собранный вручную, отклоняется
        bad = os.path.join(work, "bad_node.json")
        json.dump({"id": "ru-x", "country": "Russia", "code": "RU", "protocols": ["reality"],
                   "roles": ["edge"], "host": "5.6.7.8", "transport": {"reality": {}}}, open(bad, "w"))
        pr = subprocess.run([sys.executable, os.path.join(HERE, "publish_nodes.py"), bad,
                             "--out", os.path.join(work, "n.json")], capture_output=True, text=True)
        published = json.load(open(os.path.join(work, "n.json")))
        ok("publish_nodes отклоняет РФ-exit (SKIPPED, 0 узлов)",
           "SKIPPED" in pr.stdout and len(published.get("nodes", [])) == 0)

        # 3) CANARY-РАСПРЕДЕЛЕНИЕ
        N = 3000
        tokens = [f"tok-{i}-{i*7%13}" for i in range(N)]
        for pct, tol in [(1, 1.2), (5, 2), (25, 3), (100, 0)]:
            inwave = sum(1 for t in tokens if canary_bucket(t) < pct)
            frac = 100 * inwave / N
            ok(f"canary rollout={pct}% → в волне ≈{frac:.1f}% (ожидали ~{pct}%)",
               abs(frac - pct) <= tol)

        # 4) МОНОТОННОСТЬ (логика клиента: apply iff version>applied AND bucket<rollout)
        def client_applies(version, applied, rollout, bucket):
            return version > applied and bucket < rollout
        ok("монотонность: version<applied не применяется", not client_applies(184, 185, 100, 0))
        ok("монотонность: version==applied не применяется", not client_applies(185, 185, 100, 0))
        ok("новее + в волне → применяется", client_applies(186, 185, 100, 0))
        ok("новее, но вне волны → не применяется", not client_applies(186, 185, 5, 50))

        # 5) rollout в подписанном теле + admin/rollout
        bdir = os.path.join(work, "backend"); shutil.copytree(BACKEND, bdir)
        sys.path.insert(0, bdir); cwd = os.getcwd(); os.chdir(bdir)
        import importlib
        for m in ("server", "manifest", "keys"):
            if m in sys.modules: del sys.modules[m]
        server = importlib.import_module("server")
        from fastapi.testclient import TestClient
        from manifest import verify_manifest
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        c = TestClient(server.app)
        man0 = c.get("/v1/manifest").json()
        ok("манифест содержит rollout (по умолчанию 100)", man0.get("rollout") == 100)
        v0 = man0["version"]
        # операторский рычаг: ставим волну 5%
        rr = c.post("/v1/admin/rollout", json={"percent": 5}, headers={"x-admin-token": server.ADMIN_TOKEN})
        ok("admin/rollout принят (200, rollout=5)", rr.status_code == 200 and rr.json()["rollout"] == 5)
        man1 = c.get("/v1/manifest", params={"since": 0}).json()
        ok("rollout применился в манифесте (=5)", man1.get("rollout") == 5)
        ok("версия НЕ изменилась при смене rollout", man1["version"] == v0)
        pub = base64.b64decode(c.get("/v1/pubkey").json()["ed25519"])
        ok("подпись валидна после re-sign", verify_manifest(man1, Ed25519PublicKey.from_public_bytes(pub)))
        os.chdir(cwd)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    print("РЕЗУЛЬТАТ: control-plane манифеста работает ✔" if FAILS == 0 else f"РЕЗУЛЬТАТ: провалов {FAILS} ✗")
    return 0 if FAILS == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
