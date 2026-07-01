"""
wheel.py — колесо ежедневных наград «Симбионта».

Дизайн (улучшенный):
  • 8 секторов, награды эскалируют: мелкое активное время → дни подписки → джекпот;
  • ВЕСА (а не равные шансы): мелкое часто, крупное редко — суммы весов = 1000;
  • PROVABLY-FAIR через commit-reveal: оператор заранее публикует commitment = SHA256(seed)
    в подписанном манифесте; дневной результат = HMAC(seed, "account:date") → сектор;
    после эпохи seed раскрывается, и любой пересчитывает все результаты и сверяет commitment;
  • вход "account:date" фиксирован → один детерминированный результат в день, грайнд невозможен,
    а оператор не может подкрутить пост-фактум;
  • PITY: счётчик спинов без дня-награды; на пороге следующий спин гарантирует ≥ первый день-сектор;
  • СЕРИИ: за подряд идущие дни — бонусы на вехах (день 3 и день 7);
  • ЗОЛОТОЕ колесо для Premium/Ultimate — тот же механизм, веса смещены к крупному.

Этот модуль — каноничная улучшенная версия (инлайновую логику в server.py заменить при cutover).
"""
from __future__ import annotations
import hashlib
import hmac

# Сектор: (label, kind, amount, weight). kind ∈ {"minutes","days"}.
# Веса в каждом колесе суммируются в 1000 (= вероятность в десятых долях %).

FREE_WHEEL = [
    ("+15 мин",   "minutes", 15,  300),  # 30.0%
    ("+30 мин",   "minutes", 30,  250),  # 25.0%
    ("+1 ч",      "minutes", 60,  180),  # 18.0%
    ("+2 ч",      "minutes", 120, 120),  # 12.0%
    ("+4 ч",      "minutes", 240,  80),  #  8.0%
    ("+8 ч",      "minutes", 480,  40),  #  4.0%
    ("+1 день",   "days",    1,    24),  #  2.4%
    ("✦ +7 дней", "days",    7,     6),  #  0.6%
]

GOLD_WHEEL = [  # для Premium/Ultimate — лучше EV, больше дней
    ("+1 ч",       "minutes", 60,  300),  # 30.0%
    ("+2 ч",       "minutes", 120, 250),  # 25.0%
    ("+5 ч",       "minutes", 300, 200),  # 20.0%
    ("+10 ч",      "minutes", 600, 130),  # 13.0%
    ("+1 день",    "days",    1,    80),  #  8.0%
    ("+2 дня",     "days",    2,    28),  #  2.8%
    ("+5 дней",    "days",    5,    10),  #  1.0%
    ("✦ +15 дней", "days",    15,    2),  #  0.2%
]

PITY_THRESHOLD = 25      # спинов подряд без дня-награды → гарантированный день
STREAK_MILESTONES = {    # бонусы за серию (доп. к сектору)
    3: ("minutes", 120, "streak_3"),
    7: ("days",    2,   "streak_7"),
}


def total_weight(wheel) -> int:
    return sum(w for *_, w in wheel)


def probabilities(wheel) -> list[tuple[str, float]]:
    tw = total_weight(wheel)
    return [(label, round(100 * w / tw, 2)) for (label, _k, _a, w) in wheel]


def expected_value(wheel) -> dict:
    """Среднее за спин: минут активного времени и дней подписки (раздельно — разные валюты)."""
    tw = total_weight(wheel)
    minutes = sum(a * w for (_l, k, a, w) in wheel if k == "minutes") / tw
    days = sum(a * w for (_l, k, a, w) in wheel if k == "days") / tw
    return {"minutes_per_spin": round(minutes, 2), "days_per_spin": round(days, 4),
            "minutes_per_month": round(minutes * 30, 1), "days_per_month": round(days * 30, 2)}


# ── PROVABLY-FAIR ─────────────────────────────────────────────────────────────
def commit(server_seed: str) -> str:
    """Обязательство, публикуется заранее (в подписанном манифесте)."""
    return hashlib.sha256(server_seed.encode()).hexdigest()


def verify_commit(server_seed: str, commitment: str) -> bool:
    return hmac.compare_digest(commit(server_seed), commitment)


def _draw_index(wheel, server_seed: str, account_id: str, day: str) -> int:
    mac = hmac.new(server_seed.encode(), f"{account_id}:{day}".encode(), hashlib.sha256).digest()
    r = int.from_bytes(mac[:8], "big") % total_weight(wheel)
    cum = 0
    for i, (_l, _k, _a, w) in enumerate(wheel):
        cum += w
        if r < cum:
            return i
    return len(wheel) - 1  # недостижимо


def spin(wheel, server_seed: str, account_id: str, day: str, *, pity_count: int = 0) -> dict:
    """Детерминированный provably-fair результат за день. Возвращает
    {index, label, prize{kind,amount}, pity, pity_count_after}."""
    i = _draw_index(wheel, server_seed, account_id, day)
    label, kind, amount, _w = wheel[i]
    pity = False
    # PITY: слишком долго без дня → апгрейд до первого дня-сектора (правило публичное → проверяемо)
    if kind != "days" and (pity_count + 1) >= PITY_THRESHOLD:
        for j, (lab, k, am, _w2) in enumerate(wheel):
            if k == "days":
                i, label, kind, amount, pity = j, lab, k, am, True
                break
    pity_after = 0 if kind == "days" else pity_count + 1
    return {"index": i, "label": label, "prize": {"kind": kind, "amount": amount},
            "pity": pity, "pity_count_after": pity_after}


def verify_spin(wheel, server_seed: str, account_id: str, day: str, claimed_index: int,
                *, pity_count: int = 0) -> bool:
    """Пересчёт результата клиентом/аудитором (после раскрытия seed)."""
    return spin(wheel, server_seed, account_id, day, pity_count=pity_count)["index"] == claimed_index


def streak_bonus(streak_day: int):
    """Бонус за серию подряд идущих дней (на вехах). None — если веха не достигнута."""
    m = STREAK_MILESTONES.get(streak_day)
    if not m:
        return None
    kind, amount, reason = m
    return {"kind": kind, "amount": amount, "reason": reason}


def daily_proof(wheel, server_seed: str, account_id: str, day: str, commitment: str,
                *, pity_count: int = 0) -> dict:
    """Объект для UI «Честность»: что показать и как проверить (seed раскрывается позже)."""
    res = spin(wheel, server_seed, account_id, day, pity_count=pity_count)
    return {"day": day, "commitment": commitment, "index": res["index"],
            "label": res["label"], "prize": res["prize"], "pity": res["pity"],
            "input": f"{account_id}:{day}", "algo": "HMAC-SHA256(seed, input) mod 1000 → веса"}
