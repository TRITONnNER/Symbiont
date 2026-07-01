#!/usr/bin/env python3
"""
heartbeat_node.py — узел раз в ~минуту говорит бэкенду «я жив» (+ текущая загрузка).

Подпись — ПЕР-УЗЛОВЫМ секретом (выдан при регистрации в /v1/node/register, лежит в
файле). Нет heartbeat дольше порога (NODE_STALE_SEC на бэкенде) → узел выпадает из
подписанного манифеста (само-починка); снова зашумел → возвращается.

Обычно ставится как systemd-таймер из bootstrap_vps.sh (путь --register-to). Пример:
  python3 heartbeat_node.py --node /etc/symbiont/out/manifest_node.json \
      --backend https://api.example.com --secret /etc/symbiont/node_secret
"""
from __future__ import annotations
import argparse, hashlib, hmac, json, os, sys, urllib.request


def _loadpct() -> "int | None":
    """Грубая текущая загрузка узла: 1-мин loadavg / число ядер → проценты (0..100)."""
    try:
        la1 = os.getloadavg()[0]
        n = os.cpu_count() or 1
        return max(0, min(100, round(la1 / n * 100)))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Heartbeat узла в бэкенд Симбионта")
    ap.add_argument("--node-id", help="id узла (или возьмётся из --node)")
    ap.add_argument("--node", help="путь к manifest_node.json (чтобы взять id)")
    ap.add_argument("--backend", required=True, help="база бэкенда, напр. https://api.example.com")
    ap.add_argument("--secret", default=os.environ.get("SYMBIONT_NODE_SECRET_SELF", ""),
                    help="ПЕР-УЗЛОВОЙ секрет: значение, путь к файлу, или env SYMBIONT_NODE_SECRET_SELF")
    args = ap.parse_args()

    node_id = args.node_id
    if not node_id and args.node:
        node_id = json.load(open(args.node, encoding="utf-8")).get("id")
    if not node_id:
        print("ОШИБКА: нужен --node-id или --node", file=sys.stderr)
        return 2

    secret = args.secret
    if secret and os.path.exists(secret):        # передан путь к файлу с секретом
        secret = open(secret, encoding="utf-8").read().strip()
    if not secret:
        print("ОШИБКА: нужен пер-узловой секрет (--secret или SYMBIONT_NODE_SECRET_SELF)", file=sys.stderr)
        return 2

    body = {"node_id": node_id}
    lp = _loadpct()
    if lp is not None:
        body["load_pct"] = lp
    raw = json.dumps(body).encode()
    sign = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    url = args.backend.rstrip("/") + "/v1/node/heartbeat"
    req = urllib.request.Request(url, data=raw, method="POST",
                                 headers={"content-type": "application/json", "x-node-sign": sign})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
        print(f"[symbiont] heartbeat ok: revived={resp.get('revived')} version={resp.get('version')}")
        return 0
    except urllib.error.HTTPError as e:
        print(f"ОШИБКА heartbeat ({e.code}): {e.read().decode()[:200]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ОШИБКА сети: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
