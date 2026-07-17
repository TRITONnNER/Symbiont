# backend/selftest_flags.py — фиче-флаги: дефолты, форма, override через flags.json.
import os, tempfile, sys, json
os.chdir(tempfile.mkdtemp())                       # рабочая папка — временная (сюда пишем flags.json)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
import server
c = TestClient(server.app)

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1

# 1) дефолты отдаются и имеют ожидаемую форму
f = c.get("/v1/config/flags")
check(f.status_code == 200, "GET /v1/config/flags → 200")
d = f.json()
check(d.get("version") == 1, "версия схемы = 1")
for sect in ("download", "coming_soon", "site", "legal", "pricing", "app"):
    check(sect in d, f"секция '{sect}' присутствует")

# 2) пример из задачи: все платформы по умолчанию включены
check(all(d["download"][p] for p in ("ios", "android", "windows", "macos", "linux", "extension")),
      "по умолчанию все платформы «Скачать» включены")

# 3) канарейка по умолчанию выключена (нет реального процесса — не показываем фейк)
check(d["legal"]["canary"] is False, "canary по умолчанию выключена")

# 4) override через flags.json подхватывается (гасим macOS — как в примере владельца)
override = json.loads(json.dumps(server.DEFAULT_FLAGS))   # глубокая копия дефолтов
override["download"]["macos"] = False
override["coming_soon"]["cli"] = False
with open("flags.json", "w", encoding="utf-8") as fh:
    json.dump(override, fh)
d2 = c.get("/v1/config/flags").json()
check(d2["download"]["macos"] is False, "override: macOS скрыт")
check(d2["download"]["windows"] is True, "override: остальные платформы не тронуты")
check(d2["coming_soon"]["cli"] is False, "override: пункт «Скоро/CLI» скрыт")

# 5) битый flags.json → тихий откат на дефолты (не 500)
with open("flags.json", "w", encoding="utf-8") as fh:
    fh.write("{ это не json ")
d3 = c.get("/v1/config/flags")
check(d3.status_code == 200 and d3.json()["download"]["macos"] is True,
      "битый flags.json → откат на дефолты, без 500")

os.remove("flags.json")
print(f"\n[flags] {ok}/{ok} проверок зелёные")
