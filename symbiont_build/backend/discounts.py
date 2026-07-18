"""discounts.py — правила скидок Симбионта (чистая логика, без состояния).

Скидку выдаёт ВЛАДЕЛЕЦ (админ) или Server-in-a-Box — как платные ключи. Она может:
  • действовать в окне времени (starts_at … expires_at);
  • применяться к конкретному тарифу/продукту ИЛИ ко всему сразу (scope);
  • быть КАМПАНИЕЙ (без кода — применяется автоматически к подходящему продукту)
    ИЛИ КОДОМ (code — пользователь вводит его при оплате, как купон);
  • иметь общий лимит использований (max_uses) и лимит на аккаунт (max_per_account).

Реестр, персист и выпуск/подпись — на стороне server.py; здесь только проверки/расчёт,
чтобы их можно было тестировать изолированно.

Форма записи скидки (dict):
{
  "id": str, "code": str|None, "percent": int|None, "amount_off": float|None,
  "scope": "all" | {"tiers": [...], "products": [...]},
  "starts_at": int|None, "expires_at": int|None,
  "max_uses": int|None, "used": int, "max_per_account": int|None, "used_by": {aid:int},
  "active": bool, "source": "owner"|"server_in_a_box", "label": str|None
}
"""
from __future__ import annotations


def is_active(d: dict, now: int) -> bool:
    if not d.get("active", True):
        return False
    if d.get("starts_at") and now < d["starts_at"]:
        return False
    if d.get("expires_at") and now > d["expires_at"]:
        return False
    if d.get("max_uses") and d.get("used", 0) >= d["max_uses"]:
        return False
    return True


def scope_matches(d: dict, product: str, tier: str) -> bool:
    sc = d.get("scope", "all")
    if not sc or sc == "all":
        return True
    prods = sc.get("products") or []
    tiers = sc.get("tiers") or []
    if not prods and not tiers:          # пустой объём = на всё
        return True
    return (product in prods) or (tier in tiers)


def per_account_ok(d: dict, aid: str) -> bool:
    cap = d.get("max_per_account")
    if not cap:
        return True
    return d.get("used_by", {}).get(aid, 0) < cap


def applicable(d: dict, aid: str, product: str, tier: str, now: int, *, is_code: bool) -> bool:
    """Годна ли скидка d к покупке. is_code=True — код введён вручную (у d должен быть code);
    is_code=False — авто-кампания (у d НЕ должно быть code)."""
    if bool(d.get("code")) != is_code:
        return False
    return is_active(d, now) and scope_matches(d, product, tier) and per_account_ok(d, aid)


def compute(d: dict, base):
    """Вернуть (итог, размер_скидки). Процент приоритетнее фикс-суммы. base=None → без изменений."""
    if base is None or not d:
        return base, 0
    if d.get("percent"):
        off = round(base * d["percent"] / 100, 2)
    elif d.get("amount_off"):
        off = min(base, float(d["amount_off"]))
    else:
        off = 0
    off = max(0, min(off, base))
    return round(base - off, 2), off
