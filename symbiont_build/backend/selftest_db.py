"""
selftest_db.py — тесты реляционного слоя db.py.

Главное, что проверяем — что гарантии корректности держит САМА БД (констрейнты +
транзакции), а не внутрипроцессный лок. Поэтому конкурентные тесты гоняют по
несколько соединений к одному файлу.

Запуск:  python3 selftest_db.py   (печатает [OK]/[FAIL], код возврата != 0 при провале)
"""
from __future__ import annotations
import os, tempfile, threading, time, sys
import db as dbm

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[OK] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}")


def fresh() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # пусть db создаст с нуля
    return path


def cleanup(path: str):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


# ── 1. Аккаунты ───────────────────────────────────────────────────────────────
def test_accounts():
    p = fresh()
    d = dbm.Database(p)
    d.create_account("acc1", tier="free", invite_code="SYM-AAA111")
    check("account: создан", d.account_exists("acc1"))
    check("account: get вернул поля", d.get_account("acc1")["tier"] == "free")
    dup = False
    try:
        d.create_account("acc1")
    except Exception:
        dup = True
    check("account: дубль PK отклонён", dup)
    check("account: invite_code UNIQUE виден", d.get_account("acc1")["invite_code"] == "SYM-AAA111")
    d.set_tier("acc1", "premium")
    check("account: set_tier", d.get_account("acc1")["tier"] == "premium")
    d.close(); cleanup(p)


# ── 2. Алиасы (всё-или-ничего; гонку решает PRIMARY KEY) ─────────────────────
def test_aliases():
    p = fresh()
    d = dbm.Database(p)
    d.create_account("a"); d.create_account("b")
    d.claim_aliases("a", ["h1", "h2"])
    check("alias: заявлены", d.alias_owner("h1") == "a" and d.alias_owner("h2") == "a")
    taken = False
    try:
        d.claim_aliases("b", ["h1"])      # h1 уже у a
    except dbm.AliasTaken:
        taken = True
    check("alias: занятый отклонён", taken)
    # всё-или-ничего: [h3 новый, h2 занятый] → h3 НЕ должен остаться
    rolled = False
    try:
        d.claim_aliases("b", ["h3", "h2"])
    except dbm.AliasTaken:
        rolled = True
    check("alias: занятый в пачке → AliasTaken", rolled)
    check("alias: откат пачки (h3 не записан)", d.alias_owner("h3") is None)
    d.close(); cleanup(p)


# ── 3. Устройства: owner-на-первом, промоут, отзыв, инвариант ≤1 owner ────────
def test_devices():
    p = fresh()
    d = dbm.Database(p)
    d.create_account("acc")
    d.register_device("acc", "dev1")
    d.register_device("acc", "dev2")
    devs = {x["device_id"]: x for x in d.list_devices("acc")}
    check("device: первое = owner", devs["dev1"]["role"] == "owner")
    check("device: второе = normal", devs["dev2"]["role"] == "normal")
    check("device: is_owner_device", d.is_owner_device("dev1") and not d.is_owner_device("dev2"))
    # промоут dev2 → owner; dev1 должен стать normal (инвариант ≤1 owner)
    d.promote_device("acc", "dev2")
    devs = {x["device_id"]: x for x in d.list_devices("acc")}
    check("device: промоут сменил owner", devs["dev2"]["role"] == "owner" and devs["dev1"]["role"] == "normal")
    owners = [x for x in d.list_devices("acc") if x["role"] == "owner" and not x["revoked"]]
    check("device: ровно один owner", len(owners) == 1)
    # прямая попытка вписать второго owner'а — частичный уникальный индекс не даст
    raw_ok = False
    try:
        c = d._begin()
        c.execute("UPDATE devices SET role='owner' WHERE device_id='dev1'")
        c.commit()
        raw_ok = True  # не должно дойти сюда
    except Exception:
        try: c.rollback()
        except Exception: pass
    check("device: частичный uniq не даёт 2 owner", not raw_ok)
    d.revoke_device("acc", "dev2")
    check("device: отзыв", d.list_devices("acc") and
          [x for x in d.list_devices("acc") if x["device_id"] == "dev2"][0]["revoked"] == 1)
    d.close(); cleanup(p)


# ── 4. Баланс + журнал ───────────────────────────────────────────────────────
def test_balance_ledger():
    p = fresh()
    d = dbm.Database(p)
    d.create_account("acc", tier="free")
    d.apply_grant("acc", days=30, kind="grant", reason="t", tier="premium")
    check("balance: дни начислены", d.balance("acc")["days_left"] == 30)
    check("balance: tier поднят free→premium", d.balance("acc")["tier"] == "premium")
    d.apply_grant("acc", minutes=600, kind="purchase", reason="topup")
    check("balance: минуты начислены", d.balance("acc")["active_minutes_left"] == 600)
    left = d.spend_minutes("acc", 100)
    check("balance: списание минут", left == 500)
    left = d.spend_minutes("acc", 9999)   # не уходит ниже нуля
    check("balance: не уходит < 0", left == 0)
    # сверка кэша с журналом
    s = d.ledger_sum("acc")
    bal = d.balance("acc")
    check("ledger: сумма дней == кэш", s["days"] == bal["days_left"])
    check("ledger: сумма минут == кэш", s["minutes"] == bal["active_minutes_left"])
    d.close(); cleanup(p)


# ── 5. Ключи: погашение, идемпотентность, коды ошибок ────────────────────────
def test_keys_basic():
    p = fresh()
    d = dbm.Database(p)
    for a in ("u1", "u2", "u3"):
        d.create_account(a)
    future = int(time.time()) + 86400
    d.issue_key("K1", plan="pro", grant_days=30, uses=1, expires_at=future)
    r = d.redeem_key("K1", "u1")
    check("key: первое погашение ок", r["grant_days"] == 30 and r["idempotent"] is False)
    r2 = d.redeem_key("K1", "u1")     # тот же аккаунт → идемпотентно, без списания
    check("key: повтор тем же → idempotent", r2["idempotent"] is True)
    check("key: uses_left не ушёл в минус", d.key_state("K1")["uses_left"] == 0)
    # другой аккаунт на одноразовом → already_redeemed
    err = None
    try:
        d.redeem_key("K1", "u2")
    except dbm.RedeemError as e:
        err = str(e)
    check("key: чужой на одноразовом → already_redeemed", err == "key_already_redeemed")
    # неизвестный / отозванный / просроченный
    err = None
    try: d.redeem_key("NOPE", "u1")
    except dbm.RedeemError as e: err = str(e)
    check("key: неизвестный → key_invalid", err == "key_invalid")
    d.issue_key("K2", plan="pro", grant_days=7, uses=1, expires_at=future)
    d.revoke_key("K2")
    err = None
    try: d.redeem_key("K2", "u1")
    except dbm.RedeemError as e: err = str(e)
    check("key: отозванный → key_revoked", err == "key_revoked")
    d.issue_key("K3", plan="pro", grant_days=7, uses=1, expires_at=int(time.time()) - 10)
    err = None
    try: d.redeem_key("K3", "u1")
    except dbm.RedeemError as e: err = str(e)
    check("key: просроченный → key_expired", err == "key_expired")
    d.close(); cleanup(p)


def test_keys_multiuse():
    p = fresh()
    d = dbm.Database(p)
    for a in ("m1", "m2", "m3", "m4"):
        d.create_account(a)
    future = int(time.time()) + 86400
    d.issue_key("MULTI", plan="pro", grant_days=10, uses=3, expires_at=future)
    ok = sum(1 for a in ("m1", "m2", "m3") if d.redeem_key("MULTI", a)["idempotent"] is False)
    check("key(multi): 3 разных погасили", ok == 3)
    err = None
    try: d.redeem_key("MULTI", "m4")
    except dbm.RedeemError as e: err = str(e)
    check("key(multi): 4-й исчерпал → already_redeemed", err == "key_already_redeemed")
    d.close(); cleanup(p)


# ── 6. КОНКУРЕНТНОСТЬ: 1 одноразовый ключ, N потоков → ровно 1 победитель ────
def test_concurrent_redeem():
    p = fresh()
    setup = dbm.Database(p)
    N = 12
    for i in range(N):
        setup.create_account(f"c{i}")
    future = int(time.time()) + 86400
    setup.issue_key("RACE", plan="pro", grant_days=30, uses=1, expires_at=future)
    setup.close()

    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(N)

    def worker(i):
        d = dbm.Database(p)          # СВОЁ соединение к тому же файлу
        barrier.wait()               # стартуем максимально одновременно
        try:
            r = d.redeem_key("RACE", f"c{i}")
            outcome = ("ok", r["idempotent"])
        except dbm.RedeemError as e:
            outcome = ("err", str(e))
        except Exception as e:
            outcome = ("exc", repr(e))
        with lock:
            results.append(outcome)
        d.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()

    wins = [r for r in results if r[0] == "ok" and r[1] is False]
    redeemed = [r for r in results if r == ("err", "key_already_redeemed")]
    excs = [r for r in results if r[0] == "exc"]
    check("concurrent: ровно 1 победитель", len(wins) == 1, f"wins={len(wins)} results={results}")
    check("concurrent: остальные already_redeemed", len(redeemed) == N - 1, f"redeemed={len(redeemed)}")
    check("concurrent: без исключений БД", len(excs) == 0, f"excs={excs}")

    verify = dbm.Database(p)
    check("concurrent: uses_left == 0", verify.key_state("RACE")["uses_left"] == 0)
    check("concurrent: ровно 1 запись погашения",
          verify._conn().execute("SELECT COUNT(*) n FROM key_redemptions WHERE kid='RACE'").fetchone()["n"] == 1)
    verify.close(); cleanup(p)


# ── 7. Колесо: идемпотентность за сутки ──────────────────────────────────────
def test_wheel():
    p = fresh()
    d = dbm.Database(p)
    d.create_account("w")
    seg = {"kind": "days", "amount": 3}
    d.spin_wheel("w", "2026-06-29", 2, seg)
    check("wheel: спин начислил дни", d.balance("w")["days_left"] == 3)
    spun = False
    try:
        d.spin_wheel("w", "2026-06-29", 5, {"kind": "days", "amount": 99})
    except dbm.AlreadySpun:
        spun = True
    check("wheel: повтор за сутки отклонён", spun)
    check("wheel: баланс начислен один раз", d.balance("w")["days_left"] == 3)
    check("wheel: today вернул тот же сегмент", d.wheel_today("w", "2026-06-29")["index"] == 2)
    d.spin_wheel("w", "2026-06-30", 1, {"kind": "minutes", "amount": 60})
    check("wheel: новый день — можно", d.balance("w")["active_minutes_left"] == 60)
    d.close(); cleanup(p)


# ── 8. Конкурентное колесо: 8 потоков, один аккаунт, один день → 1 запись ────
def test_concurrent_wheel():
    p = fresh()
    s = dbm.Database(p); s.create_account("ww"); s.close()
    N = 8
    barrier = threading.Barrier(N)
    results = []
    lock = threading.Lock()

    def worker():
        d = dbm.Database(p)
        barrier.wait()
        try:
            d.spin_wheel("ww", "2026-07-01", 0, {"kind": "days", "amount": 1})
            out = "ok"
        except dbm.AlreadySpun:
            out = "spun"
        except Exception as e:
            out = f"exc:{e!r}"
        with lock: results.append(out)
        d.close()

    ts = [threading.Thread(target=worker) for _ in range(N)]
    for t in ts: t.start()
    for t in ts: t.join()
    check("wheel(conc): ровно 1 спин прошёл", results.count("ok") == 1, str(results))
    v = dbm.Database(p)
    check("wheel(conc): баланс начислен один раз", v.balance("ww")["days_left"] == 1)
    v.close(); cleanup(p)


# ── 9. Рефералы: ребро invited_by + окно выплат из журнала ───────────────────
def test_referrals():
    p = fresh()
    d = dbm.Database(p)
    for a in ("inv", "r1", "r2"):
        d.create_account(a)
    check("ref: привязка пригласившего", d.set_inviter("r1", "inv") and d.set_inviter("r2", "inv"))
    check("ref: на себя нельзя", d.set_inviter("inv", "inv") is False)
    check("ref: повторная привязка нет", d.set_inviter("r1", "r2") is False)
    check("ref: referrals_of", sorted(d.referrals_of("inv")) == ["r1", "r2"])
    # выплаты в журнал → окно считает referral_out
    d.apply_grant("inv", days=14, kind="referral_out", reason="ref_premium_month", ref="r1")
    d.apply_grant("inv", days=14, kind="referral_out", reason="ref_premium_month", ref="r2")
    d.apply_grant("inv", days=30, kind="grant", reason="other")  # не считается
    check("ref: окно выплат = 2", d.payouts_in_window("inv") == 2)
    d.close(); cleanup(p)


# ── 10. Узлы: upsert + protocols/roles + сохранение секрета ──────────────────
def test_nodes():
    p = fresh()
    d = dbm.Database(p)
    node = {"id": "nl-01", "country": "Netherlands", "code": "NL", "host": "1.2.3.4",
            "transport": "reality", "protocols": ["reality", "hysteria2"], "roles": ["edge"],
            "loadPct": 18, "xrayCore": True, "provider": "x"}
    d.upsert_node(node, secret="node-secret-1")
    got = {n["id"]: n for n in d.list_nodes()}["nl-01"]
    check("node: записан с протоколами", sorted(got["protocols"]) == ["hysteria2", "reality"])
    check("node: роли", got["roles"] == ["edge"])
    check("node: xrayCore прокинут", got.get("xrayCore") is True)
    check("node: секрет доступен", d.get_node_secret("nl-01") == "node-secret-1")
    # повторный upsert без секрета: секрет НЕ теряется, протоколы переписаны без дублей
    node2 = dict(node); node2["protocols"] = ["reality"]; node2["loadPct"] = 50
    d.upsert_node(node2)
    got = {n["id"]: n for n in d.list_nodes()}["nl-01"]
    check("node: upsert переписал протоколы", got["protocols"] == ["reality"])
    check("node: upsert обновил loadPct", got["loadPct"] == 50)
    check("node: секрет пережил upsert", d.get_node_secret("nl-01") == "node-secret-1")
    check("node: нет дублей в junction",
          d._conn().execute("SELECT COUNT(*) n FROM node_protocols WHERE node_id='nl-01'").fetchone()["n"] == 1)
    d.close(); cleanup(p)


# ── 11. Поддержка + аудит + настройки ────────────────────────────────────────
def test_support_audit_settings():
    p = fresh()
    d = dbm.Database(p)
    d.add_message("tok", "support", "Здравствуйте")
    d.add_message("tok", "user", "Не работает")
    th = d.thread("tok")
    check("support: порядок сообщений", th[0]["from"] == "support" and th[1]["from"] == "user")
    # аудит пишется при issue_key
    d.create_account("acc")
    d.issue_key("AK", plan="pro", grant_days=30, uses=1, expires_at=int(time.time()) + 86400)
    aud = d.recent_audit()
    check("audit: issue_key записан", any(a["action"] == "issue_key" and a["target"] == "AK" for a in aud))
    d.set_setting("rollout", 25)
    check("settings: get/set", d.get_setting("rollout") == 25)
    check("settings: дефолт", d.get_setting("nope", "x") == "x")
    d.close(); cleanup(p)


# ── 12. Долговечность: переоткрытие файла ────────────────────────────────────
def test_durability():
    p = fresh()
    d = dbm.Database(p)
    d.create_account("acc", tier="free")
    d.apply_grant("acc", days=30, kind="grant", tier="premium")
    d.issue_key("DK", plan="pro", grant_days=5, uses=1, expires_at=int(time.time()) + 86400)
    d.redeem_key("DK", "tok")
    d.close()
    # новый объект на том же файле — данные на месте
    d2 = dbm.Database(p)
    check("durable: аккаунт пережил рестарт", d2.account_exists("acc"))
    check("durable: баланс пережил рестарт", d2.balance("acc")["days_left"] == 30)
    check("durable: погашение пережило рестарт", d2.key_state("DK")["uses_left"] == 0)
    s = d2.ledger_sum("acc")
    check("durable: журнал сходится с кэшем", s["days"] == d2.balance("acc")["days_left"])
    st = d2.stats()
    check("durable: stats считает", st["accounts"] == 1 and st["keys"] == 1)
    d2.close(); cleanup(p)


def main():
    test_accounts()
    test_aliases()
    test_devices()
    test_balance_ledger()
    test_keys_basic()
    test_keys_multiuse()
    test_concurrent_redeem()
    test_wheel()
    test_concurrent_wheel()
    test_referrals()
    test_nodes()
    test_support_audit_settings()
    test_durability()
    print(f"\n=== ИТОГ: {PASS} OK, {FAIL} FAIL ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
