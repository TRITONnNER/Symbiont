"""
selftest_automation.py — тесты автоматизации:
  • само-починка узлов (heartbeat → last_seen → свип выключает мёртвые/возвращает живые);
  • расширенные ключи-ваучеры (срок/минуты/тариф/батч/дружелюбный формат/активация);
  • чистая логика autotune (выбор лучшего SNI / протоколы / уникальный id / нагрузка).

Запуск:  python3 selftest_automation.py
"""
from __future__ import annotations
import os, tempfile, time, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import db as dbm
import vouchers
import node_autotune as nat

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"[OK] {name}")
    else:
        FAIL += 1; print(f"[FAIL] {name} {extra}")


def fresh():
    fd, p = tempfile.mkstemp(suffix=".db"); os.close(fd); os.remove(p); return p


def cleanup(p):
    for s in ("", "-wal", "-shm"):
        try: os.remove(p + s)
        except OSError: pass


def _set_last_seen(d, node_id, ts):
    c = d._begin()
    try:
        c.execute("UPDATE nodes SET last_seen=? WHERE node_id=?", (ts, node_id)); c.commit()
    except Exception:
        c.rollback(); raise


# ── САМО-ПОЧИНКА УЗЛОВ ────────────────────────────────────────────────────────
def test_self_healing():
    p = fresh(); d = dbm.Database(p)
    for i in (1, 2, 3):
        d.upsert_node({"id": f"n{i}", "country": "NL", "code": "NL",
                       "protocols": ["reality"], "roles": ["edge"]}, secret=f"s{i}")
    # все свежие → свип никого не трогает
    r = d.sweep_stale_nodes(max_age_sec=300)
    check("heal: свежие не выключены", r["disabled"] == [] and len(d.list_nodes()) == 3)
    # n2 «протух» (last_seen в прошлом) → свип выключит, выпадет из манифеста
    _set_last_seen(d, "n2", int(time.time()) - 1000)
    r = d.sweep_stale_nodes(max_age_sec=300)
    check("heal: протухший выключен", r["disabled"] == ["n2"])
    ids = {n["id"] for n in d.list_nodes()}  # enabled_only по умолчанию
    check("heal: мёртвый выпал из манифеста", ids == {"n1", "n3"})
    # n2 вернулся (heartbeat) → следующий свип вернёт его в строй
    d.node_heartbeat("n2", load_pct=12)
    r = d.sweep_stale_nodes(max_age_sec=300)
    check("heal: вернувшийся восстановлен", r["restored"] == ["n2"])
    check("heal: снова в манифесте", {n["id"] for n in d.list_nodes()} == {"n1", "n2", "n3"})
    check("heal: heartbeat обновил нагрузку",
          [n for n in d.list_nodes() if n["id"] == "n2"][0]["loadPct"] == 12)
    # ручное выключение оператором — свип НЕ воскрешает (health_disabled=0)
    d.set_node_enabled("n3", False)
    _set_last_seen(d, "n3", int(time.time()))  # свежий, но выключен вручную
    r = d.sweep_stale_nodes(max_age_sec=300)
    check("heal: ручное выключение свип не трогает", "n3" not in r["restored"])
    check("heal: n3 остался выключен", "n3" not in {n["id"] for n in d.list_nodes()})
    d.close(); cleanup(p)


# ── ВАУЧЕРЫ: выпуск (срок/минуты/тариф) + активация ──────────────────────────
def test_voucher_basic():
    p = fresh(); d = dbm.Database(p)
    sk = Ed25519PrivateKey.generate(); pk = sk.public_key()
    d.create_account("acc", tier="free")
    d.create_session("tok", account_id="acc")
    # ваучер на 30 дней premium
    v = vouchers.issue(d, sk, grant_days=30, tier="premium", label="НГ-2026")
    check("vouch: выпущен код", v["code"].startswith("SYMB-") and "." in v["code"])
    # preview без погашения
    pv = vouchers.preview(d, pk, v["code"])
    check("vouch: preview показал срок/тариф", pv["valid"] and pv["grant_days"] == 30 and pv["tier"] == "premium")
    # активация
    res = vouchers.redeem(d, pk, v["code"], account_token="tok", account_id="acc")
    check("vouch: активирован — 30 дней", res["applied"]["days"] == 30 and res["idempotent"] is False)
    check("vouch: тариф поднят", d.balance("acc")["tier"] == "premium")
    check("vouch: дни на балансе", d.balance("acc")["days_left"] == 30)
    # повтор тем же аккаунтом → идемпотентно, без второго начисления
    res2 = vouchers.redeem(d, pk, v["code"], account_token="tok", account_id="acc")
    check("vouch: повтор идемпотентен", res2["idempotent"] is True)
    check("vouch: дни не задвоились", d.balance("acc")["days_left"] == 30)
    d.close(); cleanup(p)


def test_voucher_minutes_and_format():
    p = fresh(); d = dbm.Database(p)
    sk = Ed25519PrivateKey.generate(); pk = sk.public_key()
    d.create_account("acc"); d.create_session("tok", account_id="acc")
    # ваучер на 600 минут активного баланса (без смены тарифа на period)
    v = vouchers.issue(d, sk, grant_days=0, grant_minutes=600, tier="premium")
    # активация дружелюбным кодом, специально «испорченным» вводом: нижний регистр, лишние пробелы
    typed = "  " + v["code"].lower().replace("SYMB-".lower(), "symb ") + "  "
    res = vouchers.redeem(d, pk, typed, account_token="tok", account_id="acc")
    check("vouch: минуты начислены", res["applied"]["minutes"] == 600)
    check("vouch: парсер прощает регистр/пробелы", d.balance("acc")["active_minutes_left"] == 600)
    d.close(); cleanup(p)


def test_voucher_batch():
    p = fresh(); d = dbm.Database(p)
    sk = Ed25519PrivateKey.generate(); pk = sk.public_key()
    # партия 5 одинаковых ваучеров одним вызовом
    b = vouchers.issue_batch(d, sk, count=5, grant_days=7, tier="premium", label="реселлер-X")
    check("batch: выпущено 5", b["count"] == 5 and len(b["codes"]) == 5)
    check("batch: общий batch_id", all("kid" in c for c in b["codes"]))
    summ = d.batch_summary(b["batch_id"])
    check("batch: сводка всего=5 активных=5", summ["total"] == 5 and summ["active"] == 5)
    # активируем 2 из партии разными аккаунтами
    for i in range(2):
        d.create_account(f"u{i}"); d.create_session(f"t{i}", account_id=f"u{i}")
        vouchers.redeem(d, pk, b["codes"][i]["code"], account_token=f"t{i}", account_id=f"u{i}")
    summ = d.batch_summary(b["batch_id"])
    check("batch: 2 погашено / 3 активны", summ["used"] == 2 and summ["active"] == 3)
    # отзыв всей партии — оставшиеся 3 больше не активируются
    revoked = d.revoke_batch(b["batch_id"])
    check("batch: отозвано оставшихся", revoked == 3)
    d.create_account("uz"); d.create_session("tz", account_id="uz")
    err = None
    try:
        vouchers.redeem(d, pk, b["codes"][4]["code"], account_token="tz", account_id="uz")
    except dbm.RedeemError as e:
        err = str(e)
    check("batch: отозванный не активируется", err == "key_revoked")
    d.close(); cleanup(p)


def test_voucher_multiuse_and_security():
    p = fresh(); d = dbm.Database(p)
    sk = Ed25519PrivateKey.generate(); pk = sk.public_key()
    # многоразовый промо-ваучер на 3 активации
    v = vouchers.issue(d, sk, grant_days=3, tier="premium", uses=3)
    ok = 0
    for i in range(3):
        d.create_account(f"m{i}"); d.create_session(f"mt{i}", account_id=f"m{i}")
        r = vouchers.redeem(d, pk, v["code"], account_token=f"mt{i}", account_id=f"m{i}")
        ok += 0 if r["idempotent"] else 1
    check("vouch(multi): 3 активации прошли", ok == 3)
    d.create_account("m4"); d.create_session("mt4", account_id="m4")
    err = None
    try:
        vouchers.redeem(d, pk, v["code"], account_token="mt4", account_id="m4")
    except dbm.RedeemError as e:
        err = str(e)
    check("vouch(multi): 4-й исчерпал", err == "key_already_redeemed")
    # подделка: бьём один символ подписи → key_invalid
    bad = v["code"][:-3] + ("AA" if v["code"][-2:] != "AA" else "BB")
    d.create_account("mx"); d.create_session("mtx", account_id="mx")
    err = None
    try:
        vouchers.redeem(d, pk, bad, account_token="mtx", account_id="mx")
    except dbm.RedeemError as e:
        err = str(e)
    check("vouch: подделка → key_invalid", err == "key_invalid")
    # чужой ключ подписи (другой issuer) → key_invalid
    other = Ed25519PrivateKey.generate()
    v2 = vouchers.issue(d, other, grant_days=5, tier="premium")
    d.create_account("my"); d.create_session("mty", account_id="my")
    err = None
    try:
        vouchers.redeem(d, pk, v2["code"], account_token="mty", account_id="my")
    except dbm.RedeemError as e:
        err = str(e)
    check("vouch: чужая подпись → key_invalid", err == "key_invalid")
    d.close(); cleanup(p)


def test_voucher_expiry():
    p = fresh(); d = dbm.Database(p)
    sk = Ed25519PrivateKey.generate(); pk = sk.public_key()
    d.create_account("acc"); d.create_session("tok", account_id="acc")
    v = vouchers.issue(d, sk, grant_days=5, tier="premium", ttl_days=-1)  # истёк (вчера)
    err = None
    try:
        vouchers.redeem(d, pk, v["code"], account_token="tok", account_id="acc")
    except dbm.RedeemError as e:
        err = str(e)
    check("vouch: просроченный → key_expired", err == "key_expired")
    d.close(); cleanup(p)


def test_voucher_check():
    """Проверка кода без активации (game-style checker): все статусы."""
    p = fresh(); d = dbm.Database(p)
    sk = Ed25519PrivateKey.generate(); pk = sk.public_key()
    # действительный одноразовый
    v = vouchers.issue(d, sk, grant_days=30, tier="premium")
    r = vouchers.check(d, pk, v["code"])
    check("check: valid", r["status"] == "valid" and r["ok"] is True
          and r["grants"]["days"] == 30 and r["grants"]["tier"] == "premium")
    # многоразовый показывает остаток активаций
    vm = vouchers.issue(d, sk, grant_days=7, tier="premium", uses=5)
    rm = vouchers.check(d, pk, vm["code"])
    check("check: multi показывает остаток", rm["status"] == "valid"
          and rm["uses_left"] == 5 and "5 из 5" in rm["message"])
    # уже использован
    d.create_account("acc"); d.create_session("tok", account_id="acc")
    vouchers.redeem(d, pk, v["code"], account_token="tok", account_id="acc")
    r = vouchers.check(d, pk, v["code"])
    check("check: already_redeemed", r["status"] == "already_redeemed" and r["ok"] is False)
    # отозванный
    vr = vouchers.issue(d, sk, grant_days=5, tier="premium")
    d.revoke_key(_kid_of(vr))
    r = vouchers.check(d, pk, vr["code"])
    check("check: revoked", r["status"] == "revoked")
    # просроченный
    ve = vouchers.issue(d, sk, grant_days=5, tier="premium", ttl_days=-1)
    r = vouchers.check(d, pk, ve["code"])
    check("check: expired", r["status"] == "expired")
    # подделка (битая подпись)
    bad = v["code"][:-3] + ("AA" if v["code"][-2:] != "AA" else "BB")
    r = vouchers.check(d, pk, bad)
    check("check: invalid (подделка)", r["status"] == "invalid" and r["valid"] is False)
    # подписан нами, но не в этой системе (удалим запись из реестра)
    vn = vouchers.issue(d, sk, grant_days=5, tier="premium")
    kid = _kid_of(vn)
    cc = d._begin()
    try: cc.execute("DELETE FROM keys WHERE kid=?", (kid,)); cc.commit()
    except Exception: cc.rollback(); raise
    r = vouchers.check(d, pk, vn["code"])
    check("check: not_found", r["status"] == "not_found" and r["registered"] is False)
    d.close(); cleanup(p)


def _kid_of(v):
    """kid ваучера из его кода (для тестов)."""
    body, _ = vouchers.parse_code(v["code"])
    import json as _j
    return _j.loads(body)["kid"]


# ── AUTOTUNE: чистая логика выбора ────────────────────────────────────────────
def test_autotune_logic():
    # лучший SNI = живой с минимальной задержкой
    res = [{"host": "a", "ok": True, "latency_ms": 80},
           {"host": "b", "ok": True, "latency_ms": 30},
           {"host": "c", "ok": False, "latency_ms": None}]
    check("autotune: лучший SNI по задержке", nat.pick_best_sni(res) == "b")
    check("autotune: нет живых → None",
          nat.pick_best_sni([{"host": "x", "ok": False, "latency_ms": None}]) is None)
    # протоколы по UDP
    check("autotune: UDP жив → полный каскад",
          nat.decide_protocols(True) == ["reality", "hysteria2", "ss2022"])
    check("autotune: UDP мёртв → только TCP",
          nat.decide_protocols(False) == ["reality", "ss2022"])
    # уникальный id: детерминирован, разный для разных host
    id1 = nat.unique_node_id("NL", "1.2.3.4")
    id1b = nat.unique_node_id("NL", "1.2.3.4")
    id2 = nat.unique_node_id("NL", "5.6.7.8")
    check("autotune: id детерминирован", id1 == id1b and id1.startswith("nl-"))
    check("autotune: id уникален по host", id1 != id2)
    # нагрузка из ёмкости: монотонно (быстрее → ниже), границы
    check("autotune: ёмкость→нагрузка монотонна",
          nat.load_from_capacity(1000) < nat.load_from_capacity(100) <= nat.load_from_capacity(10))
    check("autotune: нет данных → нейтрально", nat.load_from_capacity(None) == 30)


def main():
    test_self_healing()
    test_voucher_basic()
    test_voucher_minutes_and_format()
    test_voucher_batch()
    test_voucher_multiuse_and_security()
    test_voucher_expiry()
    test_voucher_check()
    test_autotune_logic()
    print(f"\n=== ИТОГ: {PASS} OK, {FAIL} FAIL ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
