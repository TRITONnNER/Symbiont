"""
key_redemption_reference.py — РЕФЕРЕНС логики ключей активации «Симбионта».

Реализует честную модель из тома VI §4:
  • ПОДДЕЛКА исключается:   ключ = полезная нагрузка + подпись Ed25519, сверяется
                            с серверным реестром. Угадать/подделать нельзя.
  • КРАЖА только смягчается: ключ «на предъявителя». Поэтому —
        - одноразовость + атомарное погашение (нет двойной траты),
        - привязка при первой активации (bind-on-redeem),
        - отзыв ещё не погашенного ключа,
        - срок действия.
    Сделать «неугоняемым» нельзя — кто держит, тот и активирует.

Зависимость: cryptography  (pip install cryptography)
Хранилище здесь — в памяти; в бою заменить на БД с транзакцией/уникальным индексом,
которая и гарантирует атомарность погашения.
"""

from __future__ import annotations
import base64, json, secrets, time, threading
from dataclasses import dataclass, field
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


# ── формат ключа ──────────────────────────────────────────────────────────────
# Видимый код:  SYMB-XXXX-XXXX-XXXX...  (для удобства), а «под капотом» —
# base32(payload) + "." + base32(signature). Здесь для краткости один компактный
# токен: b32(json_payload) + "." + b32(sig). UI показывает его группами по 4.

def _b32(b: bytes) -> str:
    return base64.b32encode(b).decode().rstrip("=")

def _unb32(s: str) -> bytes:
    pad = "=" * (-len(s) % 8)
    return base64.b32decode(s + pad)


@dataclass
class KeyPayload:
    kid: str          # уникальный id ключа (для реестра/отзыва)
    plan: str         # "pro" | "trial" | "topup"
    grant_days: int   # сколько дней добавляет
    uses: int         # 1 = одноразовый; >1 = многоразовый (на N активаций)
    expires_at: int   # unix-срок действия самого КОДА (до активации)

    def canonical(self) -> bytes:
        # детерминированная сериализация для подписи
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":")).encode()


# ── состояние в реестре (в бою — строка в БД) ─────────────────────────────────
@dataclass
class KeyState:
    kid: str
    uses_left: int
    revoked: bool = False
    bound_accounts: set[str] = field(default_factory=set)  # кто уже активировал


class Issuer:
    """Выпускает и проверяет/погашает ключи."""

    def __init__(self, signing_key: Optional[Ed25519PrivateKey] = None):
        self._sk = signing_key or Ed25519PrivateKey.generate()
        self._pk: Ed25519PublicKey = self._sk.public_key()
        self._registry: dict[str, KeyState] = {}     # kid -> state
        self._lock = threading.Lock()                # имитация транзакции БД

    # offline-проверяемая часть (подпись) + серверная часть (реестр)
    def issue(self, plan: str, grant_days: int, uses: int = 1, ttl_days: int = 365) -> str:
        kid = _b32(secrets.token_bytes(10))          # 80-битный id
        payload = KeyPayload(kid, plan, grant_days, uses,
                             int(time.time()) + ttl_days * 86400)
        sig = self._sk.sign(payload.canonical())
        with self._lock:
            self._registry[kid] = KeyState(kid=kid, uses_left=uses)
        return f"{_b32(payload.canonical())}.{_b32(sig)}"

    def public_key_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._pk.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def revoke(self, kid: str) -> bool:
        with self._lock:
            st = self._registry.get(kid)
            if not st or st.uses_left <= 0:
                return False
            st.revoked = True
            return True

    def _parse_verify(self, code: str) -> KeyPayload:
        """Проверяет ПОДПИСЬ (подделка исключена). Бросает ValueError при провале."""
        try:
            body_b32, sig_b32 = code.strip().split(".")
            body, sig = _unb32(body_b32), _unb32(sig_b32)
            self._pk.verify(sig, body)               # InvalidSignature → подделка
        except (ValueError, InvalidSignature):
            raise ValueError("key_invalid")
        p = json.loads(body)
        return KeyPayload(**p)

    def redeem(self, code: str, account_token: str) -> dict:
        """
        Атомарно погашает ключ для аккаунта. Идемпотентно для повторной активации
        ТЕМ ЖЕ аккаунтом. Возвращает grant; бросает ValueError(code) при отказе.
        """
        payload = self._parse_verify(code)           # 1) подпись/подделка

        if payload.expires_at < int(time.time()):    # 2) срок кода
            raise ValueError("key_expired")

        with self._lock:                             # 3) атомарная секция (= транзакция БД)
            st = self._registry.get(payload.kid)
            if st is None:
                raise ValueError("key_invalid")
            if st.revoked:
                raise ValueError("key_revoked")

            # идемпотентность: повтор тем же аккаунтом — не списываем повторно
            if account_token in st.bound_accounts:
                return {"plan": payload.plan, "grant_days": payload.grant_days,
                        "idempotent": True}

            if st.uses_left <= 0:
                raise ValueError("key_already_redeemed")

            # bind-on-redeem + списание одной активации (защита от двойной траты)
            st.uses_left -= 1
            st.bound_accounts.add(account_token)

        return {"plan": payload.plan, "grant_days": payload.grant_days, "idempotent": False}


# ── мини-демо/самопроверка ────────────────────────────────────────────────────
if __name__ == "__main__":
    iss = Issuer()

    one = iss.issue(plan="pro", grant_days=30, uses=1)
    print("issued:", one[:24], "…")

    print("redeem #1:", iss.redeem(one, account_token="acc_A"))      # ok
    print("redeem same acc again:", iss.redeem(one, "acc_A"))         # idempotent

    try:
        iss.redeem(one, account_token="acc_B")                        # уже погашен другим
    except ValueError as e:
        print("redeem by other:", e)

    # подделка: меняем символ — подпись не сойдётся
    forged = ("AA" + one[2:]) if one[0] != "A" else ("BB" + one[2:])
    try:
        iss._parse_verify(forged)
    except ValueError as e:
        print("forged key:", e)

    # многоразовый промо-ключ на 3 активации
    promo = iss.issue(plan="trial", grant_days=7, uses=3)
    print("promo uses:", [iss.redeem(promo, f"u{i}")["grant_days"] for i in range(3)])
    try:
        iss.redeem(promo, "u4")
    except ValueError as e:
        print("promo exhausted:", e)
