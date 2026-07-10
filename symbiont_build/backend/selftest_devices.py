# backend/selftest_devices.py — устройства (owner-class/повышение/отзыв) + экономика.
import os, tempfile, sys
os.chdir(tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
import server
c = TestClient(server.app)

ok = 0
def check(cond, name):
    global ok
    assert cond, f"[FAIL] {name}"
    print(f"[OK] {name}"); ok += 1

def hdr(t): return {"Authorization": f"Bearer {t}"}

# Регистрация с устройством-1 → оно owner
r = c.post("/v1/account/register", json={
    "aliases": [{"value": "owner", "kind": "nick"}],
    "device": {"id": "dev-A", "name": "ПК Алисы", "platform": "windows"}}).json()
tokA = r["token"]; rec = r["recovery_code"]
check(r["device_id"] == "dev-A", "регистрация привязала устройство")

# Список устройств: одно, роль owner, current
d = c.get("/v1/account/devices", headers=hdr(tokA)).json()["devices"]
check(len(d) == 1 and d[0]["role"] == "owner" and d[0]["current"], "первое устройство = owner, текущее")

# Вход тем же аккаунтом со ВТОРОГО устройства (через recovery) → роль normal
r2 = c.post("/v1/account/recover", json={"recovery_code": rec,
            "device": {"id": "dev-B", "name": "Ноут", "platform": "linux"}}).json()
tokB = r2["token"]
d = c.get("/v1/account/devices", headers=hdr(tokA)).json()["devices"]
check(len(d) == 2, "в списке два устройства")
roleB = [x for x in d if x["id"] == "dev-B"][0]["role"]
check(roleB == "normal", "второе устройство = normal")

# normal-устройство НЕ может отзывать (403)
rr = c.post("/v1/account/devices/revoke", json={"device_id": "dev-A"}, headers=hdr(tokB))
check(rr.status_code == 403, "normal-устройство не может отзывать (403)")

# owner повышает dev-B до owner → теперь B может управлять
c.post("/v1/account/devices/promote", json={"device_id": "dev-B"}, headers=hdr(tokA))
d = c.get("/v1/account/devices", headers=hdr(tokA)).json()["devices"]
roleB = [x for x in d if x["id"] == "dev-B"][0]["role"]
check(roleB == "owner", "owner повысил устройство до owner-class")

# Теперь B (owner) отзывает третье устройство
c.post("/v1/account/recover", json={"recovery_code": rec,
       "device": {"id": "dev-C", "name": "Телефон", "platform": "android"}})
rr = c.post("/v1/account/devices/revoke", json={"device_id": "dev-C"}, headers=hdr(tokB))
check(rr.status_code == 200, "повышенное устройство смогло отозвать другое")

# Отозванное устройство: его токен больше не работает
rC = c.post("/v1/account/recover", json={"recovery_code": rec,
            "device": {"id": "dev-C", "name": "Телефон", "platform": "android"}})
# повторный вход переактивирует — проверим отзыв на свежей сессии иначе:
c.post("/v1/account/devices/revoke", json={"device_id": "dev-C"}, headers=hdr(tokA))
tokC = rC.json()["token"]
chk = c.get("/v1/account/devices", headers=hdr(tokC))
check(chk.status_code == 401, "токен отозванного устройства отклонён (401)")

# Чужое устройство не отозвать (404 — не в аккаунте)
rr = c.post("/v1/account/devices/revoke", json={"device_id": "dev-NOPE"}, headers=hdr(tokA))
check(rr.status_code == 404, "несуществующее устройство → 404")

# Экономика: эндпоинт отдаёт тарифы/цены/рефералов/репутацию
e = c.get("/v1/config/economy")
check(e.status_code == 200, "GET /v1/config/economy → 200")
ej = e.json()
check(len(ej["tiers"]) == 3 and ej["tiers"][0]["id"] == "free", "3 тарифа, первый free")
check(ej["prices"]["ru"]["premium"]["month"] == 199, "цена Premium РФ = 199")
check(ej["referral"]["premium_year"]["inviter"] == 90, "реферал за годовой Premium = +90 инвайтеру")
check(ej["reputation"][0]["level"] == "new", "репутация: первый уровень — новичок")
check("crypto" in ej["payments"]["ru"], "оплата РФ включает крипту")

print(f"\nВсего проверок: {ok}")
