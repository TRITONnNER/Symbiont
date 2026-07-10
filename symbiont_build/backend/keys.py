"""
keys.py — выпуск и погашение ключей активации (Ed25519).
Канонический модуль для сервера (см. также key_redemption_reference.py с демо).
Подделка исключена (подпись + реестр); кража смягчается (bind-on-redeem +
одноразовость + отзыв). В бою реестр — строка БД с уникальным индексом, что и
даёт атомарность; здесь — память + Lock.
"""
from __future__ import annotations
import base64, json, secrets, time, threading, os
from dataclasses import dataclass, field
from typing import Optional
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


def b32(b: bytes) -> str: return base64.b32encode(b).decode().rstrip("=")
def unb32(s: str) -> bytes: return base64.b32decode(s + "=" * (-len(s) % 8))


def load_or_create_signing_key(path: str) -> Ed25519PrivateKey:
    """Загружает ключ подписи из файла (raw 32 байта) или создаёт и сохраняет."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Ed25519PrivateKey.from_private_bytes(f.read())
    sk = Ed25519PrivateKey.generate()
    with open(path, "wb") as f:
        f.write(sk.private_bytes(serialization.Encoding.Raw,
                                 serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption()))
    return sk


@dataclass
class KeyPayload:
    kid: str; plan: str; grant_days: int; uses: int; expires_at: int
    def canonical(self) -> bytes:
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":")).encode()


@dataclass
class KeyState:
    kid: str; uses_left: int; revoked: bool = False
    bound_accounts: set = field(default_factory=set)


class KeyError_(ValueError):
    """Ошибка погашения с кодом (key_invalid|key_revoked|key_expired|key_already_redeemed)."""


class Issuer:
    def __init__(self, signing_key: Optional[Ed25519PrivateKey] = None, registry_path: Optional[str] = None):
        self._sk = signing_key or Ed25519PrivateKey.generate()
        self._pk: Ed25519PublicKey = self._sk.public_key()
        self._registry: dict[str, KeyState] = {}
        self._lock = threading.Lock()
        self._path = registry_path     # если задан — реестр ключей переживает рестарт
        if registry_path:
            self._load()

    def _load(self) -> None:
        from persist import jload
        for kid, d in jload(self._path, {}).items():
            self._registry[kid] = KeyState(
                kid=kid, uses_left=d.get("uses_left", 0), revoked=d.get("revoked", False),
                bound_accounts=set(d.get("bound_accounts", [])))

    def _save(self) -> None:
        if not self._path:
            return
        from persist import jsave
        jsave(self._path, {kid: {"kid": st.kid, "uses_left": st.uses_left,
                                 "revoked": st.revoked, "bound_accounts": sorted(st.bound_accounts)}
                           for kid, st in self._registry.items()})

    def public_key_b64(self) -> str:
        return base64.b64encode(self._pk.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

    def issue(self, plan: str, grant_days: int, uses: int = 1, ttl_days: int = 365) -> str:
        kid = b32(secrets.token_bytes(10))
        p = KeyPayload(kid, plan, grant_days, uses, int(time.time()) + ttl_days * 86400)
        sig = self._sk.sign(p.canonical())
        with self._lock:
            self._registry[kid] = KeyState(kid=kid, uses_left=uses)
        self._save()
        # видимый код группами по 4 для красоты; парсинг игнорирует дефисы/регистр
        raw = f"{b32(p.canonical())}.{b32(sig)}"
        return raw

    def revoke(self, kid: str) -> bool:
        with self._lock:
            st = self._registry.get(kid)
            if not st or st.uses_left <= 0:
                return False
            st.revoked = True
        self._save()
        return True

    def _parse_verify(self, code: str) -> KeyPayload:
        try:
            body_b32, sig_b32 = code.strip().split(".")
            body, sig = unb32(body_b32), unb32(sig_b32)
            self._pk.verify(sig, body)
        except (ValueError, InvalidSignature):
            raise KeyError_("key_invalid")
        return KeyPayload(**json.loads(body))

    # ── ПРОВЕРКА без погашения (game-style checker; НЕ мутирует, НЕ требует аккаунта) ──
    # Статусы — чистый перечень для UI (сайт/приложение) и веб-чекера:
    # valid | already_redeemed | expired | revoked | not_found | invalid.
    _CHECK_MESSAGES = {
        "valid":            "Ключ действителен и готов к активации",
        "already_redeemed": "Ключ уже использован",
        "expired":          "Срок действия ключа истёк",
        "revoked":          "Ключ отозван",
        "not_found":        "Код подлинный, но не зарегистрирован в этой системе",
        "invalid":          "Недействительный код — подделка или опечатка",
    }

    def check(self, code: str) -> dict:
        """Проверить код БЕЗ погашения и БЕЗ авторизации (как code-checker у игр).
        Подпись ловит подделку/опечатку оффлайн; реестр даёт статус в этой системе.
        Возвращает {status, ok, message, valid, grants{days,tier}, uses_left,
        uses_total, expires_at, registered}. Ничего не меняет."""
        def out(status: str, **extra) -> dict:
            return {"status": status, "ok": status == "valid",
                    "message": self._CHECK_MESSAGES[status], **extra}
        try:
            p = self._parse_verify(code)          # подпись + декод (бросит key_invalid)
        except KeyError_:
            return out("invalid", valid=False)
        now = int(time.time())
        base = {"valid": True, "kid": p.kid, "grants": {"days": p.grant_days, "tier": p.plan},
                "uses_total": p.uses, "expires_at": p.expires_at}
        with self._lock:
            st = self._registry.get(p.kid)
        if st is None:
            # подписано нами, но в этом бэкенде нет (другая среда / не сохранён выпуск)
            return out("not_found", registered=False, **base)
        base["registered"] = True
        base["uses_left"] = st.uses_left
        if st.revoked:
            return out("revoked", **base)
        if p.expires_at < now:
            return out("expired", **base)
        if st.uses_left <= 0:
            return out("already_redeemed", **base)
        res = out("valid", **base)
        if p.uses and p.uses > 1:
            res["message"] = (f"Ключ действителен — осталось активаций: "
                              f"{st.uses_left} из {p.uses}")
        return res

    def redeem(self, code: str, account_token: str) -> dict:
        p = self._parse_verify(code)
        if p.expires_at < int(time.time()):
            raise KeyError_("key_expired")
        with self._lock:
            st = self._registry.get(p.kid)
            if st is None:
                raise KeyError_("key_invalid")
            if st.revoked:
                raise KeyError_("key_revoked")
            if account_token in st.bound_accounts:
                return {"plan": p.plan, "grant_days": p.grant_days, "idempotent": True}
            if st.uses_left <= 0:
                raise KeyError_("key_already_redeemed")
            st.uses_left -= 1
            st.bound_accounts.add(account_token)
        self._save()
        return {"plan": p.plan, "grant_days": p.grant_days, "idempotent": False}
