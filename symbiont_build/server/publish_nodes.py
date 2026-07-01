#!/usr/bin/env python3
"""
publish_nodes.py — собирает узлы со всех серверов в один файл для бэкенда.

Каждый сервер при генерации даёт server/out/manifest_node.json. Этот скрипт
собирает их в общий nodes.json, который бэкенд подхватывает в манифест
(если положить рядом с server.py — см. патч load_nodes() в backend).

Клиент Симбионт ждёт транспорт-секреты в узле (transport.*). ВАЖНО: в манифест
идёт ТОЛЬКО публичная часть (reality public_key, uuid, пароли подключения) —
это и есть то, что клиент подставляет в свой конфиг. Приватный Reality-ключ
(secrets.json) ОСТАЁТСЯ ТОЛЬКО НА СЕРВЕРЕ и в манифест не попадает.

Использование:
  # собрать из нескольких файлов:
  python3 publish_nodes.py node1.json node2.json --out nodes.json
  # или из каталога, где лежат manifest_node.json в подпапках:
  python3 publish_nodes.py --dir ./servers --out nodes.json
"""
from __future__ import annotations
import argparse, glob, json, os, sys


def load_node(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        n = json.load(f)
    # минимальная валидация по схеме клиента
    req = ["id", "country", "code", "protocols", "host"]
    miss = [k for k in req if k not in n]
    if miss:
        raise ValueError(f"{path}: в узле нет полей {miss}")
    if "transport" not in n:
        raise ValueError(f"{path}: нет transport.* (узел не даст клиенту подключиться)")
    # на всякий случай: убеждаемся, что приватного ключа тут НЕТ
    blob = json.dumps(n)
    if "reality_private" in blob or "private_key" in blob:
        raise ValueError(f"{path}: в узле обнаружен ПРИВАТНЫЙ ключ — он не должен попадать в манифест!")
    # ЮРИДИЧЕСКИЙ ПРЕДОХРАНИТЕЛЬ: не публиковать публичный exit-узел в РФ (оператор
    # = «контролёр»: СОРМ, ОРИ/FGIS, ст. 13.52/13.53 КоАП). relay в РФ — допустим.
    roles = n.get("roles") or []
    is_relay = "relay" in roles
    if (n.get("code") or "").strip().upper() == "RU" and not is_relay:
        raise ValueError(f"{path}: ОТКАЗ — публичный exit-узел в РФ недопустим "
                         f"(юр-риски оператора). Помести exit вне РФ или сделай roles=['relay'].")
    return n


def main():
    ap = argparse.ArgumentParser(description="Сборка узлов для манифеста бэкенда")
    ap.add_argument("files", nargs="*", help="manifest_node.json файлы")
    ap.add_argument("--dir", help="каталог: соберёт все */manifest_node.json и *.json рекурсивно")
    ap.add_argument("--out", default="nodes.json", help="выходной файл")
    args = ap.parse_args()

    paths: list[str] = list(args.files)
    if args.dir:
        paths += glob.glob(os.path.join(args.dir, "**", "manifest_node.json"), recursive=True)
        paths += glob.glob(os.path.join(args.dir, "*.json"))
    paths = sorted(set(paths))
    if not paths:
        print("No node files passed. Examples:")
        print("  python3 publish_nodes.py out/manifest_node.json --out nodes.json")
        print("  python3 publish_nodes.py --dir ./servers --out nodes.json")
        sys.exit(1)

    nodes = []
    seen = set()
    for p in paths:
        if os.path.basename(p) in ("nodes.json", "secrets.json", "config.json"):
            continue
        try:
            n = load_node(p)
        except ValueError as e:
            print("  SKIPPED:", e)
            continue
        if n["id"] in seen:
            print(f"  WARNING: duplicate id {n['id']} ({p}) - skipping")
            continue
        seen.add(n["id"])
        nodes.append(n)
        print(f"  + {n['id']:<12} {n['country']} ({n['code']})  host={n['host']}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes}, f, indent=2, ensure_ascii=False)
    print(f"\nDone: {len(nodes)} nodes -> {args.out}")
    print("Put this file next to backend/server.py (as nodes.json) and restart the backend -")
    print("nodes will go into the signed manifest, and the Symbiont client will see them.")


if __name__ == "__main__":
    main()
