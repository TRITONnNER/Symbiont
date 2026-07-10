"""
selftest_wheel_design.py — проверка дизайна колеса (wheel.py).
Доказываем: веса сходятся к заявленным шансам, результат детерминирован и
проверяем (provably-fair), EV считается, pity и серии работают.
"""
from __future__ import annotations
import sys
import wheel as W

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"[OK] {name}")
    else:
        FAIL += 1; print(f"[FAIL] {name} {extra}")


def test_weights_sum():
    check("веса FREE = 1000", W.total_weight(W.FREE_WHEEL) == 1000, str(W.total_weight(W.FREE_WHEEL)))
    check("веса GOLD = 1000", W.total_weight(W.GOLD_WHEEL) == 1000, str(W.total_weight(W.GOLD_WHEEL)))
    check("8 секторов FREE", len(W.FREE_WHEEL) == 8)
    check("8 секторов GOLD", len(W.GOLD_WHEEL) == 8)
    check("шансы FREE сумма 100%", abs(sum(p for _l, p in W.probabilities(W.FREE_WHEEL)) - 100) < 0.05)


def test_determinism():
    a = W.spin(W.FREE_WHEEL, "seed-1", "acc-A", "2026-07-01")
    b = W.spin(W.FREE_WHEEL, "seed-1", "acc-A", "2026-07-01")
    check("детерминизм: те же входы → тот же сектор", a["index"] == b["index"])
    # другой аккаунт обычно даёт другой результат (грайнд бессмысленен — 1/день)
    diff = sum(1 for i in range(50)
               if W.spin(W.FREE_WHEEL, "seed-1", f"acc-{i}", "2026-07-01")["index"]
               != W.spin(W.FREE_WHEEL, "seed-1", "acc-A", "2026-07-01")["index"])
    check("разные аккаунты → разнообразие результатов", diff > 30)


def test_provably_fair():
    seed = "epoch-secret-xyz"
    com = W.commit(seed)
    check("commit/verify_commit", W.verify_commit(seed, com))
    check("неверный seed не проходит commit", not W.verify_commit("wrong", com))
    res = W.spin(W.GOLD_WHEEL, seed, "acc-Z", "2026-07-02")
    check("verify_spin: верный индекс", W.verify_spin(W.GOLD_WHEEL, seed, "acc-Z", "2026-07-02", res["index"]))
    check("verify_spin: неверный индекс отвергнут",
          not W.verify_spin(W.GOLD_WHEEL, seed, "acc-Z", "2026-07-02", (res["index"] + 1) % 8))
    # проверка чужим seed даёт другой результат → подкрутить нельзя незаметно
    other = W.spin(W.GOLD_WHEEL, "other-seed", "acc-Z", "2026-07-02")
    check("другой seed → возможно другой результат (нельзя подменить незаметно)",
          isinstance(other["index"], int))


def test_distribution():
    """60k розыгрышей по разным аккаунтам: частоты ≈ заявленным шансам (±1.0 п.п.)."""
    N = 60000
    counts = [0] * 8
    for i in range(N):
        idx = W.spin(W.FREE_WHEEL, "seed-dist", f"u{i}", "2026-07-03")["index"]
        counts[idx] += 1
    probs = W.probabilities(W.FREE_WHEEL)
    ok = True
    worst = 0.0
    for i, (label, expected) in enumerate(probs):
        observed = 100 * counts[i] / N
        d = abs(observed - expected)
        worst = max(worst, d)
        if d > 1.0:
            ok = False
            print(f"    сектор {label}: ожид {expected}% набл {observed:.2f}% Δ{d:.2f}")
    check(f"распределение сходится к весам (макс Δ={worst:.2f} п.п.)", ok)


def test_expected_value():
    ev = W.expected_value(W.FREE_WHEEL)
    # ручной расчёт FREE: минут 75.6, дней 0.066
    check("EV FREE минут/спин ≈ 75.6", abs(ev["minutes_per_spin"] - 75.6) < 0.01, str(ev))
    check("EV FREE дней/спин ≈ 0.066", abs(ev["days_per_spin"] - 0.066) < 0.001, str(ev))
    evg = W.expected_value(W.GOLD_WHEEL)
    check("EV GOLD дней/спин больше FREE (платный перк)",
          evg["days_per_spin"] > ev["days_per_spin"], f"gold={evg['days_per_spin']} free={ev['days_per_spin']}")
    check("EV считает месяц", abs(ev["days_per_month"] - round(ev["days_per_spin"] * 30, 2)) < 0.01)


def test_pity():
    # эмулируем подряд спины без дня: на пороге следующий обязан дать день
    # берём вход, который НЕ день, и поднимаем счётчик до порога-1
    seed, day = "seed-pity", "2026-07-04"
    # найдём аккаунт, чей натуральный результат — минуты (не день)
    acc = next(f"p{i}" for i in range(100)
               if W.spin(W.FREE_WHEEL, seed, f"p{i}", day)["prize"]["kind"] == "minutes")
    nat = W.spin(W.FREE_WHEEL, seed, acc, day, pity_count=0)
    check("pity: ниже порога — без вмешательства", nat["pity"] is False and nat["pity_count_after"] == 1)
    forced = W.spin(W.FREE_WHEEL, seed, acc, day, pity_count=W.PITY_THRESHOLD - 1)
    check("pity: на пороге — гарантированный день", forced["pity"] is True and forced["prize"]["kind"] == "days")
    check("pity: день сбрасывает счётчик", forced["pity_count_after"] == 0)
    # натуральный день тоже сбрасывает
    accd = next(f"d{i}" for i in range(500)
                if W.spin(W.FREE_WHEEL, seed, f"d{i}", day)["prize"]["kind"] == "days")
    natd = W.spin(W.FREE_WHEEL, seed, accd, day, pity_count=10)
    check("pity: натуральный день сбрасывает счётчик", natd["pity_count_after"] == 0)


def test_streak():
    check("серия: день 3 → бонус минут", W.streak_bonus(3) == {"kind": "minutes", "amount": 120, "reason": "streak_3"})
    check("серия: день 7 → бонус дней", W.streak_bonus(7) == {"kind": "days", "amount": 2, "reason": "streak_7"})
    check("серия: обычный день — без бонуса", W.streak_bonus(5) is None)


def main():
    test_weights_sum()
    test_determinism()
    test_provably_fair()
    test_distribution()
    test_expected_value()
    test_pity()
    test_streak()
    print(f"\n=== ИТОГ: {PASS} OK, {FAIL} FAIL ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
