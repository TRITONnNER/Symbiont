"""
vouchers.py — расширенные ключи-ваучеры поверх db.py.

Удобно СОЗДАВАТЬ:
  • выбор срока        — grant_days (дни) ИЛИ grant_minutes (баланс активного времени);
  • выбор тарифа       — premium | ultimate (или None — только время, не меняя тариф);
  • количество         — issue_batch(count=...) выпускает партию одним вызовом;
  • многоразовость     — uses>1 (один ваучер на N активаций, напр. промо);
  • срок жизни кода    — ttl_days (до активации);
  • метка/заметка/партия — label/note/batch_id для реселлеров и кампаний.

Удобно АКТИВИРОВАТЬ:
  • дружелюбный формат  SYMB-XXXX-XXXX-…  (группы по 4);
  • парсер прощает регистр, пробелы, дефисы, неправильные переносы;
  • подпись Ed25519 — подделать/угадать нельзя; погашение атомарно (db.redeem_key).

Подделка исключена (подпись + реестр), кража смягчается (одноразовость + bind-on-redeem
+ отзыв + срок) — как и в keys.py, но с богатой полезной нагрузкой.
"""
from __future__ import annotations
import base64
import json
import secrets
import time
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

import db as dbm


# ── кодирование ───────────────────────────────────────────────────────────────
def _b32(b: bytes) -> str:
    return base64.b32encode(b).decode().rstrip("=")


def _unb32(s: str) -> bytes:
    return base64.b32decode(s + "=" * (-len(s) % 8))


def _group(s: str, n: int = 4) -> str:
    return "-".join(s[i:i + n] for i in range(0, len(s), n))


def format_code(payload_b32: str, sig_b32: str) -> str:
    """Дружелюбный вид: SYMB-<группы payload>.<группы sig>. Префикс SYMB- — бренд/намёк;
    парсер его не требует. Точка отделяет нагрузку от подписи (вне base32-алфавита,
    поэтому однозначна). Каждую часть группируем по 4 для удобства ввода."""
    return "SYMB-" + _group(payload_b32) + "." + _group(sig_b32)


def parse_code(code: str) -> tuple[bytes, bytes]:
    """Разбор дружелюбного кода в (payload_bytes, sig_bytes). Прощает регистр/пробелы/дефисы
    и необязательный префикс SYMB-. Бросает ValueError при кривом формате."""
    s = code.strip().upper().replace(" ", "")
    # убрать префикс (в любом регистре, с дефисом или без)
    for pref in ("SYMB-", "SYMB"):
        if s.startswith(pref):
            s = s[len(pref):]
            break
    if "." not in s:
        raise ValueError("bad_format")
    body_part, sig_part = s.split(".", 1)
    body_b32 = body_part.replace("-", "")
    sig_b32 = sig_part.replace("-", "")
    return _unb32(body_b32), _unb32(sig_b32)


# ── полезная нагрузка ─────────────────────────────────────────────────────────
def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def make_kid() -> str:
    return _b32(secrets.token_bytes(10))


# ── ВЫПУСК ────────────────────────────────────────────────────────────────────
def issue(db: dbm.Database, sk: Ed25519PrivateKey, *, plan: str = "premium",
          grant_days: int = 0, grant_minutes: int = 0, tier: Optional[str] = "premium",
          uses: int = 1, ttl_days: int = 365, issued_by: str = "admin",
          batch_id: Optional[str] = None, label: Optional[str] = None,
          note: Optional[str] = None) -> dict:
    """Выпустить один ваучер. Возвращает {kid, code}. Регистрирует в БД (db.issue_key)."""
    if grant_days <= 0 and grant_minutes <= 0:
        raise ValueError("need_grant_days_or_minutes")
    kid = make_kid()
    expires_at = int(time.time()) + ttl_days * 86400
    payload = {"kid": kid, "plan": plan, "grant_days": grant_days,
               "grant_minutes": grant_minutes, "tier": tier, "uses": uses,
               "expires_at": expires_at}
    body = _canonical(payload)
    sig = sk.sign(body)
    db.issue_key(kid, plan=plan, grant_days=grant_days, grant_minutes=grant_minutes, tier=tier,
                 uses=uses, expires_at=expires_at, issued_by=issued_by, batch_id=batch_id,
                 label=label, note=note)
    return {"kid": kid, "code": format_code(_b32(body), _b32(sig))}


def issue_batch(db: dbm.Database, sk: Ed25519PrivateKey, *, count: int, **kw) -> dict:
    """Выпустить ПАРТИЮ одинаковых ваучеров одним вызовом. Возвращает
    {batch_id, label, codes:[{kid,code}...]}. Удобно для реселлеров/кампаний."""
    if count < 1:
        raise ValueError("count_must_be_positive")
    batch_id = kw.pop("batch_id", None) or ("batch-" + _b32(secrets.token_bytes(6)))
    label = kw.get("label")
    codes = [issue(db, sk, batch_id=batch_id, **kw) for _ in range(count)]
    return {"batch_id": batch_id, "label": label, "count": count, "codes": codes}


# ── АКТИВАЦИЯ ─────────────────────────────────────────────────────────────────
def redeem(db: dbm.Database, pk: Ed25519PublicKey, code: str, *,
           account_token: str, account_id: str) -> dict:
    """Активировать ваучер: проверить подпись → атомарно погасить → начислить грант
    аккаунту. Возвращает {applied, idempotent, balance}. Бросает RedeemError с кодом
    (совместимо с keys.py: key_invalid|key_revoked|key_expired|key_already_redeemed)."""
    # 1) разбор + проверка подписи
    try:
        body, sig = parse_code(code)
        pk.verify(sig, body)
        payload = json.loads(body)
        kid = payload["kid"]
    except (ValueError, InvalidSignature, KeyError, json.JSONDecodeError):
        raise dbm.RedeemError("key_invalid")
    # 2) атомарное погашение (реестр БД — источник истины: одноразовость, отзыв, срок)
    res = db.redeem_key(kid, account_token)   # бросит RedeemError при revoked/expired/already
    # 3) начисление гранта аккаунту (только если это НЕ идемпотентный повтор)
    if not res["idempotent"]:
        db.apply_grant(account_id, days=res["grant_days"], minutes=res["grant_minutes"],
                       kind="redeem", reason=f"voucher:{kid}", tier=res["tier"], ref=kid)
    return {"applied": {"days": res["grant_days"], "minutes": res["grant_minutes"],
                        "tier": res["tier"]},
            "idempotent": res["idempotent"], "balance": db.balance(account_id)}


def preview(db: dbm.Database, pk: Ed25519PublicKey, code: str) -> dict:
    """Показать, что даёт ваучер, БЕЗ погашения (для UI «вы вводите ключ на N дней…»).
    Возвращает {valid, plan, grant_days, grant_minutes, tier, uses_left, expired, revoked}."""
    try:
        body, sig = parse_code(code)
        pk.verify(sig, body)
        p = json.loads(body)
    except (ValueError, InvalidSignature, json.JSONDecodeError):
        return {"valid": False}
    st = db.key_state(p["kid"])
    now = int(time.time())
    return {"valid": True, "plan": p.get("plan"), "grant_days": p.get("grant_days"),
            "grant_minutes": p.get("grant_minutes"), "tier": p.get("tier"),
            "uses_left": st["uses_left"] if st else None,
            "expired": p.get("expires_at", now) < now,
            "revoked": bool(st["revoked"]) if st else False,
            "registered": st is not None}


# ── ПРОВЕРКА КОДА «как у игровых сервисов» (без активации) ────────────────────
# Статусы — чистый перечень для UI (сайт/приложение). Сообщения — человеку.
STATUS_MESSAGES = {
    "valid":            "Ключ действителен и готов к активации",
    "already_redeemed": "Ключ уже использован",
    "expired":          "Срок действия ключа истёк",
    "revoked":          "Ключ отозван",
    "not_found":        "Код подлинный, но не зарегистрирован в этой системе",
    "invalid":          "Недействительный код — подделка или опечатка",
}


def check(db: dbm.Database, pk: Ed25519PublicKey, code: str) -> dict:
    """Проверка кода на работоспособность БЕЗ погашения (как code-checker у Fortnite).
    Не требует аккаунта/авторизации — можно вызывать с сайта до входа.
    Возвращает {status, ok, message, grants{days,minutes,tier}, uses_left, uses_total,
    expires_at, registered}. status ∈ STATUS_MESSAGES."""
    def out(status, **extra):
        return {"status": status, "ok": status == "valid",
                "message": STATUS_MESSAGES[status], **extra}

    # 1) подпись + декод нагрузки (ловит подделку/опечатку оффлайн)
    try:
        body, sig = parse_code(code)
        pk.verify(sig, body)
        p = json.loads(body)
        kid = p["kid"]
    except (ValueError, InvalidSignature, KeyError, json.JSONDecodeError):
        return out("invalid", valid=False)

    now = int(time.time())
    grants = {"days": p.get("grant_days", 0), "minutes": p.get("grant_minutes", 0),
              "tier": p.get("tier")}
    base = {"valid": True, "kid": kid, "grants": grants, "uses_total": p.get("uses"),
            "expires_at": p.get("expires_at")}

    # 2) сверка с реестром этой системы
    st = db.key_state(kid)
    if st is None:
        # подписано нами, но в этом бэкенде нет (другая среда / не сохранён выпуск)
        return out("not_found", registered=False, **base)
    base["registered"] = True
    base["uses_left"] = st["uses_left"]
    base["uses_total"] = st["uses_total"]

    if st["revoked"]:
        return out("revoked", **base)
    if p.get("expires_at", now) < now:
        return out("expired", **base)
    if st["uses_left"] <= 0:
        return out("already_redeemed", **base)

    # действителен; для многоразового — сколько активаций осталось
    res = out("valid", **base)
    if st["uses_total"] and st["uses_total"] > 1:
        res["message"] = (f"Ключ действителен — осталось активаций: "
                          f"{st['uses_left']} из {st['uses_total']}")
    return res
