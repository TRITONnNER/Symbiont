#!/usr/bin/env python3
"""
register_node.py — узел сам вписывает себя в манифест бэкенда (без ручного publish_nodes).

Берёт manifest_node.json (публичную запись узла), подписывает её HMAC-SHA256 на
секрете оператора (SYMBIONT_NODE_SECRET) и POST'ит на /v1/node/register. Бэкенд
проверяет подпись, прогоняет юр-предохранитель и обновляет подписанный манифест.

Использование (обычно вызывается из bootstrap_vps.sh для узлов с --no-backend):
  SYMBIONT_NODE_SECRET=<секрет> python3 register_node.py \
      --node /etc/symbiont/out/manifest_node.json \
      --backend https://api.example.com

Код возврата 0 = узел зарегистрирован.
"""
from __future__ import annotations
import argparse, hashlib, hmac, json, os, sys, urllib.request


def main() -> int:
    ap = argparse.ArgumentParser(description="Саморегистрация узла в манифесте бэкенда")
    ap.add_argument("--node", required=True, help="путь к manifest_node.json")
    ap.add_argument("--backend", required=True, help="база бэкенда, напр. https://api.example.com")
    ap.add_argument("--secret", default=os.environ.get("SYMBIONT_NODE_SECRET", ""),
                    help="секрет оператора (или env SYMBIONT_NODE_SECRET)")
    ap.add_argument("--secret-out", default=None,
                    help="куда записать выданный ПЕР-УЗЛОВОЙ секрет (для heartbeat-таймера)")
    args = ap.parse_args()
    if not args.secret:
        print("ОШИБКА: задай секрет оператора (--secret или SYMBIONT_NODE_SECRET)", file=sys.stderr)
        return 2

    # канонизируем тело узла, чтобы HMAC на клиенте и сервере считался от одних байт
    node = json.load(open(args.node, encoding="utf-8"))
    raw = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(args.secret.encode(), raw, hashlib.sha256).hexdigest()

    url = args.backend.rstrip("/") + "/v1/node/register"
    req = urllib.request.Request(url, data=raw, method="POST",
                                 headers={"content-type": "application/json", "x-node-sig": sig})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
        # сохранить ПЕР-УЗЛОВОЙ секрет (для последующих heartbeat/session-вызовов)
        if args.secret_out and resp.get("node_secret"):
            os.makedirs(os.path.dirname(os.path.abspath(args.secret_out)), exist_ok=True)
            with open(args.secret_out, "w", encoding="utf-8") as f:
                f.write(resp["node_secret"])
            try:
                os.chmod(args.secret_out, 0o600)
            except OSError:
                pass
        print(f"[symbiont] узел зарегистрирован: id={resp.get('id')} "
              f"всего узлов={resp.get('total_nodes')} версия манифеста={resp.get('version')}")
        return 0
    except urllib.error.HTTPError as e:
        print(f"ОШИБКА регистрации ({e.code}): {e.read().decode()[:200]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ОШИБКА сети: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
