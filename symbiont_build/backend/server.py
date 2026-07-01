"""
server.py — референс-бэкенд «Симбионта» (FastAPI). Реализует backend/API.md.
Принципы: нет личных данных, авторизация по непрозрачному токену, конфигурация
только подписанным манифестом, сервер не знает «кто-куда».

Запуск:
    pip install -r requirements.txt
    uvicorn server:app --reload
Документация эндпоинтов: http://127.0.0.1:8000/docs

Хранилища — в памяти (см. комментарии «# DB:»). Ключ подписи — файл signing.key
(создаётся при старте). ADMIN_TOKEN — из переменной окружения (по умолчанию demo).
"""
from __future__ import annotations
import os, json, secrets, base64, time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel

import hmac, hashlib
from fastapi import Request
import persist
import identity
from keys import Issuer, KeyError_, load_or_create_signing_key, b32
from manifest import demo_manifest, sign_manifest

# ── Секреты: env → secrets.json → генерация. Никаких предсказуемых дефолтов. ───
# В prod секреты задают через окружение. Если их нет — генерируем случайные и
# сохраняем в secrets.json (стабильны между рестартами; secrets.json НЕ коммитить).
import secrets as _secrets_mod
_secrets_store = persist.jload("secrets.json", {})
_secrets_before = dict(_secrets_store)

def _resolve_secret(env_key: str, store_key: str, gen_bytes: int = 24) -> str:
    v = os.environ.get(env_key)
    if v:
        return v
    if _secrets_store.get(store_key):
        return _secrets_store[store_key]
    v = b32(_secrets_mod.token_bytes(gen_bytes))
    _secrets_store[store_key] = v
    return v

ADMIN_TOKEN = _resolve_secret("SYMBIONT_ADMIN_TOKEN", "admin_token")
# секрет ЗАЧИСЛЕНИЯ узлов (enrollment): узел подписывает им ТОЛЬКО регистрацию,
# в ответ получает СВОЙ пер-узловой секрет для дальнейших вызовов.
NODE_SECRET = _resolve_secret("SYMBIONT_NODE_SECRET", "node_enroll_secret")
WHEEL_SEED = _resolve_secret("SYMBIONT_WHEEL_SEED", "wheel_seed")
# секрет подписи вебхука платёжного провайдера (в проде — секрет из кабинета
# провайдера). Провайдер подписывает им колбэк, бэкенд подтверждает pending-платёж.
PAY_WEBHOOK_SECRET = _resolve_secret("SYMBIONT_PAY_WEBHOOK_SECRET", "pay_webhook_secret")
# секрет HMAC алиасов — тоже из хранилища (не предсказуемый дефолт identity.py)
identity.ALIAS_SECRET = _resolve_secret("SYMBIONT_ALIAS_SECRET", "alias_secret").encode()

# ── подпись и выпуск ──────────────────────────────────────────────────────────
_sk = load_or_create_signing_key("signing.key")
issuer = Issuer(_sk, registry_path="keys_registry.json")  # реестр ключей переживает рестарт
# Пер-узловые секреты: node_id → секрет (выдаётся при регистрации, переживает рестарт).
_node_secrets: dict[str, str] = persist.jload("node_secrets.json", {})
if _secrets_store != _secrets_before:
    persist.jsave("secrets.json", _secrets_store)
    print("[secrets] недостающие секреты сгенерированы и сохранены в secrets.json (НЕ коммитить, держать вне репо)")
MANIFEST_BASE = 184          # базовая версия (demo-узлы)
MANIFEST_VERSION = MANIFEST_BASE
ROLLOUT = 100                # текущая волна canary-выкатки, % (операторский рычаг)


# ── Само-починка узлов: heartbeat + свип протухших ────────────────────────────
# Узлы, зарегистрировавшиеся сами (/v1/node/register), шлют раз в ~минуту heartbeat
# (/v1/node/heartbeat). Нет свежего heartbeat дольше NODE_STALE_SEC → узел выпадает
# из манифеста; снова зашумел → возвращается. Узлы, опубликованные вручную
# (publish_nodes.py, без heartbeat), НЕ трогаем — ими управляет оператор.
NODE_STALE_SEC = int(os.environ.get("SYMBIONT_NODE_STALE_SEC", "150"))
_node_health: dict[str, dict] = persist.jload("node_health.json", {})  # id -> {last_seen, load_pct}
def _save_node_health(): persist.jsave("node_health.json", _node_health)
_node_rev = 1 if os.path.exists("nodes.json") else 0   # версия += при смене НАБОРА узлов

def _healthy_ids() -> set:
    """id узлов, которые сейчас показываем клиенту: со свежим heartbeat, ЛИБО без
    записи health вообще (опубликованы вручную — само-починка их не касается)."""
    now = int(time.time())
    data = persist.jload("nodes.json", {"nodes": []})
    cur = data.get("nodes") if isinstance(data, dict) else data
    ids = set()
    for n in (cur or []):
        h = _node_health.get(n.get("id"))
        if h is None or (now - h.get("last_seen", 0)) <= NODE_STALE_SEC:
            ids.add(n.get("id"))
    return ids


def _build_manifest():
    """Манифест из demo-тела; если рядом есть nodes.json (реальные узлы) — подставляем
    их, отфильтровав ПРОТУХШИЕ (без свежего heartbeat) и подставив живую загрузку.
    Версия = MANIFEST_BASE + _node_rev: растёт при смене набора живых узлов, но НЕ при
    смене rollout (иначе клиент ложно увидел бы «обновление»). rollout — в подписанном
    теле (клиент не подделает)."""
    global MANIFEST_VERSION
    MANIFEST_VERSION = MANIFEST_BASE + _node_rev
    body = demo_manifest(MANIFEST_VERSION)
    if os.path.exists("nodes.json"):
        try:
            data = persist.jload("nodes.json", {"nodes": []})
            real = data.get("nodes") if isinstance(data, dict) else data
            if real:
                healthy = _healthy_ids()
                live = []
                for n in real:
                    if n.get("id") not in healthy:
                        continue                       # протухший — само-починка убрала
                    h = _node_health.get(n.get("id"))
                    if h and isinstance(h.get("load_pct"), int):
                        n = {**n, "loadPct": h["load_pct"]}   # живая загрузка из heartbeat
                    live.append(n)
                body["nodes"] = live
        except Exception as e:
            print(f"[manifest] nodes.json не загружен: {e}")
    body["rollout"] = ROLLOUT
    return sign_manifest(body, _sk)


_last_healthy: set = _healthy_ids()
_manifest_signed = _build_manifest()

def _bump_and_rebuild():
    """Набор узлов ИЗМЕНИЛСЯ (регистрация/новые параметры) → двигаем версию и
    переподписываем манифест, чтобы клиенты перетянули."""
    global _manifest_signed, _node_rev, _last_healthy
    _node_rev += 1
    _last_healthy = _healthy_ids()
    _manifest_signed = _build_manifest()

def _sync_manifest_health() -> bool:
    """Ленивый свип: если набор ЖИВЫХ узлов изменился (кто-то протух/вернулся) —
    двигаем версию и пересобираем. Возвращает True при изменении. Вызывается из
    heartbeat и из чтения манифеста (без фонового потока)."""
    if _healthy_ids() != _last_healthy:
        _bump_and_rebuild()
        return True
    return False

# ── «БД» в памяти ─────────────────────────────────────────────────────────────
# Персистентные хранилища (переживают рестарт). Файлы — в рабочем каталоге бэкенда.
ACCOUNTS_FILE, THREADS_FILE = "accounts.json", "threads.json"
accounts: dict[str, dict] = persist.jload(ACCOUNTS_FILE, {})
threads: dict[str, list] = persist.jload(THREADS_FILE, {})
def _save_accounts(): persist.jsave(ACCOUNTS_FILE, accounts)
def _save_threads(): persist.jsave(THREADS_FILE, threads)

# ── Слой аккаунтов (identity): алиасы и крипто-личности ────────────────────────
# accounts[token] — СЕССИЯ (как и раньше). Ниже — слой АККАУНТА:
ALIASES_FILE, IDACCOUNTS_FILE = "aliases.json", "idaccounts.json"
aliases: dict[str, str] = persist.jload(ALIASES_FILE, {})        # alias_hmac -> account_id
idaccounts: dict[str, dict] = persist.jload(IDACCOUNTS_FILE, {}) # account_id -> {...}
def _save_aliases(): persist.jsave(ALIASES_FILE, aliases)
def _save_idaccounts(): persist.jsave(IDACCOUNTS_FILE, idaccounts)

# ── Устройства (Этап 2): список, роли owner-class, отзыв ───────────────────────
DEVICES_FILE = "devices.json"
devices: dict[str, dict] = persist.jload(DEVICES_FILE, {})  # device_id -> {...}
def _save_devices(): persist.jsave(DEVICES_FILE, devices)

def _register_device(account_id: str, dev) -> str:
    """Привязать устройство к аккаунту. Первое устройство аккаунта → owner-class."""
    did = (dev.id if dev and dev.id else None) or b32(secrets.token_bytes(10))
    ex = devices.get(did)
    if ex and ex["account_id"] == account_id:
        ex["last_seen"] = int(time.time())
        if dev and dev.name: ex["name"] = dev.name
        ex["revoked"] = False
    else:
        first = not any(d["account_id"] == account_id and not d.get("revoked") for d in devices.values())
        devices[did] = {"account_id": account_id,
                        "name": (dev.name if dev else None) or "Устройство",
                        "platform": (dev.platform if dev else None) or "unknown",
                        "created_at": int(time.time()), "last_seen": int(time.time()),
                        "role": "owner" if first else "normal", "revoked": False}
    _save_devices()
    return did

def _account_of(token: str): return accounts[token].get("account_id")
def _is_owner(token: str) -> bool:
    did = accounts[token].get("device_id")
    return bool(did and devices.get(did, {}).get("role") == "owner")

# Анти-фрод: PoW (0 = выкл для тестов; в prod выставить >0) и rate-limit по /24.
import collections
POW_BITS = int(os.environ.get("SYMBIONT_POW_BITS", "0"))
REG_LIMIT_PER_HOUR = int(os.environ.get("SYMBIONT_REG_LIMIT", "20"))
_pow_challenges: dict[str, float] = {}
_reg_by_subnet: dict[str, list] = collections.defaultdict(list)

def _subnet24(ip: str) -> str:
    p = ip.split("."); return ".".join(p[:3]) if len(p) == 4 else ip

def _mint_session(account_id: str, label: str, device_id: str = None) -> str:
    """Выдать сессионный токен поверх аккаунта (совместимо с auth/redeem/support)."""
    token = b32(secrets.token_bytes(20))
    acc = idaccounts.get(account_id, {})
    accounts[token] = {"label": label, "plan": acc.get("tier", "free"),
                       "paid_until": None, "max_sessions": 1,
                       "account_id": account_id, "device_id": device_id}
    _save_accounts()
    return token

app = FastAPI(title="Symbiont backend (reference)", version="0.1")

# CORS: публичный веб-чекер ключа (web/check.html) и лендинг живут на другом
# origin и должны звать API из браузера. Безопасно, потому что авторизация — по
# заголовку Authorization: Bearer (НЕ по кукам), и allow_credentials=False:
# сторонний сайт не может «одолжить» токен пользователя (ambient-креденшелов нет),
# а все чувствительные эндпоинты требуют Bearer, которого у него нет.
from fastapi.middleware.cors import CORSMiddleware
_cors_origins = os.environ.get("SYMBIONT_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


# ── авторизация по токену ─────────────────────────────────────────────────────
def auth(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing_token")
    token = authorization[len("Bearer "):]
    if token not in accounts:
        raise HTTPException(401, "unknown_token")
    did = accounts[token].get("device_id")
    if did and devices.get(did, {}).get("revoked"):
        raise HTTPException(401, "device_revoked")
    return token


def admin(x_admin_token: str = Header(default="")) -> None:
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, "forbidden")


# ── модели запросов ───────────────────────────────────────────────────────────
class AnonReq(BaseModel):
    label: Optional[str] = None

class LabelReq(BaseModel):
    label: str

class RedeemReq(BaseModel):
    code: str

class IssueReq(BaseModel):
    plan: str = "pro"
    grant_days: int = 30
    uses: int = 1

class MsgReq(BaseModel):
    text: str
    diag: Optional[dict] = None

class AttestReq(BaseModel):
    platform: str
    token_blob: str


# ── 1. Аккаунт без данных ─────────────────────────────────────────────────────
@app.post("/v1/account/anon")
def account_anon(req: AnonReq):
    token = b32(secrets.token_bytes(20))           # 160-битный непрозрачный токен
    accounts[token] = {"label": req.label or "guest", "plan": "free",
                       "paid_until": None, "max_sessions": 1}
    threads[token] = [{"from": "support",
                       "text": "Здравствуйте! Опишите проблему — отвечаем прямо здесь, данные не нужны.",
                       "at": _now()}]
    _save_accounts(); _save_threads()
    a = accounts[token]
    return {"token": token, "label": a["label"],
            "subscription": {"plan": a["plan"], "maxConcurrentSessions": a["max_sessions"]}}

@app.patch("/v1/account/label")
def set_label(req: LabelReq, token: str = Depends(auth)):
    accounts[token]["label"] = req.label          # формат НЕ проверяем
    _save_accounts()
    return {"label": req.label}


# ── 1b. Аккаунты через алиасы (крипто-личность) ───────────────────────────────
class Alias(BaseModel):
    value: str
    kind: str = "free"      # nick | email | phone | free — метка, БЕЗ верификации

class DeviceInfo(BaseModel):
    id: Optional[str] = None
    name: str = "Устройство"
    platform: str = "unknown"

class RegisterReq(BaseModel):
    aliases: list[Alias]
    password: Optional[str] = None
    device: Optional[DeviceInfo] = None
    invite: Optional[str] = None        # инвайт-код пригласившего
    pow_challenge: Optional[str] = None
    pow_nonce: Optional[str] = None

class LoginReq(BaseModel):
    alias: Optional[Alias] = None
    recovery_code: Optional[str] = None
    password: Optional[str] = None
    device: Optional[DeviceInfo] = None

@app.get("/v1/account/pow")
def account_pow():
    """Выдать PoW-челлендж для регистрации (анти-фрод). bits=0 → PoW выключен."""
    chal = b32(secrets.token_bytes(12))
    _pow_challenges[chal] = time.time() + 120
    return {"challenge": chal, "bits": POW_BITS}

@app.post("/v1/account/register")
def account_register(req: RegisterReq, request: Request):
    now = time.time()
    # rate-limit по /24
    sub = _subnet24(request.client.host if request.client else "0.0.0.0")
    _reg_by_subnet[sub] = [t for t in _reg_by_subnet[sub] if now - t < 3600]
    if len(_reg_by_subnet[sub]) >= REG_LIMIT_PER_HOUR:
        raise HTTPException(429, "rate_limited")
    # PoW (если включён)
    if POW_BITS > 0:
        if not req.pow_challenge or _pow_challenges.get(req.pow_challenge, 0) < now:
            raise HTTPException(400, "pow_challenge_invalid")
        if not identity.pow_ok(req.pow_challenge, req.pow_nonce or "", POW_BITS):
            raise HTTPException(400, "pow_failed")
        _pow_challenges.pop(req.pow_challenge, None)
    if not req.aliases:
        raise HTTPException(422, "no_alias")
    pairs = [(a.value, a.kind) for a in req.aliases]
    hmacs = [identity.alias_hmac(v, k) for v, k in pairs]
    if len(set(hmacs)) != len(hmacs):
        raise HTTPException(409, "duplicate_alias_in_request")
    for h in hmacs:
        if h in aliases:
            raise HTTPException(409, "alias_taken")
    secret = identity.new_secret()
    aid = identity.account_id(secret)
    idaccounts[aid] = {"created_at": int(now),
                       "password_hash": identity.hash_password(req.password) if req.password else None,
                       "reputation": 0, "tier": "free"}
    for h in hmacs:
        aliases[h] = aid
    _ensure_referral_fields(aid)
    # обработка инвайта: ребро графа + награда обоим (выплата инвайтеру метится лимитом)
    if req.invite:
        inviter = next((a for a, d in idaccounts.items()
                        if d.get("invite_code") == req.invite and a != aid), None)
        if inviter:
            idaccounts[aid]["invited_by"] = inviter
            idaccounts[inviter].setdefault("referrals", []).append(aid)
            reg = DEFAULT_ECONOMY["referral"]["register"]
            _grant_days(aid, reg["invitee"], "ref_register")
            if _can_payout(inviter):
                _grant_days(inviter, reg["inviter"], "ref_register")
                _record_payout(inviter)
    _save_idaccounts(); _save_aliases()
    _reg_by_subnet[sub].append(now)
    did = _register_device(aid, req.device)
    token = _mint_session(aid, pairs[0][0], device_id=did)
    return {"token": token, "account_id": aid, "recovery_code": identity.recovery_code(secret),
            "device_id": did, "invite_code": idaccounts[aid]["invite_code"],
            "subscription": {"plan": "free", "maxConcurrentSessions": 1}}

@app.post("/v1/account/login")
def account_login(req: LoginReq):
    aid = None
    if req.recovery_code:
        try:
            cand = identity.account_id(identity.secret_from_recovery(req.recovery_code))
            aid = cand if cand in idaccounts else None
        except Exception:
            aid = None
    elif req.alias:
        aid = aliases.get(identity.alias_hmac(req.alias.value, req.alias.kind))
        if aid:
            acc = idaccounts[aid]
            if acc.get("password_hash") and not (
                    req.password and identity.verify_password(req.password, acc["password_hash"])):
                aid = None
    if not aid:
        raise HTTPException(401, "login_failed")
    did = _register_device(aid, req.device)
    return {"token": _mint_session(aid, "user", device_id=did), "account_id": aid, "device_id": did}

@app.post("/v1/account/recover")
def account_recover(req: LoginReq):
    return account_login(req)


# ── 1c. Устройства ────────────────────────────────────────────────────────────
class DeviceOp(BaseModel):
    device_id: str

@app.get("/v1/account/devices")
def list_devices(token: str = Depends(auth)):
    aid = _account_of(token)
    cur = accounts[token].get("device_id")
    out = [{"id": k, "name": v["name"], "platform": v["platform"], "role": v["role"],
            "revoked": v["revoked"], "lastSeen": v["last_seen"], "current": k == cur}
           for k, v in devices.items() if v["account_id"] == aid]
    out.sort(key=lambda d: (not d["current"], d["revoked"], -d["lastSeen"]))
    return {"devices": out}

@app.post("/v1/account/devices/revoke")
def revoke_device(req: DeviceOp, token: str = Depends(auth)):
    if not _is_owner(token):
        raise HTTPException(403, "owner_required")
    d = devices.get(req.device_id)
    if not d or d["account_id"] != _account_of(token):
        raise HTTPException(404, "no_device")
    d["revoked"] = True; _save_devices()
    return {"ok": True, "device_id": req.device_id}

@app.post("/v1/account/devices/promote")
def promote_device(req: DeviceOp, token: str = Depends(auth)):
    """Повысить устройство до owner-class (тогда оно тоже сможет управлять списком)."""
    if not _is_owner(token):
        raise HTTPException(403, "owner_required")
    d = devices.get(req.device_id)
    if not d or d["account_id"] != _account_of(token):
        raise HTTPException(404, "no_device")
    d["role"] = "owner"; _save_devices()
    return {"ok": True, "device_id": req.device_id, "role": "owner"}


# ── Серверная экономика (тарифы/цены/награды/репутация — рендерятся в сервисе) ──
DEFAULT_ECONOMY = {
    "tiers": [
        {"id": "free", "name": {"ru": "Free", "en": "Free"}, "devices": 2,
         "features": {"relay": "limited", "speed": "basic", "locations": 2, "dedicated_ip": False, "game_mode": False}},
        {"id": "premium", "name": {"ru": "Premium", "en": "Premium"}, "devices": 0,  # 0 = без лимита
         "features": {"relay": "full", "speed": "full", "locations": "all", "dedicated_ip": False, "game_mode": True}},
        {"id": "ultimate", "name": {"ru": "Ultimate", "en": "Ultimate"}, "devices": 0,
         "features": {"relay": "full", "speed": "max", "locations": "all+premium", "dedicated_ip": True, "game_mode": True}},
    ],
    "prices": {
        "ru": {"premium": {"month": 199, "year": 1490}, "ultimate": {"month": 349, "year": 2790}, "currency": "RUB"},
        "intl": {"premium": {"month": 2.99, "year": 24.99}, "ultimate": {"month": 4.99, "year": 39.99}, "currency": "USD"},
        "balance": {"ru": {"100h": 149, "300h": 399}, "intl": {"100h": 1.99, "300h": 4.99}},
        "crypto_discount": 0.10,
    },
    "referral": {"register": {"inviter": 3, "invitee": 3},
                 "premium_month": {"inviter": 30, "invitee": 10},
                 "premium_year": {"inviter": 90, "invitee": 30},
                 "ultimate": {"inviter": 120, "invitee": 40}},
    "combo": {"thresholds_months": [3, 6, 12], "bonus_days": [7, 20, 45]},
    "reputation": [
        {"level": "new", "name": {"ru": "Новичок", "en": "New"}, "payout_cap_month": 5, "multiplier": 1.0},
        {"level": "verified", "name": {"ru": "Проверенный", "en": "Verified"}, "payout_cap_month": 20, "multiplier": 1.0},
        {"level": "established", "name": {"ru": "Опытный", "en": "Established"}, "payout_cap_month": 100, "multiplier": 1.25},
        {"level": "ambassador", "name": {"ru": "Амбассадор", "en": "Ambassador"}, "payout_cap_month": 0, "multiplier": 1.5},
    ],
    # МИР, Visa/Mastercard и крипта — по требованию; СБП/ЮMoney под РФ.
    "payments": {"ru": ["sbp", "mir", "visa", "mastercard", "yoomoney", "crypto"],
                 "intl": ["visa", "mastercard", "crypto"]},
    # Колесо: бесплатный ежедневный бонус. Результат ДЕТЕРМИНИРОВАН (provably fair):
    # зависит от аккаунта+даты+сид — нельзя переролить/накрутить. Призы — только дни,
    # без вывода в деньги (промо-механика, не лотерея).
    "wheel": {"segments": [
        {"kind": "days", "amount": 1}, {"kind": "days", "amount": 2},
        {"kind": "days", "amount": 1}, {"kind": "days", "amount": 3},
        {"kind": "days", "amount": 1}, {"kind": "days", "amount": 2},
    ]},
}

@app.get("/v1/config/economy")
def economy():
    """Все числа экономики — отсюда; приложение рендерит тарифы/награды/репутацию из этого."""
    if os.path.exists("economy.json"):
        try:
            with open("economy.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[economy] economy.json не загружен: {e}")
    return DEFAULT_ECONOMY


# ── Оплата, подписка, баланс активного времени, гранты (Этап 3) ────────────────
# Каталог продуктов. period — дни; balance — активные минуты (списываются узлом
# ТОЛЬКО под подключением). Цены — для справки/sandbox; правда живёт в economy.
PRODUCTS = {
    "premium_month":  {"tier": "premium",  "add_days": 30},
    "premium_year":   {"tier": "premium",  "add_days": 365},
    "ultimate_month": {"tier": "ultimate", "add_days": 30},
    "ultimate_year":  {"tier": "ultimate", "add_days": 365},
    "balance_100h":   {"tier": "premium",  "add_minutes": 100 * 60},
    "balance_300h":   {"tier": "premium",  "add_minutes": 300 * 60},
}
# МИР, Visa, Mastercard, СБП, ЮMoney, крипта.
ALLOWED_METHODS = {"sbp", "mir", "visa", "mastercard", "yoomoney", "crypto"}
SANDBOX_PAY = os.environ.get("SYMBIONT_SANDBOX_PAY", "1") == "1"

# Платежи: payment_id → {account_id, product, method, status, created_at, …}.
# status ∈ pending | completed | failed. Персистентно (переживает рестарт).
PAYMENTS_FILE = "payments.json"
payments: dict[str, dict] = persist.jload(PAYMENTS_FILE, {})
def _save_payments(): persist.jsave(PAYMENTS_FILE, payments)

def _ledger_add(aid: str, kind: str, **fields):
    """Записать движение баланса/подписки в журнал аккаунта (История начислений).
    kind ∈ purchase | grant | wheel | ref_earn | debit. Только идентити-слой (aid)."""
    idaccounts.setdefault(aid, {}).setdefault("ledger", []).append(
        {"at": int(time.time()), "kind": kind, **fields})

def _sub(aid: str) -> dict:
    acc = idaccounts[aid]
    if "sub" not in acc:
        acc["sub"] = {"tier": "free", "mode": "period", "days_left": 0,
                      "active_minutes_left": 0, "auto_renew": False, "source": None}
    return acc["sub"]

# ── Репутация и реферальные выплаты ────────────────────────────────────────────
def _reputation(aid: str):
    """Уровень + месячный лимит выплачиваемых рефералов (0 = без лимита)."""
    acc = idaccounts.get(aid, {})
    age_days = (time.time() - acc.get("created_at", time.time())) / 86400
    paid = acc.get("paid_months", 0)
    refs = acc.get("referrals", [])
    paying = len([r for r in refs if idaccounts.get(r, {}).get("paid_months", 0) > 0])
    if paying >= 20:
        lvl, cap, mult = "ambassador", 0, 1.5
    elif age_days >= 90 and len(refs) >= 3:
        lvl, cap, mult = "established", 100, 1.25
    elif paid >= 1 or age_days >= 14:
        lvl, cap, mult = "verified", 20, 1.0
    else:
        lvl, cap, mult = "new", 5, 1.0
    return lvl, cap, mult

def _can_payout(aid: str) -> bool:
    _lvl, cap, _m = _reputation(aid)
    if cap == 0:
        return True
    now = time.time()
    recent = [t for t in idaccounts[aid].get("payout_log", []) if now - t < 30 * 86400]
    idaccounts[aid]["payout_log"] = recent
    return len(recent) < cap

def _record_payout(aid: str):
    idaccounts[aid].setdefault("payout_log", []).append(time.time())

def _grant_days(aid: str, days: int, reason: str, tier: str = "premium"):
    """Начислить бонус-дни (премиум-время). Free поднимается до premium на срок."""
    sub = _sub(aid)
    sub["mode"] = "period"; sub["days_left"] += days
    if sub["tier"] == "free":
        sub["tier"] = tier
    idaccounts[aid]["tier"] = sub["tier"]
    idaccounts[aid].setdefault("bonus_log", []).append(
        {"days": days, "reason": reason, "at": int(time.time())})
    # журнал: колесо / реферальная награда / прочий грант
    kind = "wheel" if reason == "wheel" else "ref_earn" if reason.startswith("ref") else "grant"
    _ledger_add(aid, kind, days=days, reason=reason)

def _ensure_referral_fields(aid: str):
    acc = idaccounts[aid]
    acc.setdefault("invite_code", "SYM-" + b32(secrets.token_bytes(5))[:6])
    acc.setdefault("referrals", [])
    acc.setdefault("paid_months", 0)

def _apply_purchase(aid: str, product: str, method: str) -> dict:
    """Начислить эффект оплаченного продукта на подписку аккаунта. Общий путь для
    sandbox-подтверждения и вебхука провайдера. Идемпотентность — на вызывающем
    (по статусу платежа). Пишет запись в журнал (ledger)."""
    p = PRODUCTS[product]
    sub = _sub(aid)
    if "add_minutes" in p:
        sub["mode"] = "balance"; sub["active_minutes_left"] += p["add_minutes"]
        _ledger_add(aid, "purchase", product=product, method=method,
                    minutes=p["add_minutes"], tier=p["tier"])
    else:
        sub["mode"] = "period"; sub["days_left"] += p["add_days"]
        _ledger_add(aid, "purchase", product=product, method=method,
                    days=p["add_days"], tier=p["tier"])
    sub["tier"] = p["tier"]; sub["source"] = method
    idaccounts[aid]["tier"] = p["tier"]
    # комбо: копим оплаченные месяцы, бонус на порогах 3/6/12
    add_months = {"premium_month": 1, "ultimate_month": 1,
                  "premium_year": 12, "ultimate_year": 12}.get(product, 0)
    if add_months:
        _ensure_referral_fields(aid)
        before = idaccounts[aid].get("paid_months", 0)
        after = before + add_months
        idaccounts[aid]["paid_months"] = after
        combo = DEFAULT_ECONOMY["combo"]
        for thr, bonus in zip(combo["thresholds_months"], combo["bonus_days"]):
            if before < thr <= after:
                _grant_days(aid, bonus, f"combo_{thr}m")
        # реферальная награда инвайтеру за покупку (метится лимитом репутации)
        inviter = idaccounts[aid].get("invited_by")
        if inviter and inviter in idaccounts and _can_payout(inviter):
            ref = DEFAULT_ECONOMY["referral"]
            key = ("premium_year" if product == "premium_year" else
                   "ultimate" if "ultimate" in product else "premium_month")
            r = ref.get(key, ref["premium_month"])
            _grant_days(inviter, r["inviter"], f"ref_{key}")
            _grant_days(aid, r["invitee"], f"ref_bonus_{key}")
            _record_payout(inviter)
    _save_idaccounts()
    return sub

class PurchaseReq(BaseModel):
    product: str
    method: str

@app.post("/v1/billing/purchase")
def purchase(req: PurchaseReq, token: str = Depends(auth)):
    """PaymentProvider-абстракция. В dev (sandbox) платёж авто-подтверждается;
    в проде возвращаем pending — подтверждение придёт вебхуком (/v1/billing/webhook)."""
    aid = _account_of(token)
    if not aid:
        raise HTTPException(400, "no_account")
    if req.product not in PRODUCTS:
        raise HTTPException(422, "unknown_product")
    if req.method not in ALLOWED_METHODS:
        raise HTTPException(422, "unknown_method")
    pay_id = b32(secrets.token_bytes(8))
    now = int(time.time())
    rec = {"payment_id": pay_id, "account_id": aid, "product": req.product,
           "method": req.method, "created_at": now}
    if not SANDBOX_PAY:
        # боевой провайдер вернул бы ссылку/QR; статус придёт вебхуком.
        rec["status"] = "pending"
        payments[pay_id] = rec; _save_payments()
        return {"payment_id": pay_id, "status": "pending", "method": req.method}
    sub = _apply_purchase(aid, req.product, req.method)
    rec.update(status="completed", completed_at=now, sandbox=True)
    payments[pay_id] = rec; _save_payments()
    return {"payment_id": pay_id, "status": "completed", "sandbox": True,
            "method": req.method, "subscription": sub}

@app.get("/v1/billing/payment/{payment_id}")
def payment_status(payment_id: str, token: str = Depends(auth)):
    """Статус конкретного платежа (для экрана «Платёж обрабатывается» → «Оплачено»)."""
    aid = _account_of(token)
    pay = payments.get(payment_id)
    if not pay or pay.get("account_id") != aid:
        raise HTTPException(404, "no_payment")
    return {k: v for k, v in pay.items() if k != "account_id"}

@app.post("/v1/billing/webhook")
async def billing_webhook(request: Request):
    """Колбэк платёжного провайдера: подтверждает (или проваливает) pending-платёж.
    Подпись — HMAC-SHA256 сырого тела на PAY_WEBHOOK_SECRET. Идемпотентно
    (повторная доставка того же события ничего не начисляет дважды)."""
    raw = await request.body()
    sig = request.headers.get("x-pay-sig", "")
    expect = hmac.new(PAY_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        raise HTTPException(401, "bad_pay_sig")
    try:
        data = json.loads(raw)
    except Exception:
        raise HTTPException(422, "bad_json")
    pay = payments.get(data.get("payment_id"))
    if not pay:
        raise HTTPException(404, "no_payment")
    if pay["status"] == "completed":
        return {"ok": True, "status": "completed", "idempotent": True}
    if data.get("status") == "failed":
        pay.update(status="failed", completed_at=int(time.time()))
        _save_payments()
        return {"ok": True, "status": "failed"}
    sub = _apply_purchase(pay["account_id"], pay["product"], pay["method"])
    pay.update(status="completed", completed_at=int(time.time()))
    _save_payments()
    return {"ok": True, "status": "completed", "subscription": sub}

@app.get("/v1/billing/ledger")
def billing_ledger(token: str = Depends(auth)):
    """История начислений/списаний (покупки, гранты, колесо, рефералы, расход времени)."""
    aid = _account_of(token)
    if not aid:
        raise HTTPException(400, "no_account")
    entries = sorted(idaccounts[aid].get("ledger", []),
                     key=lambda e: e.get("at", 0), reverse=True)
    return {"entries": entries, "subscription": _sub(aid)}

@app.get("/v1/billing/status")
def billing_status(token: str = Depends(auth)):
    aid = _account_of(token)
    if not aid:
        raise HTTPException(400, "no_account")
    return {"account_id": aid, "subscription": _sub(aid),
            "granted_tier": idaccounts[aid].get("granted_tier")}

class NodeSession(BaseModel):
    account_id: str
    minutes: int

@app.post("/v1/node/session")
async def node_session(request: Request):
    """Узел рапортует длительность активной сессии → списываем баланс активного
    времени. Узнаём длительность, НЕ содержимое трафика (приватность). Подпись —
    ПЕР-УЗЛОВЫМ секретом (выдан при регистрации), узел опознаётся по node_id."""
    raw = await request.body()
    data = json.loads(raw)
    node_id = data.get("node_id")
    node_secret = _node_secrets.get(node_id) if node_id else None
    if not node_secret:
        raise HTTPException(403, "unknown_node")
    expect = hmac.new(node_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("x-node-sign", ""), expect):
        raise HTTPException(403, "bad_node_sign")
    aid, minutes = data.get("account_id"), int(data.get("minutes", 0))
    if aid not in idaccounts:
        raise HTTPException(404, "no_account")
    sub = _sub(aid)
    if sub["mode"] == "balance":
        sub["active_minutes_left"] = max(0, sub["active_minutes_left"] - minutes)
        if minutes:
            _ledger_add(aid, "debit", minutes=minutes)
        _save_idaccounts()
    return {"ok": True, "active_minutes_left": sub["active_minutes_left"]}

class GrantReq(BaseModel):
    account_id: str
    tier: str = "ultimate"
    days: Optional[int] = None     # None = пожизненно
    reason: str = "owner_grant"

@app.post("/v1/admin/grant")
def admin_grant(req: GrantReq, _: None = Depends(admin)):
    """Грант премиума по решению владельца (не покупается массово)."""
    if req.account_id not in idaccounts:
        raise HTTPException(404, "no_account")
    g = {"tier": req.tier, "expires": req.days, "source": "owner_grant", "reason": req.reason}
    idaccounts[req.account_id]["granted_tier"] = g
    idaccounts[req.account_id]["tier"] = req.tier
    _save_idaccounts()
    return {"ok": True, "granted_tier": g}


# ── Рефералы и репутация (видны пользователю) ─────────────────────────────────
@app.get("/v1/referral")
def referral_info(token: str = Depends(auth)):
    aid = _account_of(token)
    _ensure_referral_fields(aid)
    acc = idaccounts[aid]
    lvl, cap, mult = _reputation(aid)
    now = time.time()
    used = len([t for t in acc.get("payout_log", []) if now - t < 30 * 86400])
    refs = acc.get("referrals", [])
    converted = len([r for r in refs if idaccounts.get(r, {}).get("paid_months", 0) > 0])
    rep_meta = next((x for x in DEFAULT_ECONOMY["reputation"] if x["level"] == lvl), {})
    return {"invite_code": acc.get("invite_code"),
            "reputation": {"level": lvl, "name": rep_meta.get("name", {}),
                           "payout_cap_month": cap, "multiplier": mult,
                           "payouts_used": used,
                           "payouts_left": (None if cap == 0 else max(0, cap - used))},
            "invited": len(refs), "converted": converted}

class FraudReq(BaseModel):
    account_id: str

@app.post("/v1/admin/fraud")
def admin_fraud(req: FraudReq, _: None = Depends(admin)):
    """Откат по инвайт-графу: помечаем аккаунт и ВСЮ его реферальную ветку как фрод,
    обнуляем им премиум. Так ферма, размножавшая бонусы, схлопывается целиком."""
    if req.account_id not in idaccounts:
        raise HTTPException(404, "no_account")
    flagged = []
    def walk(a):
        acc = idaccounts.get(a)
        if not acc or acc.get("fraud"):
            return
        acc["fraud"] = True
        s = _sub(a); s["days_left"] = 0; s["active_minutes_left"] = 0; s["tier"] = "free"
        acc["tier"] = "free"
        flagged.append(a)
        for r in list(acc.get("referrals", [])):
            walk(r)
    walk(req.account_id)
    _save_idaccounts()
    return {"ok": True, "flagged": flagged, "count": len(flagged)}


# ── Колесо фортуны: бесплатный ежедневный бонус (provably fair) ────────────────
import datetime as _dt

def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

def _wheel_segments():
    return DEFAULT_ECONOMY.get("wheel", {}).get("segments", [])

def _wheel_index(aid: str, date: str) -> int:
    """Детерминированный индекс: hash(аккаунт:дата:сид). Один и тот же для пары —
    переролить нельзя, накрутить нельзя, можно проверить (provably fair)."""
    segs = _wheel_segments()
    if not segs:
        return 0
    h = hashlib.sha256(f"{aid}:{date}:{WHEEL_SEED}".encode()).hexdigest()
    return int(h, 16) % len(segs)

@app.get("/v1/wheel")
def wheel_info(token: str = Depends(auth)):
    aid = _account_of(token)
    w = idaccounts[aid].get("wheel", {})
    spun = w.get("last_date") == _today()
    return {"segments": _wheel_segments(), "available": not spun,
            "today_index": w.get("last_index") if spun else None,
            "last_prize": w.get("last_prize") if spun else None}

@app.post("/v1/wheel/spin")
def wheel_spin(token: str = Depends(auth)):
    aid = _account_of(token)
    if not aid:
        raise HTTPException(400, "no_account")
    today = _today()
    if idaccounts[aid].get("wheel", {}).get("last_date") == today:
        raise HTTPException(409, "already_spun_today")
    idx = _wheel_index(aid, today)
    seg = _wheel_segments()[idx]
    if seg["kind"] == "days":
        _grant_days(aid, seg["amount"], "wheel")   # ledger('wheel') внутри _grant_days
    else:
        sub = _sub(aid); sub["active_minutes_left"] += seg["amount"]
        _ledger_add(aid, "wheel", minutes=seg["amount"])
    idaccounts[aid]["wheel"] = {"last_date": today, "last_index": idx, "last_prize": seg}
    _save_idaccounts()
    return {"index": idx, "prize": seg, "subscription": _sub(aid)}


# ── 2. Оплата ключами ─────────────────────────────────────────────────────────
@app.post("/v1/key/redeem")
def redeem(req: RedeemReq, token: str = Depends(auth)):
    try:
        res = issuer.redeem(req.code, account_token=token)
    except KeyError_ as e:
        code = str(e)
        status = {"key_already_redeemed": 409, "key_revoked": 410,
                  "key_expired": 410, "key_invalid": 422}.get(code, 422)
        raise HTTPException(status, code)
    a = accounts[token]
    base = datetime.now(timezone.utc)
    if a["paid_until"]:
        cur = datetime.fromisoformat(a["paid_until"])
        if cur > base: base = cur
    paid_until = base + timedelta(days=res["grant_days"])
    a["plan"] = res["plan"] if res["plan"] != "topup" else a["plan"]
    a["paid_until"] = paid_until.isoformat()
    a["max_sessions"] = 5 if a["plan"] == "pro" else a["max_sessions"]
    _save_accounts()
    return {"plan": a["plan"], "paidUntil": a["paid_until"],
            "added": {"days": res["grant_days"]}, "idempotent": res["idempotent"]}


# ── 2b. Проверка ключа БЕЗ активации (публичный game-style чекер) ─────────────
# Не требует авторизации: можно звать с публичного сайта/лендинга до входа.
# Ничего не мутирует — только читает подпись и реестр. Статусы для UI:
# valid | already_redeemed | expired | revoked | not_found | invalid.
@app.post("/v1/key/check")
def key_check(req: RedeemReq):
    return issuer.check(req.code)


# ── 3. Подписанный манифест ───────────────────────────────────────────────────
@app.get("/v1/manifest")
def manifest(since: int = 0):
    _sync_manifest_health()   # ленивый свип протухших/вернувшихся узлов
    if since >= MANIFEST_VERSION:
        raise HTTPException(304, "not_modified")
    return _manifest_signed

@app.get("/v1/pubkey")
def pubkey():
    # клиент вшивает ключ заранее; эндпоинт — для удобства разработки
    return {"ed25519": issuer.public_key_b64()}


# ── 3b. Саморегистрация узла (узел сам вписывается в манифест) ────────────────
# Узел при старте (bootstrap) шлёт свою публичную запись (manifest_node.json),
# подписанную HMAC-SHA256 на секрете оператора (NODE_SECRET). Бэкенд проверяет
# подпись (защита от чужой регистрации/Sybil), прогоняет юр-предохранитель и
# upsert'ит узел в nodes.json → манифест пересобирается и переподписывается.
# Это убирает ручной publish_nodes: новый VPS появляется в сети сам.
@app.post("/v1/node/register")
async def node_register(request: Request):
    global _manifest_signed
    raw = await request.body()
    sig = request.headers.get("x-node-sig", "")
    expect = hmac.new(NODE_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        raise HTTPException(401, "bad_node_sig")
    try:
        node = json.loads(raw)
    except Exception:
        raise HTTPException(422, "bad_json")
    for k in ("id", "country", "code", "protocols", "host", "transport"):
        if k not in node:
            raise HTTPException(422, f"missing_{k}")
    # защита приватного ключа и юр-предохранитель (как в publish_nodes)
    if "reality_private" in raw.decode("utf-8", "ignore") or "private_key" in raw.decode("utf-8", "ignore"):
        raise HTTPException(400, "private_key_leak")
    roles = node.get("roles") or []
    if (node.get("code") or "").strip().upper() == "RU" and "relay" not in roles:
        raise HTTPException(403, "ru_exit_forbidden")  # публичный exit в РФ запрещён
    # upsert по id в nodes.json (персистентно)
    data = persist.jload("nodes.json", {"nodes": []})
    cur = data.get("nodes") if isinstance(data, dict) else data
    cur = [n for n in (cur or []) if n.get("id") != node["id"]]
    cur.append(node)
    persist.jsave("nodes.json", {"nodes": cur})
    # регистрация = свежий узел живой; заводим health, чтобы свип дал ему grace-период
    _node_health[node["id"]] = {"last_seen": int(time.time()),
                                "load_pct": node.get("loadPct", node.get("load_pct"))}
    _save_node_health()
    _bump_and_rebuild()                    # набор узлов изменился → версия+подпись
    # выдаём узлу ЕГО собственный секрет для дальнейших вызовов (node/session, heartbeat)
    node_secret = _node_secrets.get(node["id"]) or b32(secrets.token_bytes(24))
    _node_secrets[node["id"]] = node_secret
    persist.jsave("node_secrets.json", _node_secrets)
    return {"ok": True, "id": node["id"], "total_nodes": len(cur),
            "version": MANIFEST_VERSION, "node_secret": node_secret,
            "stale_after_sec": NODE_STALE_SEC}


# ── 3c. Heartbeat узла (само-починка: живой → в манифесте, замолчал → выпал) ───
@app.post("/v1/node/heartbeat")
async def node_heartbeat(request: Request):
    """Узел раз в ~минуту рапортует «я жив» (+ опц. load_pct). Подпись — ПЕР-УЗЛОВЫМ
    секретом (как /v1/node/session). Нет heartbeat дольше NODE_STALE_SEC → узел
    выпадает из манифеста; вернулся → снова появляется (и версия манифеста растёт)."""
    raw = await request.body()
    try:
        data = json.loads(raw)
    except Exception:
        raise HTTPException(422, "bad_json")
    node_id = data.get("node_id")
    node_secret = _node_secrets.get(node_id) if node_id else None
    if not node_secret:
        raise HTTPException(403, "unknown_node")
    expect = hmac.new(node_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("x-node-sign", ""), expect):
        raise HTTPException(403, "bad_node_sign")
    h = _node_health.setdefault(node_id, {})
    h["last_seen"] = int(time.time())
    lp = data.get("load_pct")
    if isinstance(lp, (int, float)):
        h["load_pct"] = max(0, min(100, int(lp)))
    _save_node_health()
    revived = _sync_manifest_health()      # если узел вернулся из протухших — обновит манифест
    return {"ok": True, "revived": revived, "stale_after_sec": NODE_STALE_SEC,
            "version": MANIFEST_VERSION}


# ── 4. Поддержка (диалог по токену) ──────────────────────────────────────────
@app.get("/v1/support/thread")
def get_thread(token: str = Depends(auth)):
    return {"messages": threads.get(token, [])}

@app.post("/v1/support/message")
def post_message(req: MsgReq, token: str = Depends(auth)):
    threads.setdefault(token, []).append({"from": "user", "text": req.text, "at": _now()})
    # авто-ответ-заглушка (в бою — очередь для роли «Поддержка»)
    threads[token].append({"from": "support",
        "text": "Спасибо, видим обращение. Попробуйте «Починить интернет» — это часто решает за 15 секунд.",
        "at": _now()})
    _save_threads()
    return {"ok": True}


# ── 5. Аттестация устройства (анти-абьюз) ─────────────────────────────────────
@app.post("/v1/attest")
def attest(req: AttestReq, token: str = Depends(auth)):
    # Здесь проверялся бы App Attest / Play Integrity payload. Личность НЕ раскрывается.
    ok = bool(req.token_blob)  # заглушка
    return {"ok": ok, "deviceClass": "genuine" if ok else "unknown"}


# ── админ: выпуск ключей (для демо/реселлеров) ───────────────────────────────
@app.post("/v1/admin/issue")
def admin_issue(req: IssueReq, _: None = Depends(admin)):
    code = issuer.issue(plan=req.plan, grant_days=req.grant_days, uses=req.uses)
    return {"code": code}


# ── админ: canary-выкатка манифеста (операторский рычаг 1%→5%→25%→100%) ───────
# Меняет ДОЛЮ клиентов, которым достанется текущая версия манифеста. Клиент сам
# решает, попал ли он в волну, по стабильному хешу своего токена (см. app_state).
# Версию НЕ двигает (это та же версия-кандидат, просто шире волна). Откат =
# выкатить новую версию с прежним содержимым (монотонность на клиенте не даст
# откатиться назад по номеру). rollout — в подписанном теле, подделать нельзя.
class RolloutReq(BaseModel):
    percent: int

@app.post("/v1/admin/rollout")
def admin_rollout(req: RolloutReq, _: None = Depends(admin)):
    global ROLLOUT, _manifest_signed
    ROLLOUT = max(0, min(100, int(req.percent)))
    _manifest_signed = _build_manifest()
    return {"rollout": ROLLOUT, "version": MANIFEST_VERSION}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
