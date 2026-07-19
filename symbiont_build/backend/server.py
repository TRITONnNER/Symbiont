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
import os, json, secrets, base64, time, threading, tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends, Response
from pydantic import BaseModel, Field

# Порядок тиров (ранг). Гранты/ключи ПОВЫШАЮТ тир, но никогда не понижают:
# премиум-пользователь, погасивший ultimate-ключ, становится ultimate.
_TIER_RANK = {"free": 0, "premium": 1, "ultimate": 2}

# Сериализация read-modify-write над общими in-memory словарями. Синхронные
# (`def`) эндпоинты FastAPI исполняются в общем пуле потоков, поэтому «проверить,
# затем начислить» без лока допускает гонку (напр. двойной спин колеса за день).
_state_lock = threading.RLock()

def _atomic_write_json(path: str, obj) -> None:
    """Атомарная запись JSON: temp-файл в том же каталоге → fsync → os.replace. Краш или
    диск-фул посреди записи не оставит обрезанный файл (иначе economy.json/flags.json
    повреждается, а _eff_economy()/flags() тихо откатываются на дефолт — цены/флаги
    молча сбрасываются)."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

import hmac, hashlib
from fastapi import Request
import logging
import persist
import identity
import log_setup
from keys import Issuer, KeyError_, load_or_create_signing_key, b32
from manifest import demo_manifest, sign_manifest

# Единый логгер бэкенда (stdout + ротируемый файл). Уровень — SYMBIONT_LOG_LEVEL.
log = log_setup.setup("symbiont.backend")

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
    log.info("недостающие секреты сгенерированы и сохранены в secrets.json (НЕ коммитить, держать вне репо)")
# PROD-режим: боевое развёртывание. В нём клиенту НИКОГДА не отдаются демо-узлы,
# а платежи по умолчанию не «самоподтверждаются» (нужен реальный провайдер).
# Включается явным SYMBIONT_ENV=prod ИЛИ автоматически, когда рядом есть nodes.json
# (реальные узлы = боевое развёртывание). В dev/демо (без узлов) остаётся витрина.
PROD = (os.environ.get("SYMBIONT_ENV", "").strip().lower() in ("prod", "production")
        or persist.exists("nodes.json"))
MANIFEST_BASE = 184          # базовая версия (demo-узлы в dev; в prod — реальные/пусто)
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
_node_rev = 1 if persist.exists("nodes.json") else 0   # версия += при смене НАБОРА узлов

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
    if persist.exists("nodes.json"):
        # Есть nodes.json → боевое развёртывание: отдаём ТОЛЬКО реальные живые узлы
        # (даже если их 0 — например, все протухли). Демо-узлы клиенту НЕ показываем.
        live = []
        try:
            data = persist.jload("nodes.json", {"nodes": []})
            real = data.get("nodes") if isinstance(data, dict) else data
            healthy = _healthy_ids()
            for n in (real or []):
                if n.get("id") not in healthy:
                    continue                       # протухший — само-починка убрала
                h = _node_health.get(n.get("id"))
                if h and isinstance(h.get("load_pct"), int):
                    n = {**n, "loadPct": h["load_pct"]}   # живая загрузка из heartbeat
                live.append(n)
        except Exception as e:
            log.warning("nodes.json не загружен: %s", e)
        body["nodes"] = live
    elif PROD:
        # Прод без nodes.json — реальных узлов ещё нет. Честный пустой список,
        # без выдуманных демо-узлов (клиент покажет «сеть ещё не развёрнута»).
        body["nodes"] = []
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
# Лимиты неаутентифицированных/дешёвых-но-абьюзимых эндпоинтов (в час на /24):
# anon — иначе безлимитное создание аккаунтов/тредов (DoS); login — брутфорс пароля
# + CPU-усиление через scrypt; discount — перебор купонов. Все настраиваются env.
ANON_LIMIT_PER_HOUR = int(os.environ.get("SYMBIONT_ANON_LIMIT", "30"))
LOGIN_LIMIT_PER_HOUR = int(os.environ.get("SYMBIONT_LOGIN_LIMIT", "30"))
DISCOUNT_LIMIT_PER_HOUR = int(os.environ.get("SYMBIONT_DISCOUNT_LIMIT", "60"))
_pow_challenges: dict[str, float] = {}
_reg_by_subnet: dict[str, list] = collections.defaultdict(list)
_rl_buckets: dict[str, list] = {}

def _subnet24(ip: str) -> str:
    p = ip.split("."); return ".".join(p[:3]) if len(p) == 4 else ip

def _rate_limit(name: str, request: "Request", limit: int, window: int = 3600):
    """Скользящее окно попыток по /24-подсети. 429 при превышении. Пустые/просроченные
    ключи не копятся: чистим на каждом обращении (иначе словарь рос бы бесконечно)."""
    now = time.time()
    ip = request.client.host if request.client else "0.0.0.0"
    key = f"{name}:{_subnet24(ip)}"
    hits = [t for t in _rl_buckets.get(key, ()) if now - t < window]
    if len(hits) >= limit:
        _rl_buckets[key] = hits
        raise HTTPException(429, "rate_limited")
    hits.append(now)
    _rl_buckets[key] = hits

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

# Предел размера тела запроса. Несколько эндпоинтов (node/register, node/heartbeat,
# billing/webhook, node/authorize) читают тело ЦЕЛИКОМ в память ДО проверки подписи;
# без предела неаутентифицированный multi-GB body раздувал бы RAM. Тела API маленькие
# (JSON), 256 KiB с запасом. Отвергаем по Content-Length (быстрый путь до чтения тела).
MAX_BODY_BYTES = int(os.environ.get("SYMBIONT_MAX_BODY", str(256 * 1024)))

@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            n = int(cl)
        except ValueError:
            return Response('{"detail":"bad_content_length"}', status_code=400, media_type="application/json")
        if n > MAX_BODY_BYTES:
            return Response('{"detail":"payload_too_large"}', status_code=413, media_type="application/json")
    return await call_next(request)


# ── Сквозное логирование запросов + необработанных ошибок ─────────────────────
# Каждый запрос: метод · путь · код · время. Любое НЕОБРАБОТАННОЕ исключение —
# с полным трейсбеком (видно, ЧТО и ГДЕ упало → как чинить). 4xx/5xx — WARNING+.
@app.middleware("http")
async def _log_requests(request: Request, call_next):
    t0 = time.monotonic()
    try:
        resp = await call_next(request)
    except Exception:
        dt = (time.monotonic() - t0) * 1000
        log.exception("НЕОБРАБОТАННАЯ ОШИБКА: %s %s (%.0fмс)",
                      request.method, request.url.path, dt)
        raise
    dt = (time.monotonic() - t0) * 1000
    lvl = logging.WARNING if resp.status_code >= 400 else logging.INFO
    log.log(lvl, "%s %s → %s (%.0fмс)", request.method, request.url.path,
            resp.status_code, dt)
    return resp


@app.on_event("startup")
async def _log_startup():
    log.info("бэкенд запущен · манифест v%s · rollout %s%% · sandbox_pay=%s · порогузла=%sс",
             MANIFEST_VERSION, ROLLOUT, SANDBOX_PAY, NODE_STALE_SEC)


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
    if not hmac.compare_digest(x_admin_token, ADMIN_TOKEN):   # константное время (не утечь секрет по таймингу)
        log.warning("отказ админ-доступа: неверный x-admin-token")
        raise HTTPException(403, "forbidden")


# ── RBAC для панели (Server-in-a-Box console): владелец + работники ────────────
# Владелец = держатель ADMIN_TOKEN (все права). Работник = именной токен с НАБОРОМ
# прав (scopes), который создаёт владелец — как выпуск ключа. Работник видит и может
# только выданные разделы (напр. только «Поддержка»). Токены работников персистентны.
# Разделы панели (scopes). Владелец = все; работнику владелец выдаёт нужные.
ALL_SCOPES = ["nodes", "users", "support", "billing", "discounts", "keys", "economy", "flags"]
SCOPE_NAMES = {"nodes": "Серверы", "users": "Пользователи", "support": "Поддержка",
               "billing": "Платежи", "discounts": "Скидки", "keys": "Ключи",
               "economy": "Экономика/цены", "flags": "Блоки"}
STAFF_FILE = "staff.json"
staff_tokens: dict[str, dict] = persist.jload(STAFF_FILE, {})  # token -> {id,name,scopes,revoked,created_at}
def _save_staff(): persist.jsave(STAFF_FILE, staff_tokens)

# ── Журнал действий панели (кто что сделал) — подотчётность работников ─────────
AUDIT_FILE = "audit.json"
audit_log: list = persist.jload(AUDIT_FILE, [])
def _audit(actor, action: str, target: str = None, **extra):
    """Записать действие оператора в журнал (последние 500). actor — Principal или имя."""
    is_p = actor.__class__.__name__ == "Principal"
    audit_log.append({"at": _now(), "action": action, "target": target,
                      "by": (actor.name if is_p else (actor or "Владелец")),
                      "role": (actor.role if is_p else "owner"), **extra})
    if len(audit_log) > 500:
        del audit_log[:len(audit_log) - 500]
    persist.jsave(AUDIT_FILE, audit_log)

class Principal:
    """Кто выполняет операцию в панели: владелец (все права) или работник (scopes)."""
    def __init__(self, role: str, scopes, name: str = None):
        self.role, self.scopes, self.name = role, list(scopes), name
    def can(self, scope: str) -> bool:
        return self.role == "owner" or scope in self.scopes

def staff(x_admin_token: str = Header(default=""),
          x_staff_token: str = Header(default="")) -> Principal:
    """Аутентификация панели: владелец по ADMIN_TOKEN, работник по staff-токену."""
    if x_admin_token and hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
        return Principal("owner", ALL_SCOPES, "Владелец")
    if x_staff_token:
        rec = staff_tokens.get(x_staff_token)
        if rec and not rec.get("revoked"):
            return Principal("worker", rec.get("scopes", []), rec.get("name"))
    log.warning("отказ панели: нет валидного admin/staff-токена")
    raise HTTPException(403, "forbidden")

def require(scope: str):
    """Зависимость: пропустить владельца ИЛИ работника с нужным правом (scope)."""
    def _dep(p: Principal = Depends(staff)) -> Principal:
        if not p.can(scope):
            raise HTTPException(403, "scope_required")
        return p
    return _dep


# ── модели запросов ───────────────────────────────────────────────────────────
class AnonReq(BaseModel):
    label: Optional[str] = None

class LabelReq(BaseModel):
    label: str = Field(max_length=200)      # метка косметическая, но без предела раздувала бы БД

class RedeemReq(BaseModel):
    code: str = Field(max_length=4000)      # разумный предел на длину кода ключа

class IssueReq(BaseModel):
    plan: str = "pro"
    grant_days: int = 30
    uses: int = 1

class MsgReq(BaseModel):
    text: str = Field(max_length=8000)      # верхний предел размера сообщения (анти-раздувание тредов)
    diag: Optional[dict] = None

class AttestReq(BaseModel):
    platform: str
    token_blob: str


# ── 1. Аккаунт без данных ─────────────────────────────────────────────────────
@app.post("/v1/account/anon")
def account_anon(req: AnonReq, request: Request):
    _rate_limit("anon", request, ANON_LIMIT_PER_HOUR)   # иначе безлимитная фарма аккаунтов/тредов
    token = b32(secrets.token_bytes(20))           # 160-битный непрозрачный токен
    accounts[token] = {"label": req.label or "guest", "plan": "free",
                       "paid_until": None, "max_sessions": 1}
    threads[_thread_key(token)] = [{"from": "support",
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
    now = time.time()
    # TTL лениво: эндпоинт публичный и без лимита, а при POW_BITS=0 register вообще
    # не удаляет челленджи — без чистки словарь рос бы бесконечно (утечка памяти).
    # Sweep запускаем только когда накопилось (амортизация O(n)); при флуде — сброс.
    if len(_pow_challenges) > 1000:
        for k in [k for k, exp in _pow_challenges.items() if exp < now]:
            _pow_challenges.pop(k, None)
        if len(_pow_challenges) > 50000:
            _pow_challenges.clear()          # предохранитель от флуда неаутентифицированными запросами
    chal = b32(secrets.token_bytes(12))
    _pow_challenges[chal] = now + 120
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
            reg = _eff_economy()["referral"]["register"]
            _grant_days(aid, reg["invitee"], "ref_register")
            if _can_payout(inviter) and not idaccounts[inviter].get("fraud"):
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
def account_login(req: LoginReq, request: Request):
    # Лимит попыток: без него — офлайн-скоростной брутфорс пароля по алиасу и
    # CPU-усиление (каждая попытка запускает серверный scrypt).
    _rate_limit("login", request, LOGIN_LIMIT_PER_HOUR)
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
            # Алиас (ник/почта/телефон) — ПУБЛИЧНЫЙ и непроверенный. Вход по нему
            # разрешён ТОЛЬКО с верным паролем. У аккаунта без пароля единственный
            # секрет — recovery-код; пускать его по одному алиасу нельзя, иначе любой,
            # кто знает ник, захватывает аккаунт.
            ok = bool(acc.get("password_hash")) and bool(req.password) and \
                identity.verify_password(req.password, acc["password_hash"])
            if not ok:
                aid = None
    if not aid:
        raise HTTPException(401, "login_failed")
    did = _register_device(aid, req.device)
    return {"token": _mint_session(aid, "user", device_id=did), "account_id": aid, "device_id": did}

@app.post("/v1/account/recover")
def account_recover(req: LoginReq, request: Request):
    return account_login(req, request)


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
        "balance": {"ru": {"20h": 39, "100h": 149, "300h": 399}, "intl": {"20h": 0.49, "100h": 1.99, "300h": 4.99}},
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

def _eff_economy() -> dict:
    """Действующая экономика: правки владельца (economy.json) поверх дефолта.
    ЕДИНЫЙ источник и для витрины, и для реальных сумм/наград — иначе цена, которую
    видит пользователь, расходится со списанием, а правки цен/наград в панели не
    влияют на списания и выплаты. economy.json всегда полный (deep-merge при записи),
    поэтому обращения по ключам безопасны."""
    if os.path.exists("economy.json"):
        try:
            with open("economy.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning("economy.json не загружен: %s", e)
    return DEFAULT_ECONOMY

@app.get("/v1/config/economy")
def economy():
    """Все числа экономики — отсюда; приложение рендерит тарифы/награды/репутацию из этого."""
    return _eff_economy()


# ── Фиче-флаги: что показывать на сайте и в приложении ─────────────────────────
# Единый источник видимости блоков для ОБОИХ поверхностей (сайт + приложение).
# Один флаг здесь → оболочка/клиент прячет блок в обеих. Переопределяется файлом
# flags.json (как economy.json), чтобы менять без пересборки. Гейтинг — fail-open:
# неизвестный/отсутствующий флаг считается ВКЛючённым (лучше лишний блок, чем пустой сайт).
DEFAULT_FLAGS = {
    "version": 1,
    # Платформы в разделе «Скачать» — гаси по готовности сборки (пример: macOS не готова).
    "download": {"ios": True, "android": True, "windows": True,
                 "macos": True, "linux": True, "extension": True},
    # Блок «Скоро» и его пункты.
    "coming_soon": {"enabled": True, "apple_tv": True, "android_tv": True,
                    "openwrt": True, "cli": True},
    # Крупные секции сайта/страницы — можно скрыть целый блок или страницу.
    "site": {"pricing": True, "download": True, "support": True, "legal": True,
             "hero_widget": True, "features": True, "numbers": True, "faq": True,
             "import_bridge": True, "lang_switcher": True},
    # Правовое — отдельные блоки, которые часто «ещё не готовы».
    # canary по умолчанию ВЫКЛ: пока нет реального процесса подписи — не показываем фейк.
    "legal": {"canary": False, "law_requests": True, "key_revocations": True,
              "jurisdictions": True},
    # Цены — какие модели показывать (баланс, годовой переключатель, крипто-скидка).
    "pricing": {"subscriptions": True, "balance": True, "balance_300h": True,
                "annual_toggle": True, "crypto_discount": True},
    # Приложение — возможности (те же флаги читает Flutter-клиент).
    "app": {"import_bridge": True, "wheel": True, "referrals": True,
            "network_analysis": True, "traffic_map": True, "key_checker": True},
}

@app.get("/v1/config/flags")
def flags():
    """Фиче-флаги видимости блоков (сайт + приложение). Единый источник — здесь;
    override — файлом flags.json (как economy.json). Клиент/оболочка читают и гейтят
    блоки. Неизвестный флаг = включён (fail-open), чтобы опечатка не гасила витрину."""
    if os.path.exists("flags.json"):
        try:
            with open("flags.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning("flags.json не загружен: %s", e)
    return DEFAULT_FLAGS

def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out

class FlagsReq(BaseModel):
    flags: dict                      # частичное или полное поддерево флагов

@app.post("/v1/admin/flags")
def admin_set_flags(req: FlagsReq, p: Principal = Depends(require("flags"))):
    """Включить/выключить блоки сайта и приложения из панели. Пишет flags.json
    (override поверх DEFAULT_FLAGS), версия +1 — клиент/сайт подхватят при опросе.
    Частичный апдейт: `{"flags":{"download":{"macos":false}}}` гасит только macOS."""
    cur = flags()
    merged = _deep_merge(cur, req.flags or {})
    merged["version"] = (cur.get("version", 1) or 1) + 1
    try:
        _atomic_write_json("flags.json", merged)
    except OSError as e:
        raise HTTPException(500, f"flags_write_failed:{e}")
    log.info("флаги обновлены из панели → v%s", merged["version"])
    _audit(p, "flags_set", changed=list((req.flags or {}).keys()), version=merged["version"])
    return {"ok": True, "flags": merged}

class EconomyReq(BaseModel):
    economy: dict                    # частичное или полное поддерево экономики

@app.get("/v1/admin/economy")
def admin_get_economy(_: Principal = Depends(require("economy"))):
    """Текущая экономика (цены/тиры/пакеты/награды) — для редактора в панели."""
    return economy()

@app.post("/v1/admin/economy")
def admin_set_economy(req: EconomyReq, p: Principal = Depends(require("economy"))):
    """Изменить цены/пакеты/награды из панели (пишет economy.json, override поверх
    DEFAULT_ECONOMY, deep-merge). Клиент/сайт рендерят тарифы из /v1/config/economy —
    смена цены здесь мгновенно меняет витрину без пересборки."""
    if not isinstance(req.economy, dict) or not req.economy:
        raise HTTPException(422, "economy_object_required")
    cur = economy()
    merged = _deep_merge(cur, req.economy)
    try:
        _atomic_write_json("economy.json", merged)
    except OSError as e:
        raise HTTPException(500, f"economy_write_failed:{e}")
    log.info("экономика обновлена из панели")
    _audit(p, "economy_set", changed=list(req.economy.keys()))
    return {"ok": True, "economy": merged}


class BridgeReq(BaseModel):
    uri: str

@app.post("/v1/config/parse-bridge")
def parse_bridge_endpoint(req: BridgeReq):
    """Разбор пользовательской ссылки «свой мост» → нормализованный узел каскада.
    Публичный и STATELESS: сервер ничего не сохраняет (секрет моста хранит клиент
    локально). Allow-list протоколов (vless/ss/hysteria2); приватные адреса и
    insecure-TLS возвращаются как warnings, а не молча."""
    import bridge_import
    try:
        node = bridge_import.parse_bridge(req.uri)
    except bridge_import.BridgeError as e:
        code = str(e)
        return {"ok": False, "error": code, "message": bridge_import.reason_text(code)}
    return {"ok": True, "node": node}


@app.get("/v1/config/discounts")
def list_discounts():
    """Активные АВТО-кампании (без кода) — для витрины, чтобы показывать «−N%».
    Коды-купоны здесь НЕ раскрываются (их проверяют по вводу через discount/check)."""
    now = int(time.time())
    out = [{"percent": d.get("percent"), "amount_off": d.get("amount_off"),
            "scope": d.get("scope"), "expires_at": d.get("expires_at"), "label": d.get("label")}
           for d in discounts.values() if not d.get("code") and disc_rules.is_active(d, now)]
    return {"campaigns": out}


# ── Оплата, подписка, баланс активного времени, гранты (Этап 3) ────────────────
# Каталог продуктов. period — дни; balance — активные минуты (списываются узлом
# ТОЛЬКО под подключением). Цены — для справки/sandbox; правда живёт в economy.
PRODUCTS = {
    "premium_month":  {"tier": "premium",  "add_days": 30},
    "premium_year":   {"tier": "premium",  "add_days": 365},
    "ultimate_month": {"tier": "ultimate", "add_days": 30},
    "ultimate_year":  {"tier": "ultimate", "add_days": 365},
    # пробный пакет: 20 ч, покупается ОДИН раз на аккаунт (max_per_account=1).
    # Чтобы сделать его постоянным (без ограничения) — убрать max_per_account.
    "balance_20h":    {"tier": "premium",  "add_minutes": 20 * 60, "max_per_account": 1},
    "balance_100h":   {"tier": "premium",  "add_minutes": 100 * 60},
    "balance_300h":   {"tier": "premium",  "add_minutes": 300 * 60},
}
# МИР, Visa, Mastercard, СБП, ЮMoney, крипта.
ALLOWED_METHODS = {"sbp", "mir", "visa", "mastercard", "yoomoney", "crypto"}
# В prod песочница выключена по умолчанию: покупки НЕ должны молча становиться
# «оплаченными» без реального провайдера. В dev — включена (удобно тестировать UI).
SANDBOX_PAY = os.environ.get("SYMBIONT_SANDBOX_PAY", "0" if PROD else "1") == "1"
# Платёжный «провайдер»: 'sandbox' — мгновенное подтверждение (кнопка «Купить» сразу
# оплачивает); 'mock' — ЗАГЛУШКА ПО РЕАЛЬНОМУ ПРИНЦИПУ: возвращает ссылку/QR и pending,
# оплата подтверждается «колбэком» провайдера (/v1/billing/mock/confirm). Боевой провайдер
# ставится сюда позже: purchase вернёт его checkoutUrl, а его вебхук — в /v1/billing/webhook.
PAY_PROVIDER = os.environ.get("SYMBIONT_PAY_PROVIDER", "sandbox")
if PROD and (SANDBOX_PAY or PAY_PROVIDER in ("sandbox", "mock")):
    log.warning("ВНИМАНИЕ: prod с ненастоящей оплатой (SANDBOX_PAY=%s, PAY_PROVIDER=%s) — "
                "покупки не берут денег. Задай реального провайдера перед приёмом платежей.",
                SANDBOX_PAY, PAY_PROVIDER)

def _price_of(product: str, region: str = "ru", method: str = "sbp") -> dict:
    """Сумма к оплате из экономики. region: 'ru'→₽, иначе 'intl'→$. Крипта даёт
    −crypto_discount. Возвращает {amount, currency}. Правда о цене — здесь (и её же
    отдаём боевому провайдеру при интеграции)."""
    pr = _eff_economy()["prices"]
    reg = "ru" if region == "ru" else "intl"
    cur = "RUB" if reg == "ru" else "USD"
    if product.startswith("balance_"):
        amount = pr["balance"][reg].get(product.split("_", 1)[1])   # '100h' / '300h'
    else:
        tier = "ultimate" if product.startswith("ultimate") else "premium"
        period = "year" if product.endswith("year") else "month"
        amount = pr[reg][tier][period]
    if amount is not None and method == "crypto":
        amount = round(amount * (1 - pr.get("crypto_discount", 0)), 2)
    return {"amount": amount, "currency": cur}

# Платежи: payment_id → {account_id, product, method, status, created_at, …}.
# status ∈ pending | completed | failed. Персистентно (переживает рестарт).
PAYMENTS_FILE = "payments.json"
payments: dict[str, dict] = persist.jload(PAYMENTS_FILE, {})
def _save_payments(): persist.jsave(PAYMENTS_FILE, payments)

# Скидки: кампании (без кода, авто) и коды (купоны). Создаёт владелец/Server-in-a-Box
# (админ-токен) — как платные ключи. Персистентно. Логика проверок — в discounts.py.
import discounts as disc_rules
DISCOUNTS_FILE = "discounts.json"
discounts: dict[str, dict] = persist.jload(DISCOUNTS_FILE, {})
def _save_discounts(): persist.jsave(DISCOUNTS_FILE, discounts)

def _purchase_count(aid: str, product: str) -> int:
    """Сколько раз аккаунт УЖЕ успешно купил этот продукт (для одноразовых пакетов)."""
    return sum(1 for p in payments.values()
               if p.get("account_id") == aid and p.get("product") == product
               and p.get("status") == "completed")

def _resolve_discount(aid: str, product: str, tier: str, code):
    """Применимая скидка: по коду (если задан) либо лучшая авто-кампания. None — нет."""
    now = int(time.time())
    if code:
        for d in discounts.values():
            if (d.get("code") or "").upper() == code.upper() and \
               disc_rules.applicable(d, aid, product, tier, now, is_code=True):
                return d
        return None
    best = None
    for d in discounts.values():
        if disc_rules.applicable(d, aid, product, tier, now, is_code=False):
            if best is None or (d.get("percent", 0) or 0) > (best.get("percent", 0) or 0):
                best = d
    return best

def _record_discount_use(disc_id: str, aid: str):
    d = discounts.get(disc_id)
    if not d:
        return
    d["used"] = d.get("used", 0) + 1
    d.setdefault("used_by", {})
    d["used_by"][aid] = d["used_by"].get(aid, 0) + 1
    _save_discounts()

def _finalize_payment(rec: dict):
    """Подтвердить платёж: начислить продукт + учесть скидку. Возвращает (sub, applied).
    applied=False, если исчерпан лимит одноразового пакета (гонка двух pending)."""
    aid, product = rec["account_id"], rec["product"]
    lim = PRODUCTS[product].get("max_per_account")
    if lim and _purchase_count(aid, product) >= lim:
        return _sub(aid), False
    sub = _apply_purchase(aid, product, rec["method"])
    if rec.get("discount_id"):
        _record_discount_use(rec["discount_id"], aid)
    return sub, True

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

def _coverage(aid: str) -> str:
    """Чем сейчас покрыт доступ аккаунта, в порядке приоритета:
       lifetime  — пожизненный грант владельца (часы НЕ тратятся);
       period    — активная подписка (дни > 0) — часы НЕ тратятся, это резерв;
       balance   — только баланс активного времени (минуты > 0) — вот его и списываем;
       none      — нечем покрыть (доступ закрыт)."""
    sub = _sub(aid)
    g = idaccounts.get(aid, {}).get("granted_tier")
    if sub.get("lifetime") or (g and g.get("expires") is None):
        return "lifetime"
    if sub.get("days_left", 0) > 0:
        return "period"
    if sub.get("active_minutes_left", 0) > 0:
        return "balance"
    return "none"

# Активные сессии узлов для баланса времени: session_token → {node_id, account_id,
# charged (накопленно списанные минуты), created_at}. Идемпотентно-кумулятивное
# списание: узел шлёт ИТОГО минут сессии, сервер вычитает только прирост. Эфемерно
# (в память): падение процесса просто закрывает сессии — узел переавторизуется.
node_sessions: dict[str, dict] = {}
NODE_SESSION_TTL = 2 * 3600      # лиз ≤30 мин; всё старше 2ч — заведомо мёртвая сессия

def _evict_node_sessions():
    """Убрать протухшие сессии (узел переавторизуется каждые ≤30 мин, получая НОВЫЙ
    токен — старые мертвы), иначе node_sessions растёт без предела. Sweep только когда
    накопилось (амортизация)."""
    if len(node_sessions) <= 1000:
        return
    cutoff = int(time.time()) - NODE_SESSION_TTL
    for st in [st for st, s in node_sessions.items() if s.get("created_at", 0) < cutoff]:
        node_sessions.pop(st, None)

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

def _raise_tier(sub: dict, tier: str) -> None:
    """Повысить тир подписки по рангу (free→premium→ultimate). Никогда не понижает —
    грант/ключ/покупка меньшего тира не сбивают уже действующий более высокий."""
    if _TIER_RANK.get(tier, 0) > _TIER_RANK.get(sub["tier"], 0):
        sub["tier"] = tier

def _grant_days(aid: str, days: int, reason: str, tier: str = "premium"):
    """Начислить бонус-дни (премиум-время). Free поднимается до premium на срок."""
    sub = _sub(aid)
    sub["mode"] = "period"; sub["days_left"] += days
    _raise_tier(sub, tier)
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
        combo = _eff_economy()["combo"]
        for thr, bonus in zip(combo["thresholds_months"], combo["bonus_days"]):
            if before < thr <= after:
                _grant_days(aid, bonus, f"combo_{thr}m")
        # реферальная награда инвайтеру за покупку (метится лимитом репутации)
        inviter = idaccounts[aid].get("invited_by")
        if inviter and inviter in idaccounts and _can_payout(inviter) and not idaccounts[inviter].get("fraud"):
            ref = _eff_economy()["referral"]
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
    region: str = "ru"                  # 'ru' → ₽, иначе → $ (определяет клиент по языку)
    discount_code: Optional[str] = None # код-купон (опционально)

@app.post("/v1/billing/purchase")
def purchase(req: PurchaseReq, token: str = Depends(auth)):
    """PaymentProvider-абстракция. ТРЕБУЕТ авторизации (login-gate: без токена аккаунта
    покупка невозможна — на сайте кнопка «Купить» ведёт в аккаунт). Режимы:
      • sandbox — платёж авто-подтверждается (кнопка «Купить» оплачивает сразу);
      • mock    — заглушка ПО РЕАЛЬНОМУ ПРИНЦИПУ: pending + checkout_url + qr,
                  подтверждение колбэком /v1/billing/mock/confirm;
      • прод    — боевой провайдер вернёт checkoutUrl, вебхук → /v1/billing/webhook.
    Учитывает одноразовые пакеты (max_per_account) и скидки (кампании/коды)."""
    aid = _account_of(token)
    if not aid:
        raise HTTPException(400, "no_account")
    if req.product not in PRODUCTS:
        raise HTTPException(422, "unknown_product")
    if req.method not in ALLOWED_METHODS:
        raise HTTPException(422, "unknown_method")
    # одноразовые пакеты (напр. пробные 20 ч): исчерпан лимит на аккаунт → отказ
    lim = PRODUCTS[req.product].get("max_per_account")
    if lim and _purchase_count(aid, req.product) >= lim:
        raise HTTPException(409, "purchase_limit_reached")
    tier = PRODUCTS[req.product]["tier"]
    base = _price_of(req.product, req.region, req.method)
    disc = _resolve_discount(aid, req.product, tier, req.discount_code)
    if req.discount_code and not disc:
        raise HTTPException(422, "discount_invalid")
    final_amount, off = disc_rules.compute(disc, base["amount"]) if disc else (base["amount"], 0)
    resp_disc = {"percent": disc.get("percent"), "off": off, "label": disc.get("label")} if disc else None

    pay_id = b32(secrets.token_bytes(8))
    now = int(time.time())
    rec = {"payment_id": pay_id, "account_id": aid, "product": req.product,
           "method": req.method, "created_at": now,
           "amount": final_amount, "base_amount": base["amount"], "currency": base["currency"]}
    if disc:
        rec["discount_id"] = disc["id"]; rec["discount_off"] = off

    if PAY_PROVIDER == "mock":
        rec["status"] = "pending"; rec["provider"] = "mock"
        payments[pay_id] = rec; _save_payments()
        return {"payment_id": pay_id, "status": "pending", "provider": "mock",
                "method": req.method, "amount": final_amount, "currency": base["currency"],
                "discount": resp_disc,
                "checkout_url": f"/v1/billing/mock/checkout/{pay_id}",
                "qr": f"SYMB-PAY:{pay_id}"}

    if not SANDBOX_PAY:
        # боевой провайдер вернул бы ссылку/QR; статус придёт вебхуком.
        rec["status"] = "pending"
        payments[pay_id] = rec; _save_payments()
        return {"payment_id": pay_id, "status": "pending", "method": req.method,
                "amount": final_amount, "currency": base["currency"], "discount": resp_disc}

    sub, applied = _finalize_payment(rec)
    rec.update(status="completed" if applied else "failed", completed_at=now, sandbox=True)
    payments[pay_id] = rec; _save_payments()
    return {"payment_id": pay_id, "status": rec["status"], "sandbox": True,
            "method": req.method, "amount": final_amount, "currency": base["currency"],
            "discount": resp_disc, "subscription": sub}

@app.get("/v1/billing/mock/checkout/{payment_id}")
def mock_checkout(payment_id: str):
    """Мок «страницы провайдера»: сумма + кнопка «Оплатить (тест)». Кнопка шлёт confirm,
    как будто пользователь оплатил у провайдера. Только для dev-режима PAY_PROVIDER=mock."""
    if PAY_PROVIDER != "mock":
        raise HTTPException(404, "not_found")   # в боевом/sandbox режиме мок-роут закрыт
    pay = payments.get(payment_id)
    if not pay:
        raise HTTPException(404, "no_payment")
    if pay.get("status") == "completed":
        html = f"<!doctype html><meta charset=utf-8><h2>Оплачено ✓</h2><p>Платёж {payment_id} подтверждён.</p>"
        return Response(content=html, media_type="text/html")
    amt, cur = pay.get("amount"), pay.get("currency", "")
    html = (f"<!doctype html><meta charset=utf-8>"
            f"<title>Оплата · Симбионт (тест)</title>"
            f"<div style='font-family:system-ui;max-width:420px;margin:60px auto;text-align:center'>"
            f"<h2>Оплата (тестовый провайдер)</h2>"
            f"<p style='font-size:28px;font-weight:700'>{amt} {cur}</p>"
            f"<p style='color:#888'>{pay.get('product')} · {pay.get('method')}</p>"
            f"<button id=pay style='padding:14px 28px;font-size:16px;border:0;border-radius:12px;"
            f"background:#34E5B0;font-weight:700;cursor:pointer'>Оплатить</button>"
            f"<p id=st style='color:#34E5B0'></p></div>"
            f"<script>document.getElementById('pay').onclick=async()=>{{"
            f"const r=await fetch('/v1/billing/mock/confirm/{payment_id}',{{method:'POST'}});"
            f"document.getElementById('st').textContent=r.ok?'Оплачено ✓ — можно вернуться в приложение':'Ошибка';"
            f"}};</script>")
    return Response(content=html, media_type="text/html")

@app.post("/v1/billing/mock/confirm/{payment_id}")
def mock_confirm(payment_id: str):
    """Эмуляция успешного колбэка провайдера: подтверждает pending-платёж и начисляет
    продукт. Идемпотентно (повторный вызов не начисляет дважды). Только dev (mock).

    ГЕЙТ: работает ТОЛЬКО при PAY_PROVIDER=='mock'. Иначе (боевой провайдер/sandbox)
    роут закрыт (404) — иначе после перехода на реального провайдера любой мог бы
    подтвердить свой pending-платёж бесплатно этим неаутентифицированным колбэком."""
    if PAY_PROVIDER != "mock":
        raise HTTPException(404, "not_found")
    pay = payments.get(payment_id)
    if not pay:
        raise HTTPException(404, "no_payment")
    if pay.get("status") in ("completed", "failed"):
        return {"ok": True, "status": pay["status"], "idempotent": True}
    sub, applied = _finalize_payment(pay)
    pay.update(status="completed" if applied else "failed", completed_at=int(time.time()))
    _save_payments()
    return {"ok": True, "status": pay["status"], "subscription": sub}

class DiscountCheckReq(BaseModel):
    code: str
    product: str
    region: str = "ru"

@app.post("/v1/billing/discount/check")
def discount_check(req: DiscountCheckReq, request: Request, token: str = Depends(auth)):
    """Проверить код-купон до оплаты (как проверка ключа): годен ли к этому продукту,
    какой процент и итоговая сумма. Ничего не списывает."""
    _rate_limit("discount", request, DISCOUNT_LIMIT_PER_HOUR)   # против перебора купонов
    aid = _account_of(token)
    if not aid:
        raise HTTPException(400, "no_account")
    if req.product not in PRODUCTS:
        raise HTTPException(422, "unknown_product")
    tier = PRODUCTS[req.product]["tier"]
    d = _resolve_discount(aid, req.product, tier, req.code)
    if not d:
        return {"ok": False, "error": "invalid_or_inapplicable"}
    base = _price_of(req.product, req.region)
    final, off = disc_rules.compute(d, base["amount"])
    return {"ok": True, "percent": d.get("percent"), "off": off,
            "final_amount": final, "currency": base["currency"], "label": d.get("label")}

class DiscountReq(BaseModel):
    percent: Optional[int] = None
    amount_off: Optional[float] = None
    code: Optional[str] = None
    scope: object = "all"            # 'all' | {"tiers":[...], "products":[...]}
    starts_at: Optional[int] = None
    expires_at: Optional[int] = None
    max_uses: Optional[int] = None
    max_per_account: Optional[int] = None
    source: str = "owner"            # 'owner' | 'server_in_a_box'
    label: Optional[str] = None

@app.post("/v1/admin/discount")
def admin_create_discount(req: DiscountReq, p: Principal = Depends(require("discounts"))):
    """Создать скидку — владелец ИЛИ Server-in-a-Box (админ-токен), как выпуск ключа.
    Кампания (без code — применяется автоматически) или код-купон (с code).
    scope: 'all' или {tiers/products}. Окно времени starts_at…expires_at. Лимиты
    max_uses (всего) и max_per_account."""
    if not req.percent and not req.amount_off:
        raise HTTPException(422, "percent_or_amount_required")
    did = b32(secrets.token_bytes(6))
    d = {"id": did, "code": (req.code or None), "percent": req.percent,
         "amount_off": req.amount_off, "scope": req.scope,
         "starts_at": req.starts_at, "expires_at": req.expires_at,
         "max_uses": req.max_uses, "used": 0,
         "max_per_account": req.max_per_account, "used_by": {},
         "active": True, "source": req.source, "label": req.label}
    discounts[did] = d
    _save_discounts()
    log.info("скидка создана: %s %s%% scope=%s source=%s",
             did, req.percent, req.scope, req.source)
    _audit(p, "discount_create", did, percent=req.percent, code=req.code)
    return {"ok": True, "discount": d}

@app.post("/v1/admin/discount/{discount_id}/revoke")
def admin_revoke_discount(discount_id: str, p: Principal = Depends(require("discounts"))):
    """Отозвать скидку (деактивировать)."""
    d = discounts.get(discount_id)
    if not d:
        raise HTTPException(404, "no_discount")
    d["active"] = False
    _save_discounts()
    _audit(p, "discount_revoke", discount_id)
    return {"ok": True, "id": discount_id, "active": False}

@app.get("/v1/admin/discounts")
def admin_list_discounts(_: Principal = Depends(require("discounts"))):
    """Все скидки (кампании и коды) с состоянием и использованием — для панели."""
    now = int(time.time())
    out = []
    for d in discounts.values():
        out.append({**{k: d.get(k) for k in
                       ("id", "code", "percent", "amount_off", "scope",
                        "starts_at", "expires_at", "max_uses", "used",
                        "max_per_account", "active", "source", "label")},
                    "live": disc_rules.is_active(d, now)})
    out.sort(key=lambda x: (not x["active"], x["id"]))
    return {"discounts": out, "total": len(out)}

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
    if pay["status"] in ("completed", "failed"):    # ТЕРМИНАЛЬНЫЕ статусы — не переигрываем
        return {"ok": True, "status": pay["status"], "idempotent": True}
    if data.get("status") == "failed":
        pay.update(status="failed", completed_at=int(time.time()))
        _save_payments()
        log.info("платёж %s помечен failed вебхуком", pay["payment_id"])
        return {"ok": True, "status": "failed"}
    sub, applied = _finalize_payment(pay)
    log.info("платёж %s подтверждён вебхуком: %s/%s",
             pay["payment_id"], pay["product"], pay["method"])
    pay.update(status="completed" if applied else "failed", completed_at=int(time.time()))
    _save_payments()
    return {"ok": True, "status": pay["status"], "subscription": sub}

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

def _verify_node(request: Request, raw: bytes, data: dict):
    """Проверка подписи узла (пер-узловой HMAC). Возвращает node_id или 403."""
    node_id = data.get("node_id")
    node_secret = _node_secrets.get(node_id) if node_id else None
    if not node_secret:
        raise HTTPException(403, "unknown_node")
    expect = hmac.new(node_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("x-node-sign", ""), expect):
        log.warning("отказ узла %s: неверная подпись", node_id)
        raise HTTPException(403, "bad_node_sign")
    return node_id

async def _node_body(request: Request):
    raw = await request.body()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not_an_object")
    except Exception:
        raise HTTPException(422, "bad_json")
    return raw, data

@app.post("/v1/node/authorize")
async def node_authorize(request: Request):
    """Старт сессии: узел спрашивает, можно ли пускать этот аккаунт, и получает
    session_token + «лиз» минут (сколько крутить БЕЗ связи с backend). Enforcement
    при нуле — здесь: если покрыть нечем (coverage=none), allowed=false и узел не
    поднимает туннель. session_token привязан к (node_id, account_id) и ТОЛЬКО он
    даёт право списывать этот баланс (защита от чужого узла)."""
    raw, data = await _node_body(request)
    node_id = _verify_node(request, raw, data)
    aid = data.get("account_id")
    if aid not in idaccounts:
        raise HTTPException(404, "no_account")
    cov = _coverage(aid)
    sub = _sub(aid)
    if cov == "none":
        return {"allowed": False, "coverage": "none", "reason": "no_time_or_subscription",
                "active_minutes_left": sub.get("active_minutes_left", 0)}
    # «лиз»: для баланса — не больше остатка (и не больше 30 мин за один лиз);
    # для подписки/lifetime — фиксированное окно переопроса (минуты не тратятся).
    if cov == "balance":
        lease = min(int(sub.get("active_minutes_left", 0)), 30)
    else:
        lease = 30
    _evict_node_sessions()
    stoken = b32(secrets.token_bytes(12))
    node_sessions[stoken] = {"node_id": node_id, "account_id": aid,
                             "charged": 0, "created_at": int(time.time())}
    return {"allowed": True, "coverage": cov, "session_token": stoken,
            "lease_minutes": lease, "active_minutes_left": sub.get("active_minutes_left", 0)}

@app.post("/v1/node/session")
async def node_session(request: Request):
    """Узел рапортует активные минуты сессии → списываем баланс активного времени.
    Узнаём ДЛИТЕЛЬНОСТЬ, не содержимое трафика (приватность).

    Два режима:
      • НОВЫЙ (идемпотентный): тело {session_token, cum_minutes} — узел шлёт ИТОГО
        минут сессии; сервер вычитает только прирост (delta = cum − уже списано).
        Повтор того же cum ничего не списывает дважды; падение узла наверстывается
        следующим cum. Списываем ТОЛЬКО если coverage=balance (подписка — приоритет).
      • ЛЕГАСИ: тело {account_id, minutes} — прямое списание дельты (обратная совместимость).
    Оба пути подписаны пер-узловым секретом."""
    raw, data = await _node_body(request)
    node_id = _verify_node(request, raw, data)

    stoken = data.get("session_token")
    if stoken is not None:                          # ── новый идемпотентно-кумулятивный путь ──
        sess = node_sessions.get(stoken)
        if not sess or sess["node_id"] != node_id:  # чужой/просроченный токен — 403
            raise HTTPException(403, "bad_session_token")
        aid = sess["account_id"]
        if aid not in idaccounts:
            raise HTTPException(404, "no_account")
        try:
            cum = max(0, int(data.get("cum_minutes", 0)))
        except (TypeError, ValueError):
            raise HTTPException(422, "bad_minutes")
        # sanity: накопленные минуты не больше прошедшего wall-clock (+2 мин запаса) —
        # узел не может «открутить» больше реального времени сессии.
        max_cum = (int(time.time()) - sess["created_at"]) // 60 + 2
        cum = min(cum, max_cum)
        delta = max(0, cum - sess["charged"])
        sess["charged"] = cum
        sub = _sub(aid)
        cov = _coverage(aid)
        if delta and cov == "balance":              # подписка/lifetime → минуты НЕ трогаем
            sub["active_minutes_left"] = max(0, sub["active_minutes_left"] - delta)
            _ledger_add(aid, "debit", minutes=delta)
            _save_idaccounts()
        left = sub.get("active_minutes_left", 0)
        return {"ok": True, "active_minutes_left": left, "coverage": _coverage(aid),
                "cutoff": _coverage(aid) == "none"}   # cutoff=true → узлу пора рвать сессию

    # ── легаси-путь: {account_id, minutes} (прямая дельта) ──
    try:
        minutes = max(0, int(data.get("minutes", 0)))
    except (TypeError, ValueError):
        raise HTTPException(422, "bad_minutes")
    aid = data.get("account_id")
    if aid not in idaccounts:
        raise HTTPException(404, "no_account")
    sub = _sub(aid)
    if sub["mode"] == "balance":                    # легаси: поведение как раньше (без приоритета подписки)
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
def admin_grant(req: GrantReq, p: "Principal" = Depends(require("billing"))):
    """Грант премиума по решению оператора (комп/бонус; не покупается массово).
    Требует scope `billing`: это МИНТ ценности (в т.ч. пожизненный ultimate) — не под
    `users` (управление пользователями), иначе саппорт-работник начислял бы безлимит."""
    if req.account_id not in idaccounts:
        raise HTTPException(404, "no_account")
    g = {"tier": req.tier, "expires": req.days, "source": "owner_grant", "reason": req.reason}
    idaccounts[req.account_id]["granted_tier"] = g
    _audit(p, "grant", req.account_id, tier=req.tier, days=req.days, reason=req.reason)
    # Единая модель тира: грант отражается и в ПОДПИСКЕ, а не только в granted_tier —
    # иначе GET /v1/billing/status показывал бы free/старый тир (два источника правды).
    # По рангу: грант не понижает уже оплаченный более высокий тир.
    sub = _sub(req.account_id)
    _raise_tier(sub, req.tier)
    sub["source"] = "owner_grant"
    if req.days is None:
        sub["lifetime"] = True            # пожизненный грант — не привязан к days_left
    else:
        sub["mode"] = "period"; sub["days_left"] += int(req.days)
        _ledger_add(req.account_id, "grant", days=int(req.days), reason=req.reason, tier=req.tier)
    idaccounts[req.account_id]["tier"] = sub["tier"]   # верхнеуровневый тир = тир подписки
    _save_idaccounts()
    return {"ok": True, "granted_tier": g, "subscription": sub}


# ── Рефералы и репутация (видны пользователю) ─────────────────────────────────
@app.get("/v1/referral")
def referral_info(token: str = Depends(auth)):
    aid = _account_of(token)
    if not aid:
        raise HTTPException(400, "no_account")
    _ensure_referral_fields(aid)
    acc = idaccounts[aid]
    lvl, cap, mult = _reputation(aid)
    now = time.time()
    used = len([t for t in acc.get("payout_log", []) if now - t < 30 * 86400])
    refs = acc.get("referrals", [])
    converted = len([r for r in refs if idaccounts.get(r, {}).get("paid_months", 0) > 0])
    rep_meta = next((x for x in _eff_economy()["reputation"] if x["level"] == lvl), {})
    # Всего начислено дней за рефералов (для стата «выплаты» в приложении/на сайте) —
    # из журнала бонусов по реферальным причинам. Без этого клиент показывал демо-число.
    earned_days = sum(int(e.get("days", 0)) for e in acc.get("bonus_log", [])
                      if str(e.get("reason", "")).startswith("ref"))
    return {"invite_code": acc.get("invite_code"),
            "reputation": {"level": lvl, "name": rep_meta.get("name", {}),
                           "payout_cap_month": cap, "multiplier": mult,
                           "payouts_used": used,
                           "payouts_left": (None if cap == 0 else max(0, cap - used))},
            "invited": len(refs), "converted": converted, "earned_days": earned_days}

class FraudReq(BaseModel):
    account_id: str

@app.post("/v1/admin/fraud")
def admin_fraud(req: FraudReq, p: "Principal" = Depends(require("users"))):
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
        # Снимаем и пожизненное покрытие: _coverage() отдаёт "lifetime" при
        # granted_tier(expires=None) ИЛИ sub.lifetime, поэтому обнуления days/minutes
        # мало — без этого ферма с owner-грантом сохраняла бы полный доступ.
        s.pop("lifetime", None)
        acc.pop("granted_tier", None)
        acc["tier"] = "free"
        flagged.append(a)
        for r in list(acc.get("referrals", [])):
            walk(r)
    walk(req.account_id)
    _save_idaccounts()
    _audit(p, "fraud_flag", req.account_id, count=len(flagged))
    return {"ok": True, "flagged": flagged, "count": len(flagged)}

@app.post("/v1/admin/unfraud")
def admin_unfraud(req: FraudReq, p: "Principal" = Depends(require("users"))):
    """Снять метку фрода с аккаунта (ошибочно помечен). Ветку НЕ трогаем — только сам
    аккаунт; премиум не возвращаем автоматически (при необходимости — грантом)."""
    acc = idaccounts.get(req.account_id)
    if not acc:
        raise HTTPException(404, "no_account")
    acc["fraud"] = False
    _save_idaccounts()
    _audit(p, "unfraud", req.account_id)
    return {"ok": True, "account_id": req.account_id, "fraud": False}


# ── Колесо фортуны: бесплатный ежедневный бонус (provably fair) ────────────────
import datetime as _dt

def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

def _wheel_segments():
    return _eff_economy().get("wheel", {}).get("segments", [])

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
    if not aid:
        raise HTTPException(400, "no_account")
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
    # Критическая секция под локом: без неё два одновременных спина за день оба
    # проходят проверку и начисляют дважды (дневной лимит обходится).
    with _state_lock:
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
    # Привязка погашения — к УСТОЙЧИВОЙ личности (account_id), а не к эфемерному
    # session-токену: иначе один пользователь, перелогинившись, выбирал все uses
    # многоразового ключа. Аноним без account_id — привязываем к токену (best effort).
    aid = _account_of(token)
    bind = aid or token
    try:
        res = issuer.redeem(req.code, account_token=bind)
    except KeyError_ as e:
        code = str(e)
        log.warning("отказ погашения ключа: %s", code)
        status = {"key_already_redeemed": 409, "key_revoked": 410,
                  "key_expired": 410, "key_invalid": 422}.get(code, 422)
        raise HTTPException(status, code)
    a = accounts[token]
    # Продлеваем сессионную запись ТОЛЬКО при реальном погашении. Идемпотентный
    # повтор (тот же ключ той же личностью/токеном) НЕ должен добавлять дни ещё раз —
    # иначе одноразовый ключ бесконечно продлевает премиум повторными вызовами.
    if not res.get("idempotent"):
        base = datetime.now(timezone.utc)
        if a["paid_until"]:
            cur = datetime.fromisoformat(a["paid_until"])
            if cur > base: base = cur
        paid_until = base + timedelta(days=res["grant_days"])
        a["plan"] = res["plan"] if res["plan"] != "topup" else a["plan"]
        a["paid_until"] = paid_until.isoformat()
        a["max_sessions"] = 5 if a["plan"] == "pro" else a["max_sessions"]
        _save_accounts()
    # Начислить и на ИДЕНТИТИ-слой (billing/ledger видят оплату ключом; переживает
    # перелогин). Только для зарегистрированных и только при реальном (не идемпотентном) погашении.
    if aid and not res.get("idempotent") and res["grant_days"] > 0:
        tier = "ultimate" if res.get("plan") == "ultimate" else "premium"
        _grant_days(aid, res["grant_days"], "keyact", tier=tier)
        _save_idaccounts()
        log.info("ключ погашен: account=%s plan=%s +%sдн", aid, res["plan"], res["grant_days"])
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
        # 304 не должен нести тело (HTTP-семантика). HTTPException(304) прикрутил бы
        # JSON {"detail":...}, что некоторые прокси/CDN обрабатывают неверно.
        return Response(status_code=304)
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
    # Выдаём узлу СВЕЖИЙ per-node секрет и РОТИРУЕМ старый. id узлов публичны (они в
    # подписанном манифесте), а register авторизуется общим NODE_SECRET — если бы мы
    # возвращали уже выданный секрет, любой со знанием общего секрета мог бы восстановить
    # per-node секрет каждого узла и подделывать его heartbeat/session. Легитимный узел
    # берёт секрет из ответа последнего register.
    node_secret = b32(secrets.token_bytes(24))
    _node_secrets[node["id"]] = node_secret
    persist.jsave("node_secrets.json", _node_secrets)
    return {"ok": True, "id": node["id"], "total_nodes": len(cur),
            "version": MANIFEST_VERSION, "node_secret": node_secret,
            "stale_after_sec": NODE_STALE_SEC}


# ── 3b. Управление узлами для оператора (Server-in-a-Box) ──────────────────────
@app.get("/v1/admin/nodes")
def admin_list_nodes(_: Principal = Depends(require("nodes"))):
    """Список зарегистрированных узлов + живость/загрузка — для управления флотом."""
    data = persist.jload("nodes.json", {"nodes": []})
    cur = data.get("nodes") if isinstance(data, dict) else (data or [])
    now = int(time.time())
    out = []
    for n in cur:
        h = _node_health.get(n["id"], {})
        last = h.get("last_seen")
        out.append({"id": n["id"], "code": n.get("code"), "country": n.get("country"),
                    "host": n.get("host"), "protocols": n.get("protocols"),
                    "roles": n.get("roles", []),
                    "load_pct": h.get("load_pct", n.get("loadPct")),
                    "last_seen": last,
                    "alive": (last is not None and (now - last) <= NODE_STALE_SEC)})
    return {"nodes": out, "total": len(out), "version": MANIFEST_VERSION,
            "stale_after_sec": NODE_STALE_SEC}

@app.post("/v1/admin/node/{node_id}/remove")
def admin_remove_node(node_id: str, p: Principal = Depends(require("nodes"))):
    """Снять узел из манифеста (оператор). Секрет узла отзывается, манифест пересобирается."""
    data = persist.jload("nodes.json", {"nodes": []})
    cur = data.get("nodes") if isinstance(data, dict) else (data or [])
    new = [n for n in cur if n.get("id") != node_id]
    if len(new) == len(cur):
        raise HTTPException(404, "no_node")
    persist.jsave("nodes.json", {"nodes": new})
    _node_health.pop(node_id, None); _save_node_health()
    _node_secrets.pop(node_id, None); persist.jsave("node_secrets.json", _node_secrets)
    _bump_and_rebuild()
    _audit(p, "node_remove", node_id)
    return {"ok": True, "removed": node_id, "total_nodes": len(new), "version": MANIFEST_VERSION}


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


# ── 4. Поддержка: реальный диалог оператор ↔ пользователь ─────────────────────
# Тред привязан к АККАУНТУ (у зарегистрированных — по account_id: переписка видна с
# любого устройства; у гостя — по токену сессии). Пользователь пишет — сообщение
# кладётся в тред и ждёт ОПЕРАТОРА (никаких фейковых авто-ответов). Оператор
# (владелец/работник со scope «support») отвечает из панели Server-in-a-Box.
def _thread_key(token: str) -> str:
    aid = accounts.get(token, {}).get("account_id")
    if aid:
        return aid                       # зарегистрированный: account_id публичен и безопасен
    # Гость: НЕ ключуем тред самим bearer-токеном — иначе он утекает работникам
    # поддержки (список тредов /v1/admin/support/threads) и позволяет угнать сессию.
    # Берём необратимый хеш токена — он идентифицирует тред, но не является ключом входа.
    return "g:" + hashlib.sha256(token.encode()).hexdigest()[:32]

@app.get("/v1/support/thread")
def get_thread(token: str = Depends(auth)):
    return {"messages": threads.get(_thread_key(token), [])}

@app.post("/v1/support/message")
def post_message(req: MsgReq, token: str = Depends(auth)):
    key = _thread_key(token)
    msg = {"from": "user", "text": req.text, "at": _now()}
    if req.diag and len(json.dumps(req.diag)) <= 8000:   # ограничиваем размер диагностики
        msg["diag"] = req.diag
    th = threads.setdefault(key, [])
    th.append(msg)
    if len(th) > 200:                        # предел сообщений в треде (анти-спам, как audit_log)
        del th[:len(th) - 200]
    _save_threads()
    log.info("support: новое обращение в треде %s (ждёт оператора)", key[:8])
    return {"ok": True, "awaiting_operator": True}

# ── 4b. Поддержка со стороны оператора (панель Server-in-a-Box) ────────────────
# Статус тредов (open/closed) — сбоку, чтобы не менять формат сообщений.
THREAD_STATUS_FILE = "thread_status.json"
thread_status: dict[str, str] = persist.jload(THREAD_STATUS_FILE, {})
def _save_thread_status(): persist.jsave(THREAD_STATUS_FILE, thread_status)

# Готовые ответы оператора (быстрые шаблоны) — правятся здесь, видны в панели.
SUPPORT_TEMPLATES = [
    {"title": "Импорт своего моста", "text": "Импортируйте свой мост: раздел «Скачать» → «Добавить свой мост», вставьте ссылку vless://…/ss://…/hysteria2://…"},
    {"title": "Починить интернет", "text": "Откройте приложение → «Починить интернет»: пересоберёт маршрут и сменит протокол автоматически (обычно решает за 15 секунд)."},
    {"title": "Сменить локацию", "text": "Попробуйте другую страну в списке серверов — ближайшая живая обычно быстрее. Если сервер молчит, он скоро сам уйдёт из списка."},
    {"title": "Оплата не прошла", "text": "Проверьте статус в разделе «Аккаунт → Платежи». Если платёж завис — напишите номер платежа, проверим и при необходимости начислим вручную."},
    {"title": "Баланс времени", "text": "Активное время тратится только когда VPN включён. Подписка/грант — приоритет, часы при них не расходуются."},
]

@app.get("/v1/admin/support/templates")
def admin_support_templates(_: Principal = Depends(require("support"))):
    """Быстрые ответы оператора (шаблоны)."""
    return {"templates": SUPPORT_TEMPLATES}

@app.get("/v1/admin/support/threads")
def admin_support_threads(status: str = "", _: Principal = Depends(require("support"))):
    """Список всех обращений: id треда, число сообщений, последнее сообщение, статус
    (open/closed), ждёт ли ответа оператора. Фильтр ?status=open|closed. Ждущие — сверху."""
    out = []
    for key, msgs in threads.items():
        if not msgs:
            continue
        st = thread_status.get(key, "open")
        if status and st != status:
            continue
        last = msgs[-1]
        out.append({"id": key, "count": len(msgs), "status": st,
                    "last_from": last.get("from"), "last_text": last.get("text", ""),
                    "last_at": last.get("at"),
                    "awaiting": last.get("from") == "user" and st == "open"})
    out.sort(key=lambda t: (t["awaiting"], t["last_at"] or ""), reverse=True)
    return {"threads": out, "total": len(out),
            "awaiting": sum(1 for t in out if t["awaiting"])}

@app.get("/v1/admin/support/thread/{key}")
def admin_support_thread(key: str, _: Principal = Depends(require("support"))):
    """Полная переписка одного треда + статус + привязка к аккаунту (ключ=account_id)."""
    if key not in threads:
        raise HTTPException(404, "no_thread")
    return {"id": key, "messages": threads[key],
            "status": thread_status.get(key, "open"),
            "account_id": (key if key in idaccounts else None)}

@app.post("/v1/admin/support/{key}/reply")
def admin_support_reply(key: str, req: MsgReq, p: Principal = Depends(require("support"))):
    """Ответ оператора в тред (виден пользователю в приложении/на сайте)."""
    if key not in threads:
        raise HTTPException(404, "no_thread")
    threads[key].append({"from": "support", "text": req.text,
                         "at": _now(), "by": p.name or "Оператор"})
    _save_threads()
    thread_status[key] = "open"; _save_thread_status()
    _audit(p, "support_reply", key)
    return {"ok": True}

class ThreadStatusReq(BaseModel):
    status: str = "closed"           # closed | open

@app.post("/v1/admin/support/{key}/status")
def admin_support_status(key: str, req: ThreadStatusReq, p: Principal = Depends(require("support"))):
    """Закрыть/переоткрыть обращение (решено / вернуть в работу)."""
    if key not in threads:
        raise HTTPException(404, "no_thread")
    st = "closed" if req.status == "closed" else "open"
    thread_status[key] = st; _save_thread_status()
    _audit(p, "support_" + st, key)
    return {"ok": True, "id": key, "status": st}


# ── 4c. Пользователи: обзор и управление (scope users) ─────────────────────────
# Приватность сохраняется: PII нет (алиасы хранятся как HMAC→aid и НЕ разворачиваются),
# оператор видит только account_id, тир, подписку/баланс, устройства, рефералы, журнал.
def _devices_of(aid: str) -> list:
    return [{"id": k, "name": v.get("name"), "platform": v.get("platform"),
             "role": v.get("role"), "revoked": v.get("revoked", False),
             "last_seen": v.get("last_seen")}
            for k, v in devices.items() if v.get("account_id") == aid]

def _payments_of(aid: str) -> list:
    out = [{"payment_id": p.get("payment_id"), "product": p.get("product"),
            "method": p.get("method"), "amount": p.get("amount"),
            "currency": p.get("currency"), "status": p.get("status"),
            "created_at": p.get("created_at"), "completed_at": p.get("completed_at")}
           for p in payments.values() if p.get("account_id") == aid]
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return out

def _user_summary(aid: str) -> dict:
    acc = idaccounts.get(aid, {})
    sub = acc.get("sub", {})
    devs = [d for d in devices.values() if d.get("account_id") == aid and not d.get("revoked")]
    return {"account_id": aid, "created_at": acc.get("created_at"),
            "tier": acc.get("tier", "free"), "coverage": _coverage(aid),
            "days_left": sub.get("days_left", 0),
            "active_minutes_left": sub.get("active_minutes_left", 0),
            "lifetime": bool(sub.get("lifetime")) or (acc.get("granted_tier", {}) or {}).get("expires") is None and bool(acc.get("granted_tier")),
            "devices": len(devs), "paid_months": acc.get("paid_months", 0),
            "referrals": len(acc.get("referrals", [])), "fraud": bool(acc.get("fraud"))}

@app.get("/v1/admin/users")
def admin_list_users(query: str = "", tier: str = "", limit: int = 100,
                     _: Principal = Depends(require("users"))):
    """Список аккаунтов (без PII): тир, покрытие, баланс, устройства, рефералы, фрод.
    Фильтры: ?query=<подстрока account_id>, ?tier=free|premium|ultimate. Свежие сверху."""
    n = max(1, min(int(limit or 100), 1000))
    rows = []
    for aid, acc in idaccounts.items():
        if query and query.lower() not in aid.lower():
            continue
        if tier and acc.get("tier", "free") != tier:
            continue
        rows.append(_user_summary(aid))
    rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return {"users": rows[:n], "total": len(rows)}

@app.get("/v1/admin/user/{aid}")
def admin_user_detail(aid: str, _: Principal = Depends(require("users"))):
    """Полная карточка аккаунта: подписка, грант, устройства, платежи, рефералы,
    репутация, журнал (последние 50), есть ли обращение в поддержку."""
    acc = idaccounts.get(aid)
    if not acc:
        raise HTTPException(404, "no_account")
    lvl, cap, mult = _reputation(aid)
    ledger = sorted(acc.get("ledger", []), key=lambda e: e.get("at", 0), reverse=True)[:50]
    return {"account_id": aid, "summary": _user_summary(aid),
            "subscription": _sub(aid), "granted_tier": acc.get("granted_tier"),
            "invite_code": acc.get("invite_code"), "invited_by": acc.get("invited_by"),
            "referrals": acc.get("referrals", []),
            "reputation": {"level": lvl, "payout_cap_month": cap, "multiplier": mult},
            "devices": _devices_of(aid), "payments": _payments_of(aid),
            "ledger": ledger, "has_support": aid in threads}

class BalanceAdjustReq(BaseModel):
    minutes: int                     # + начислить, − списать
    reason: str = "operator_adjust"

@app.post("/v1/admin/user/{aid}/balance")
def admin_adjust_balance(aid: str, req: BalanceAdjustReq, p: Principal = Depends(require("billing"))):
    """Начислить/списать активные минуты (комп/возврат/коррекция). Пишет в журнал.
    Требует scope `billing` (минт баланса — деньги), не `users`."""
    if aid not in idaccounts:
        raise HTTPException(404, "no_account")
    sub = _sub(aid)
    if req.minutes >= 0:
        sub["active_minutes_left"] = sub.get("active_minutes_left", 0) + req.minutes
        if sub.get("tier", "free") == "free":
            _raise_tier(sub, "premium"); idaccounts[aid]["tier"] = sub["tier"]
        sub["mode"] = "balance" if sub.get("days_left", 0) == 0 and not sub.get("lifetime") else sub.get("mode", "balance")
        _ledger_add(aid, "grant", minutes=req.minutes, reason=req.reason)
    else:
        sub["active_minutes_left"] = max(0, sub.get("active_minutes_left", 0) + req.minutes)
        _ledger_add(aid, "debit", minutes=-req.minutes, reason=req.reason)
    _save_idaccounts()
    _audit(p, "balance_adjust", aid, minutes=req.minutes, reason=req.reason)
    return {"ok": True, "account_id": aid, "active_minutes_left": sub["active_minutes_left"]}

@app.post("/v1/admin/user/{aid}/device/{did}/revoke")
def admin_revoke_user_device(aid: str, did: str, p: Principal = Depends(require("users"))):
    """Отозвать устройство пользователя (украдено/лишнее) — сессии этого устройства станут 401."""
    d = devices.get(did)
    if not d or d.get("account_id") != aid:
        raise HTTPException(404, "no_device")
    d["revoked"] = True; _save_devices()
    _audit(p, "device_revoke", aid, device=did)
    return {"ok": True, "account_id": aid, "device_id": did, "revoked": True}


# ── 4d. Платежи: обзор, сводка, ручное подтверждение/возврат (scope billing) ───
@app.get("/v1/admin/payments")
def admin_list_payments(status: str = "", limit: int = 100,
                        _: Principal = Depends(require("billing"))):
    """Все платежи (id, аккаунт, продукт, сумма, метод, статус, время). Фильтр ?status=."""
    n = max(1, min(int(limit or 100), 1000))
    rows = []
    for pay in payments.values():
        if status and pay.get("status") != status:
            continue
        rows.append({"payment_id": pay.get("payment_id"), "account_id": pay.get("account_id"),
                     "product": pay.get("product"), "method": pay.get("method"),
                     "amount": pay.get("amount"), "currency": pay.get("currency"),
                     "status": pay.get("status"), "provider": pay.get("provider"),
                     "created_at": pay.get("created_at"), "completed_at": pay.get("completed_at")})
    rows.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return {"payments": rows[:n], "total": len(rows)}

@app.get("/v1/admin/billing/summary")
def admin_billing_summary(_: Principal = Depends(require("billing"))):
    """Сводка: выручка (по завершённым), разбивка по продуктам и статусам, за 24ч/7д."""
    now = int(time.time())
    by_product, by_status, revenue = {}, {}, {}
    rev_24h = rev_7d = 0
    for pay in payments.values():
        st = pay.get("status", "unknown")
        by_status[st] = by_status.get(st, 0) + 1
        if st == "completed":
            cur = pay.get("currency", "RUB"); amt = pay.get("amount") or 0
            revenue[cur] = round(revenue.get(cur, 0) + amt, 2)
            prod = pay.get("product", "?")
            by_product[prod] = by_product.get(prod, 0) + 1
            ts = pay.get("completed_at") or pay.get("created_at") or 0
            if cur == "RUB":
                if now - ts <= 86400: rev_24h += amt
                if now - ts <= 7 * 86400: rev_7d += amt
    return {"revenue": revenue, "by_product": by_product, "by_status": by_status,
            "revenue_rub_24h": round(rev_24h, 2), "revenue_rub_7d": round(rev_7d, 2),
            "total_payments": len(payments)}

@app.post("/v1/admin/payment/{pid}/confirm")
def admin_confirm_payment(pid: str, p: Principal = Depends(require("billing"))):
    """Вручную подтвердить pending-платёж (тест-режим / зависший колбэк). Идемпотентно."""
    pay = payments.get(pid)
    if not pay:
        raise HTTPException(404, "no_payment")
    if pay.get("status") in ("completed", "failed", "refunded"):
        return {"ok": True, "status": pay["status"], "idempotent": True}
    sub, applied = _finalize_payment(pay)
    pay.update(status="completed" if applied else "failed", completed_at=int(time.time()))
    _save_payments()
    _audit(p, "payment_confirm", pid, account=pay.get("account_id"), product=pay.get("product"))
    return {"ok": True, "status": pay["status"], "subscription": sub}

@app.post("/v1/admin/payment/{pid}/fail")
def admin_fail_payment(pid: str, p: Principal = Depends(require("billing"))):
    """Пометить pending-платёж проваленным (отменён/не оплачен)."""
    pay = payments.get(pid)
    if not pay:
        raise HTTPException(404, "no_payment")
    if pay.get("status") == "completed":
        raise HTTPException(409, "already_completed_use_refund")
    pay.update(status="failed", completed_at=int(time.time()))
    _save_payments()
    _audit(p, "payment_fail", pid)
    return {"ok": True, "status": "failed"}

@app.post("/v1/admin/payment/{pid}/refund")
def admin_refund_payment(pid: str, p: Principal = Depends(require("billing"))):
    """Возврат завершённого платежа: помечаем refunded и снимаем начисленный эффект
    (дни/минуты) с аккаунта, насколько возможно. Деньги в тест-режиме не двигаются."""
    pay = payments.get(pid)
    if not pay:
        raise HTTPException(404, "no_payment")
    if pay.get("status") != "completed":
        raise HTTPException(409, "not_completed")
    aid, product = pay.get("account_id"), pay.get("product")
    prod = PRODUCTS.get(product, {})
    sub = _sub(aid)
    if "add_minutes" in prod:
        sub["active_minutes_left"] = max(0, sub.get("active_minutes_left", 0) - prod["add_minutes"])
        _ledger_add(aid, "debit", minutes=prod["add_minutes"], reason="refund:" + pid)
    elif "add_days" in prod:
        sub["days_left"] = max(0, sub.get("days_left", 0) - prod["add_days"])
        _ledger_add(aid, "debit", days=prod["add_days"], reason="refund:" + pid)
    idaccounts[aid]["tier"] = sub.get("tier", "free")
    _save_idaccounts()
    pay.update(status="refunded", refunded_at=int(time.time()))
    _save_payments()
    _audit(p, "payment_refund", pid, account=aid, product=product)
    return {"ok": True, "status": "refunded", "subscription": sub}


# ── 5. Аттестация устройства (анти-абьюз) ─────────────────────────────────────
@app.post("/v1/attest")
def attest(req: AttestReq, token: str = Depends(auth)):
    # Здесь проверялся бы App Attest / Play Integrity payload. Личность НЕ раскрывается.
    ok = bool(req.token_blob)  # заглушка
    return {"ok": ok, "deviceClass": "genuine" if ok else "unknown"}


# ── админ: выпуск ключей (для демо/реселлеров) ───────────────────────────────
@app.post("/v1/admin/issue")
def admin_issue(req: IssueReq, p: "Principal" = Depends(require("keys"))):
    code = issuer.issue(plan=req.plan, grant_days=req.grant_days, uses=req.uses)
    _audit(p, "key_issue", plan=req.plan, days=req.grant_days, uses=req.uses)
    return {"code": code}

@app.get("/v1/admin/keys")
def admin_list_keys(_: "Principal" = Depends(require("keys"))):
    """Выпущенные ключи (реестр): kid, осталось активаций, отозван, к скольким аккаунтам
    привязан. Сам код показывается ТОЛЬКО при выпуске (как пароль) — здесь его нет."""
    out = []
    for kid, st in issuer._registry.items():
        out.append({"kid": kid, "uses_left": st.uses_left, "revoked": st.revoked,
                    "bound": len(st.bound_accounts)})
    out.sort(key=lambda k: (k["revoked"], -k["uses_left"]))
    return {"keys": out, "total": len(out)}

@app.post("/v1/admin/key/{kid}/revoke")
def admin_revoke_key(kid: str, p: "Principal" = Depends(require("keys"))):
    """Отозвать выпущенный ключ (перестанет активироваться)."""
    if not issuer.revoke(kid):
        raise HTTPException(404, "no_key_or_spent")
    _audit(p, "key_revoke", kid)
    return {"ok": True, "kid": kid, "revoked": True}


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
    _audit("Владелец", "rollout", percent=ROLLOUT, version=MANIFEST_VERSION)
    return {"rollout": ROLLOUT, "version": MANIFEST_VERSION}


# ── Панель Server-in-a-Box: кто я, работники, состояние БД ─────────────────────
@app.get("/v1/admin/whoami")
def admin_whoami(p: Principal = Depends(staff)):
    """Роль и права текущего токена панели — панель по этому рисует доступные вкладки."""
    return {"role": p.role, "name": p.name, "scopes": p.scopes, "all_scopes": ALL_SCOPES}

class StaffReq(BaseModel):
    name: str
    scopes: list[str] = []           # подмножество ALL_SCOPES

@app.post("/v1/admin/staff")
def admin_create_staff(req: StaffReq, _: None = Depends(admin)):
    """Владелец создаёт токен РАБОТНИКА с набором прав (scopes). Работник входит в
    панель этим токеном и видит только выданные разделы (напр. только «Поддержка»)."""
    scopes = [s for s in req.scopes if s in ALL_SCOPES]
    if not scopes:
        raise HTTPException(422, "no_valid_scopes")
    tok = "wrk_" + b32(secrets.token_bytes(18))
    sid = b32(secrets.token_bytes(5))
    staff_tokens[tok] = {"id": sid, "name": req.name or "Работник",
                         "scopes": scopes, "revoked": False, "created_at": int(time.time())}
    _save_staff()
    log.info("создан работник %s (%s) scopes=%s", sid, req.name, scopes)
    _audit("Владелец", "staff_create", sid, name=req.name, scopes=scopes)
    return {"ok": True, "id": sid, "name": req.name, "scopes": scopes, "token": tok}

class StaffEditReq(BaseModel):
    scopes: list[str]

@app.patch("/v1/admin/staff/{staff_id}")
def admin_edit_staff(staff_id: str, req: StaffEditReq, _: None = Depends(admin)):
    """Изменить набор прав работника (владелец). Токен не меняется."""
    scopes = [s for s in req.scopes if s in ALL_SCOPES]
    if not scopes:
        raise HTTPException(422, "no_valid_scopes")
    for r in staff_tokens.values():
        if r["id"] == staff_id:
            r["scopes"] = scopes; _save_staff()
            _audit("Владелец", "staff_edit", staff_id, scopes=scopes)
            return {"ok": True, "id": staff_id, "scopes": scopes}
    raise HTTPException(404, "no_staff")

@app.get("/v1/admin/staff")
def admin_list_staff(_: None = Depends(admin)):
    """Список работников (без самих токенов — токен показывается только при создании)."""
    out = [{"id": r["id"], "name": r["name"], "scopes": r["scopes"],
            "revoked": r.get("revoked", False), "created_at": r.get("created_at")}
           for r in staff_tokens.values()]
    out.sort(key=lambda x: (x["revoked"], x["created_at"] or 0))
    return {"staff": out, "total": len(out)}

@app.post("/v1/admin/staff/{staff_id}/revoke")
def admin_revoke_staff(staff_id: str, _: None = Depends(admin)):
    """Отозвать доступ работника (по id). Токен сразу перестаёт действовать."""
    for r in staff_tokens.values():
        if r["id"] == staff_id:
            r["revoked"] = True; _save_staff()
            _audit("Владелец", "staff_revoke", staff_id)
            return {"ok": True, "id": staff_id, "revoked": True}
    raise HTTPException(404, "no_staff")

@app.get("/v1/admin/audit")
def admin_audit(limit: int = 100, _: None = Depends(admin)):
    """Журнал действий панели (кто что сделал) — только владелец. Свежие сверху."""
    n = max(1, min(int(limit or 100), 500))
    return {"entries": list(reversed(audit_log))[:n], "total": len(audit_log)}

@app.get("/v1/admin/db/stats")
def admin_db_stats(_: None = Depends(admin)):
    """Состояние РЕАЛЬНОЙ базы (SQLite/WAL): путь, размер, режим журнала, ключи.
    Панель показывает это, чтобы владелец видел живую БД, а не набор файлов."""
    return persist.stats()

@app.post("/v1/admin/db/backup")
def admin_db_backup(_: None = Depends(admin)):
    """Сделать консистентный онлайн-бэкап БД в ./backups (безопасно на живой WAL-базе).
    Возвращает путь и размер снимка. Файл забирает оператор (rsync/scp) или таймер."""
    import glob
    os.makedirs("backups", exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    dest = os.path.join("backups", f"symbiont-{stamp}.db")
    persist.backup(dest)
    # ротация: держим 14 последних
    snaps = sorted(glob.glob(os.path.join("backups", "symbiont-*.db")))
    for old in snaps[:-14]:
        try: os.remove(old)
        except OSError: pass
    return {"ok": True, "path": os.path.abspath(dest),
            "size_bytes": os.path.getsize(dest), "kept": min(len(snaps), 14)}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── Публичные веб-страницы с того же адреса, что и API ─────────────────────────
# Если рядом есть каталог web/ (лендинг + чекер ключа) — монтируем его в корень:
#   http://<бэкенд>/            → лендинг index.html
#   http://<бэкенд>/check.html  → РЕАЛЬНЫЙ чекер ключа (тем же origin зовёт
#                                 /v1/key/check и /v1/pubkey — без CORS, без настройки).
# API-маршруты /v1/... объявлены выше и имеют приоритет над этим монтированием.
# Так одному серверу достаточно запустить бэкенд, чтобы сайт показывал реальные данные.
try:
    from fastapi.staticfiles import StaticFiles
    _web_dir = os.environ.get("SYMBIONT_WEB_DIR") or next(
        (p for p in (
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"),
            os.path.join(os.getcwd(), "web"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"),
        ) if os.path.isdir(p)), None)
    if _web_dir:
        app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
        log.info("веб-страницы отдаются из %s (лендинг + чекер ключа на том же адресе)",
                 os.path.abspath(_web_dir))
    else:
        log.info("каталог web/ не найден рядом — публичные страницы не отдаём (только API)")
except Exception as e:
    log.warning("не удалось смонтировать web/: %s", e)
