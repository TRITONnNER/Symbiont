# backend/selftest_panel.py — панель Server-in-a-Box:
#   • RBAC: владелец (admin-токен, все права) + работник (staff-токен, scopes);
#   • поддержка по-настоящему: пользователь пишет → ждёт ОПЕРАТОРА (без фейк-бота),
#     оператор отвечает из панели, ответ виден пользователю;
#   • флаги: панель включает/выключает блоки (пишет flags.json, версия растёт);
#   • БД: /v1/admin/db/stats (реальная SQLite/WAL) и онлайн-бэкап.
import os, tempfile, sys
os.chdir(tempfile.mkdtemp())
os.environ["SYMBIONT_ADMIN_TOKEN"] = "owner-secret-xyz"    # известный admin-токен ДО импорта
os.environ["SYMBIONT_PAY_PROVIDER"] = "mock"               # тест-платежи (pending + ручное подтверждение)
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

# ── 8. Пользователи: список, карточка, баланс, устройство, фрод ───────────────
ureg = c.post("/v1/account/register", json={"aliases":[{"value":"cust1","kind":"nick"}],
              "device":{"name":"iPhone","platform":"ios"}}).json()
ctok, caid, cdid = ureg["token"], ureg["account_id"], ureg["device_id"]
ul = c.get("/v1/admin/users", headers=OWNER).json()
check(ul["total"] >= 1 and any(u["account_id"]==caid for u in ul["users"]), "список пользователей содержит аккаунт")
ud = c.get("/v1/admin/user/"+caid, headers=OWNER).json()
check(ud["account_id"]==caid and ud["summary"]["tier"]=="free", "карточка пользователя: free")
check(len(ud["devices"])==1 and ud["devices"][0]["platform"]=="ios", "в карточке видно устройство")
# начислить 120 минут (комп)
ab = c.post("/v1/admin/user/"+caid+"/balance", json={"minutes":120,"reason":"comp"}, headers=OWNER).json()
check(ab["ok"] and ab["active_minutes_left"]==120, "оператор начислил 120 минут")
check(c.get("/v1/billing/status", headers={"Authorization":f"Bearer {ctok}"}).json()["subscription"]["active_minutes_left"]==120,
      "пользователь видит начисленные минуты")
# списать 30
ab2 = c.post("/v1/admin/user/"+caid+"/balance", json={"minutes":-30}, headers=OWNER).json()
check(ab2["active_minutes_left"]==90, "оператор списал 30 минут")
# грант тира
gr = c.post("/v1/admin/grant", json={"account_id":caid,"tier":"ultimate","days":30}, headers=OWNER).json()
check(gr["ok"] and gr["subscription"]["tier"]=="ultimate", "грант Ultimate на 30 дней")
# отзыв устройства
dv = c.post(f"/v1/admin/user/{caid}/device/{cdid}/revoke", headers=OWNER).json()
check(dv["ok"], "оператор отозвал устройство")
check(c.get("/v1/account/devices", headers={"Authorization":f"Bearer {ctok}"}).status_code==401,
      "сессия отозванного устройства → 401")
# фрод и снятие
fr = c.post("/v1/admin/fraud", json={"account_id":caid}, headers=OWNER).json()
check(fr["ok"] and caid in fr["flagged"], "аккаунт помечен фродом")
check(c.get("/v1/admin/user/"+caid, headers=OWNER).json()["summary"]["fraud"] is True, "фрод виден в карточке")
uf = c.post("/v1/admin/unfraud", json={"account_id":caid}, headers=OWNER).json()
check(uf["ok"] and uf["fraud"] is False, "фрод снят")

# ── 9. Платежи: список, сводка, подтверждение, возврат (mock) ─────────────────
breg = c.post("/v1/account/register", json={"aliases":[{"value":"payer1","kind":"nick"}]}).json()
btok, baid = breg["token"], breg["account_id"]
pur = c.post("/v1/billing/purchase", json={"product":"balance_100h","method":"sbp","region":"ru"},
             headers={"Authorization":f"Bearer {btok}"}).json()
check(pur["status"]=="pending", "покупка (mock) → pending")
pid = pur["payment_id"]
pl = c.get("/v1/admin/payments", headers=OWNER).json()
check(any(x["payment_id"]==pid and x["status"]=="pending" for x in pl["payments"]), "платёж виден в списке (pending)")
conf = c.post(f"/v1/admin/payment/{pid}/confirm", headers=OWNER).json()
check(conf["ok"] and conf["status"]=="completed", "оператор подтвердил платёж вручную")
check(c.get("/v1/billing/status", headers={"Authorization":f"Bearer {btok}"}).json()["subscription"]["active_minutes_left"]==6000,
      "после подтверждения начислено 100 ч")
summ = c.get("/v1/admin/billing/summary", headers=OWNER).json()
check(summ["revenue"].get("RUB",0) >= 149, "сводка: выручка учитывает завершённый платёж")
check(summ["by_product"].get("balance_100h",0) >= 1, "сводка: разбивка по продуктам")
ref = c.post(f"/v1/admin/payment/{pid}/refund", headers=OWNER).json()
check(ref["ok"] and ref["status"]=="refunded", "возврат платежа")
check(c.get("/v1/billing/status", headers={"Authorization":f"Bearer {btok}"}).json()["subscription"]["active_minutes_left"]==0,
      "после возврата минуты сняты")

# ── 10. Ключи: выпуск, список, отзыв ─────────────────────────────────────────
iss = c.post("/v1/admin/issue", json={"plan":"premium","grant_days":30,"uses":1}, headers=OWNER).json()
check("code" in iss and "." in iss["code"], "ключ выпущен (код разовый)")
kl = c.get("/v1/admin/keys", headers=OWNER).json()
check(kl["total"]>=1, "выпущенные ключи видны в реестре")
kid = kl["keys"][0]["kid"]
kr = c.post(f"/v1/admin/key/{kid}/revoke", headers=OWNER).json()
check(kr["ok"] and kr["revoked"], "ключ отозван")

# ── 11. Экономика: чтение и изменение цены ────────────────────────────────────
eco = c.get("/v1/admin/economy", headers=OWNER).json()
old = eco["prices"]["ru"]["premium"]["month"]
es = c.post("/v1/admin/economy", json={"economy":{"prices":{"ru":{"premium":{"month":249}}}}}, headers=OWNER).json()
check(es["ok"] and es["economy"]["prices"]["ru"]["premium"]["month"]==249, "цена изменена из панели")
check(es["economy"]["prices"]["ru"]["premium"]["year"]==eco["prices"]["ru"]["premium"]["year"], "остальные цены не тронуты (merge)")
check(c.get("/v1/config/economy").json()["prices"]["ru"]["premium"]["month"]==249, "клиент видит новую цену")

# ── 12. Поддержка: шаблоны, закрытие/переоткрытие ─────────────────────────────
tpl = c.get("/v1/admin/support/templates", headers=OWNER).json()
check(len(tpl["templates"])>=3, "есть быстрые шаблоны ответов")
cl = c.post(f"/v1/admin/support/{aid}/status", json={"status":"closed"}, headers=OWNER).json()
check(cl["status"]=="closed", "обращение закрыто")
th = c.get("/v1/admin/support/thread/"+aid, headers=OWNER).json()
check(th["status"]=="closed" and th["account_id"]==aid, "статус closed + привязка к аккаунту")
op = c.post(f"/v1/admin/support/{aid}/status", json={"status":"open"}, headers=OWNER).json()
check(op["status"]=="open", "обращение переоткрыто")

# ── 13. Работники: правка прав + enforcement новых scopes ─────────────────────
mk2 = c.post("/v1/admin/staff", json={"name":"Биллинг Боб","scopes":["billing"]}, headers=OWNER).json()
B = {"X-Staff-Token": mk2["token"]}
check(c.get("/v1/admin/payments", headers=B).status_code==200, "работник billing видит платежи")
check(c.get("/v1/admin/users", headers=B).status_code==403, "работник billing НЕ видит пользователей")
ed = c.patch("/v1/admin/staff/"+mk2["id"], json={"scopes":["billing","users"]}, headers=OWNER).json()
check(ed["ok"], "владелец расширил права работника")
check(c.get("/v1/admin/users", headers=B).status_code==200, "после правки работник видит пользователей")

# ── 14. Аудит: действия оператора записаны (только владелец) ───────────────────
au = c.get("/v1/admin/audit", headers=OWNER).json()
acts = {e["action"] for e in au["entries"]}
check({"grant","balance_adjust","payment_confirm","payment_refund","economy_set","key_issue"} <= acts,
      "журнал аудита содержит ключевые действия")
check(all(e.get("by") for e in au["entries"]), "у каждой записи аудита есть исполнитель (by)")
check(c.get("/v1/admin/audit", headers=B).status_code==403, "аудит — только владелец (работник → 403)")

print(f"\n=== selftest_panel: {ok}/{ok} OK ===")
