# backend/selftest_panel.py — панель Server-in-a-Box:
#   • RBAC: владелец (admin-токен, все права) + работник (staff-токен, scopes);
#   • поддержка по-настоящему: пользователь пишет → ждёт ОПЕРАТОРА (без фейк-бота),
#     оператор отвечает из панели, ответ виден пользователю;
#   • флаги: панель включает/выключает блоки (пишет flags.json, версия растёт);
#   • БД: /v1/admin/db/stats (реальная SQLite/WAL) и онлайн-бэкап.
import os, tempfile, sys
os.chdir(tempfile.mkdtemp())
os.environ["SYMBIONT_ADMIN_TOKEN"] = "owner-secret-xyz"    # известный admin-токен ДО импорта
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
import server
c = TestClient(server.app)

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1
def bearer(t): return {"Authorization": f"Bearer {t}"}
OWNER = {"X-Admin-Token": "owner-secret-xyz"}

# ── 1. whoami: владелец видит все права ───────────────────────────────────────
w = c.get("/v1/admin/whoami", headers=OWNER).json()
check(w["role"] == "owner" and set(w["scopes"]) == set(server.ALL_SCOPES), "whoami: владелец = все права")
check(c.get("/v1/admin/whoami").status_code == 403, "whoami без токена → 403")

# ── 2. Владелец создаёт РАБОТНИКА только со scope «support» ────────────────────
mk = c.post("/v1/admin/staff", json={"name": "Оператор Аня", "scopes": ["support"]}, headers=OWNER).json()
check(mk["ok"] and mk["token"].startswith("wrk_") and mk["scopes"] == ["support"], "создан работник (scope support)")
WRK = {"X-Staff-Token": mk["token"]}
staff_id = mk["id"]

ww = c.get("/v1/admin/whoami", headers=WRK).json()
check(ww["role"] == "worker" and ww["scopes"] == ["support"], "whoami: работник видит свой scope")

# создание работника — только владелец
check(c.post("/v1/admin/staff", json={"name": "x", "scopes": ["nodes"]}, headers=WRK).status_code == 403,
      "работник НЕ может создавать работников")
check(c.post("/v1/admin/staff", json={"name": "no", "scopes": ["bogus"]}, headers=OWNER).status_code == 422,
      "невалидные scopes → 422")

# ── 3. RBAC на разделах: работник-поддержка не лезет в узлы/скидки ─────────────
check(c.get("/v1/admin/support/threads", headers=WRK).status_code == 200, "работник видит поддержку (scope есть)")
check(c.get("/v1/admin/nodes", headers=WRK).status_code == 403, "работник НЕ видит узлы (scope нет)")
check(c.get("/v1/admin/discounts", headers=WRK).status_code == 403, "работник НЕ видит скидки (scope нет)")
check(c.get("/v1/admin/nodes", headers=OWNER).status_code == 200, "владелец видит узлы")

# ── 4. Поддержка по-настоящему: без фейкового авто-ответа ──────────────────────
reg = c.post("/v1/account/register", json={"aliases": [{"value": "user-sup", "kind": "nick"}]}).json()
utok, aid = reg["token"], reg["account_id"]
pm = c.post("/v1/support/message", json={"text": "Не подключается в Иране"}, headers=bearer(utok)).json()
check(pm["ok"] and pm["awaiting_operator"] is True, "сообщение принято и ждёт оператора")
thread = c.get("/v1/support/thread", headers=bearer(utok)).json()["messages"]
check(len(thread) == 1 and thread[0]["from"] == "user", "нет фейк-ответа: в треде только сообщение пользователя")

# тред виден оператору как «ждёт ответа», ключ = account_id (переписка по аккаунту)
lst = c.get("/v1/admin/support/threads", headers=WRK).json()
check(lst["awaiting"] >= 1 and any(t["id"] == aid and t["awaiting"] for t in lst["threads"]),
      "оператор видит обращение как ждущее (ключ = account_id)")

# оператор отвечает из панели
rep = c.post(f"/v1/admin/support/{aid}/reply", json={"text": "Импортируйте свой мост — раздел «Скачать»"},
             headers=WRK).json()
check(rep["ok"], "оператор ответил")
t2 = c.get("/v1/support/thread", headers=bearer(utok)).json()["messages"]
check(len(t2) == 2 and t2[1]["from"] == "support" and "мост" in t2[1]["text"], "пользователь видит ответ оператора")
lst2 = c.get("/v1/admin/support/threads", headers=OWNER).json()
check(not any(t["id"] == aid and t["awaiting"] for t in lst2["threads"]), "после ответа тред больше не «ждёт»")
check(c.post(f"/v1/admin/support/НЕТ/reply", json={"text": "x"}, headers=WRK).status_code == 404,
      "ответ в несуществующий тред → 404")

# ── 5. Флаги: панель гасит платформу, /v1/config/flags это отражает ────────────
before = c.get("/v1/config/flags").json()
v0 = before.get("version", 1)
sf = c.post("/v1/admin/flags", json={"flags": {"download": {"macos": False}}}, headers=OWNER).json()
check(sf["ok"] and sf["flags"]["download"]["macos"] is False, "панель выключила macOS")
check(sf["flags"]["download"]["windows"] is True, "остальные платформы не тронуты (частичный merge)")
after = c.get("/v1/config/flags").json()
check(after["download"]["macos"] is False and after["version"] == v0 + 1, "клиент видит выключенный флаг, версия +1")
check(c.post("/v1/admin/flags", json={"flags": {"download": {"macos": True}}}, headers=WRK).status_code == 403,
      "работник-поддержка НЕ может менять флаги")

# ── 6. РЕАЛЬНАЯ база: stats + онлайн-бэкап ─────────────────────────────────────
st = c.get("/v1/admin/db/stats", headers=OWNER).json()
check(st["journal_mode"].lower() == "wal", "БД в режиме WAL (реальная база)")
check(st["keys"] > 0 and st["size_bytes"] > 0, "в БД есть ключи и ненулевой размер")
check("accounts.json" in st["bytes_by_key"] or "idaccounts.json" in st["bytes_by_key"], "видны ключи стейта")
bk = c.post("/v1/admin/db/backup", headers=OWNER).json()
check(bk["ok"] and os.path.exists(bk["path"]) and bk["size_bytes"] > 0, "онлайн-бэкап создан, файл на диске")

# ── 7. Отзыв работника: токен сразу перестаёт действовать ─────────────────────
rv = c.post(f"/v1/admin/staff/{staff_id}/revoke", headers=OWNER).json()
check(rv["ok"] and rv["revoked"], "работник отозван")
check(c.get("/v1/admin/support/threads", headers=WRK).status_code == 403, "отозванный работник → 403")
sl = c.get("/v1/admin/staff", headers=OWNER).json()
check(any(s["id"] == staff_id and s["revoked"] for s in sl["staff"]), "работник числится отозванным в списке")
check("token" not in str(sl), "список работников не раскрывает токены")

print(f"\n=== selftest_panel: {ok}/{ok} OK ===")
