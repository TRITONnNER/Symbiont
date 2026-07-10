# backend/selftest_persist_sqlite.py — состояние живёт в РЕАЛЬНОЙ SQLite-БД,
# переживает «рестарт», и старые json-файлы бесшовно импортируются.
import os, tempfile, sys, sqlite3
os.chdir(tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import persist

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1

# 1) jsave → появляется РЕАЛЬНЫЙ файл БД
persist.jsave("accounts.json", {"tok1": {"plan": "pro"}})
check(os.path.exists("symbiont_state.db"), "создан реальный файл БД symbiont_state.db")

# 2) читаем обратно + exists()
check(persist.jload("accounts.json", {}) == {"tok1": {"plan": "pro"}}, "jload читает сохранённое")
check(persist.exists("accounts.json") and not persist.exists("nope.json"), "exists() корректен")

# 3) «рестарт»: сбрасываем кэш соединений → данные читаются из файла БД заново
persist._conns.clear()
check(persist.jload("accounts.json", {}) == {"tok1": {"plan": "pro"}},
      "состояние переживает рестарт (из файла БД)")

# 4) это действительно SQLite: открываем файл напрямую и видим строку в таблице kv
con = sqlite3.connect("symbiont_state.db")
row = con.execute("SELECT v FROM kv WHERE k='accounts.json'").fetchone()
con.close()
check(row is not None and "tok1" in row[0], "данные лежат в таблице kv настоящей SQLite-БД")

# 5) бесшовная миграция: старый json-файл импортируется в БД
open("legacy.json", "w", encoding="utf-8").write('{"old": true}')
check(persist.jload("legacy.json", {}) == {"old": True}, "legacy json-файл импортирован в БД")

# 6) UPSERT обновляет значение
persist.jsave("accounts.json", {"tok1": {"plan": "ultimate"}})
persist._conns.clear()
check(persist.jload("accounts.json", {})["tok1"]["plan"] == "ultimate", "UPSERT обновляет значение")

print(f"\n=== ИТОГ: {ok} OK, 0 FAIL ===")
