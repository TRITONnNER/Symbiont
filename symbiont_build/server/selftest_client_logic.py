#!/usr/bin/env python3
"""
selftest_client_logic.py — проверка чистой клиентской логики (порт из Dart):
  1) classifyCurtain — классификатор «16 КБ занавеса» (все ветки);
  2) curtainNeedsRelay — когда уводить на relay;
  3) разделение узлов/relay из манифеста (как api_client: relay в список UI не идёт).

Запуск:  python3 server/selftest_client_logic.py   (код 0 = всё ок)
"""
from __future__ import annotations
import sys

FAILS = 0
def ok(name, cond):
    global FAILS
    print(("  [OK] " if cond else "  [FAIL] ") + name)
    if not cond: FAILS += 1

# ── порт lib/net/curtain.dart ────────────────────────────────────────────────
LO, HI = 12 * 1024, 24 * 1024
def classify_curtain(received, target, stalled):
    if received >= target: return "clear"
    if not stalled: return "partial"
    if received < LO: return "blocked"
    if received <= HI: return "curtain"
    return "slow"
def needs_relay(v): return v in ("curtain", "slow")

# ── порт разделения узлов из api_client.fetchManifest ────────────────────────
def split_nodes(nodes):
    is_relay = lambda n: "relay" in (n.get("roles") or [])
    return [n for n in nodes if not is_relay(n)], [n for n in nodes if is_relay(n)]

def main():
    T = 64 * 1024
    # 1) классификатор — все ветки
    ok("полный объём → clear", classify_curtain(T, T, False) == "clear")
    ok("~16 КБ и замерло → curtain", classify_curtain(16 * 1024, T, True) == "curtain")
    ok("15 КБ и замерло → curtain", classify_curtain(15 * 1024, T, True) == "curtain")
    ok("умерло до 12 КБ → blocked", classify_curtain(5 * 1024, T, True) == "blocked")
    ok(">24 КБ и тормозит → slow", classify_curtain(40 * 1024, T, True) == "slow")
    ok("закрылось чисто, но меньше → partial", classify_curtain(10 * 1024, T, False) == "partial")
    ok("граница 24 КБ включительно → curtain", classify_curtain(24 * 1024, T, True) == "curtain")
    ok("чуть выше 24 КБ → slow", classify_curtain(24 * 1024 + 1, T, True) == "slow")

    # 2) needs_relay
    ok("curtain → нужен relay", needs_relay("curtain"))
    ok("slow → нужен relay", needs_relay("slow"))
    ok("clear → relay не нужен", not needs_relay("clear"))
    ok("blocked → relay не нужен (другой блок)", not needs_relay("blocked"))

    # 3) разделение узлов
    nodes = [
        {"id": "nl-01", "roles": ["edge"]},
        {"id": "fi-01", "roles": ["edge", "backbone"]},
        {"id": "ru-relay-01", "roles": ["relay"]},
    ]
    ui, relays = split_nodes(nodes)
    ok("relay НЕ попадает в список UI", all(n["id"] != "ru-relay-01" for n in ui) and len(ui) == 2)
    ok("relay уходит в relays (для detour)", len(relays) == 1 and relays[0]["id"] == "ru-relay-01")

    print()
    print("РЕЗУЛЬТАТ: клиентская логика (занавес + relay) верна ✔" if FAILS == 0 else f"РЕЗУЛЬТАТ: провалов {FAILS} ✗")
    return 0 if FAILS == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
