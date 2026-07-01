"""
db.py — реляционное хранилище «Симбионта» на SQLite (WAL).

Заменяет JSON-стор из persist.py. Главная цель — перенести гарантии корректности
из внутрипроцессных threading.Lock в КОНСТРЕЙНТЫ БД, которые держатся и при
нескольких потоках, и при падении посреди записи:

  • погашение ключа «ровно один раз»  → UNIQUE(kid, account) + условный UPDATE;
  • уникальность алиаса               → PRIMARY KEY(alias_hmac);
  • колесо «один спин в день»          → PRIMARY KEY(account_id, day);
  • «ровно один owner на аккаунт»      → частичный уникальный индекс;
  • аудируемый баланс                  → append-only журнал ledger + кэш в колонках.

Долговечность: WAL + synchronous=NORMAL (без потери коммитов), atomic-commit БД.
Конкурентность: по соединению на поток (thread-local), запись — BEGIN IMMEDIATE,
busy_timeout сглаживает конкуренцию. Для MVP-нагрузки достаточно; на бо́льшую —
пул соединений или Postgres (интерфейс репозитория тот же).

Секреты сервера (admin_token, node_enroll_secret, wheel_seed, alias_secret) здесь
НЕ хранятся намеренно — это секреты развёртывания (env/secrets.json), чтобы дамп
данных не нёс и ключи. В БД — только данные сервиса.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Optional

SCHEMA_VERSION = 2

# ── DDL ───────────────────────────────────────────────────────────────────────
SCHEMA = """
-- АККАУНТ (крипто-личность). Подписка инлайнена 1:1 в колонки.
CREATE TABLE IF NOT EXISTS accounts (
    account_id          TEXT PRIMARY KEY,
    created_at          INTEGER NOT NULL,
    password_hash       TEXT,
    reputation          INTEGER NOT NULL DEFAULT 0,
    tier                TEXT    NOT NULL DEFAULT 'free',   -- эффективный тариф (кэш)
    sub_mode            TEXT    NOT NULL DEFAULT 'period', -- 'period' | 'balance'
    days_left           INTEGER NOT NULL DEFAULT 0,        -- кэш баланса (источник — ledger)
    active_minutes_left INTEGER NOT NULL DEFAULT 0,        -- кэш баланса
    auto_renew          INTEGER NOT NULL DEFAULT 0,
    sub_source          TEXT,
    paid_months         INTEGER NOT NULL DEFAULT 0,
    invite_code         TEXT UNIQUE,
    invited_by          TEXT REFERENCES accounts(account_id),
    granted_tier_json   TEXT,                              -- грант владельца (мелкий блоб)
    fraud_flag          INTEGER NOT NULL DEFAULT 0,
    fraud_reason        TEXT
);
CREATE INDEX IF NOT EXISTS idx_accounts_invited_by ON accounts(invited_by);

-- АЛИАСЫ: только HMAC. PRIMARY KEY = гарантия уникальности (гонку решает БД).
CREATE TABLE IF NOT EXISTS aliases (
    alias_hmac  TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_aliases_account ON aliases(account_id);

-- СЕССИИ (непрозрачные bearer-токены). anon-сессия → account_id IS NULL.
CREATE TABLE IF NOT EXISTS sessions (
    token        TEXT PRIMARY KEY,
    account_id   TEXT REFERENCES accounts(account_id) ON DELETE CASCADE,
    device_id    TEXT,
    label        TEXT,
    plan         TEXT    NOT NULL DEFAULT 'free',
    paid_until   TEXT,                                     -- iso (легаси-поле уровня сессии)
    max_sessions INTEGER NOT NULL DEFAULT 1,
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);

-- УСТРОЙСТВА. role owner|normal; частичный уникальный индекс = ≤1 активный owner.
CREATE TABLE IF NOT EXISTS devices (
    device_id    TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    name         TEXT,
    platform     TEXT,
    role         TEXT    NOT NULL DEFAULT 'normal',
    created_at   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    revoked      INTEGER NOT NULL DEFAULT 0,
    revoked_at   INTEGER,
    attest_class TEXT,
    attest_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_devices_account ON devices(account_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_owner
    ON devices(account_id) WHERE role = 'owner' AND revoked = 0;

-- ЖУРНАЛ ДВИЖЕНИЙ (append-only). Источник истины для баланса; кэш — в accounts.
CREATE TABLE IF NOT EXISTS ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    at            INTEGER NOT NULL,
    kind          TEXT NOT NULL,   -- purchase|grant|bonus|combo|wheel|referral_in|referral_out|spend|redeem|owner_grant
    delta_days    INTEGER NOT NULL DEFAULT 0,
    delta_minutes INTEGER NOT NULL DEFAULT 0,
    tier          TEXT,
    reason        TEXT,
    ref           TEXT             -- payment_id | kid | контрагент | ...
);
CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger(account_id, at);
CREATE INDEX IF NOT EXISTS idx_ledger_kind    ON ledger(account_id, kind, at);

-- КЛЮЧИ-ВАУЧЕРЫ: реестр + богатые метаданные (срок/минуты/тариф/батч/метка).
CREATE TABLE IF NOT EXISTS keys (
    kid           TEXT PRIMARY KEY,
    plan          TEXT NOT NULL,
    grant_days    INTEGER NOT NULL DEFAULT 0,   -- срок (дни premium-времени)
    grant_minutes INTEGER NOT NULL DEFAULT 0,   -- или баланс активных минут
    tier          TEXT,                          -- какой тариф даёт (premium|ultimate); NULL = не меняет
    uses_total    INTEGER NOT NULL,
    uses_left     INTEGER NOT NULL,
    expires_at    INTEGER NOT NULL,
    revoked       INTEGER NOT NULL DEFAULT 0,
    issued_at     INTEGER NOT NULL,
    issued_by     TEXT,
    batch_id      TEXT,                           -- партия (массовый выпуск)
    label         TEXT,                           -- человеко-метка («НГ-2026», «реселлер X»)
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_keys_batch ON keys(batch_id);

-- ПОГАШЕНИЯ: bound_accounts + идемпотентность + анти-двойная-трата.
-- PRIMARY KEY(kid, account) — гонку и повтор решает БД.
CREATE TABLE IF NOT EXISTS key_redemptions (
    kid           TEXT NOT NULL REFERENCES keys(kid) ON DELETE CASCADE,
    account_token TEXT NOT NULL,
    redeemed_at   INTEGER NOT NULL,
    PRIMARY KEY (kid, account_token)
);

-- КОЛЕСО: PRIMARY KEY(account, day) — идемпотентность спина за сутки.
CREATE TABLE IF NOT EXISTS wheel_spins (
    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    day        TEXT NOT NULL,           -- 'YYYY-MM-DD'
    seg_index  INTEGER NOT NULL,
    prize_json TEXT NOT NULL,
    at         INTEGER NOT NULL,
    PRIMARY KEY (account_id, day)
);

-- УЗЛЫ + множества протоколов/ролей (junction).
CREATE TABLE IF NOT EXISTS nodes (
    node_id       TEXT PRIMARY KEY,
    country       TEXT NOT NULL,
    code          TEXT NOT NULL,
    host          TEXT,
    transport     TEXT,
    load_pct      INTEGER NOT NULL DEFAULT 0,
    xray_core     INTEGER NOT NULL DEFAULT 0,
    white_ip      INTEGER NOT NULL DEFAULT 0,
    provider      TEXT,
    enabled       INTEGER NOT NULL DEFAULT 1,
    health_disabled INTEGER NOT NULL DEFAULT 0, -- 1 = выключен свипом по протуханию (не оператором)
    registered_at INTEGER NOT NULL,
    last_seen     INTEGER,
    secret        TEXT,                  -- пер-узловой секрет (чувствительно)
    extra_json    TEXT                   -- forward-compat поля для манифеста
);
CREATE TABLE IF NOT EXISTS node_protocols (
    node_id  TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    protocol TEXT NOT NULL,
    PRIMARY KEY (node_id, protocol)
);
CREATE TABLE IF NOT EXISTS node_roles (
    node_id TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    role    TEXT NOT NULL,
    PRIMARY KEY (node_id, role)
);

-- ПРАВИЛА МАРШРУТИЗАЦИИ (для манифеста; редактируемы).
CREATE TABLE IF NOT EXISTS routing_rules (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT NOT NULL,            -- domainSuffix|tld|...
    match    TEXT NOT NULL,
    action   TEXT NOT NULL,            -- direct|bypass|tunnel|relay|block
    priority INTEGER NOT NULL DEFAULT 0,
    enabled  INTEGER NOT NULL DEFAULT 1
);

-- ПОДДЕРЖКА (тред по токену сессии — как в текущей модели).
CREATE TABLE IF NOT EXISTS support_messages (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    token  TEXT NOT NULL,
    sender TEXT NOT NULL,              -- 'user' | 'support'
    text   TEXT NOT NULL,
    at     INTEGER NOT NULL,
    read   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_support_token ON support_messages(token, id);

-- АУДИТ админ-/узловых действий.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          INTEGER NOT NULL,
    actor       TEXT NOT NULL,          -- 'admin' | node_id | 'system'
    action      TEXT NOT NULL,          -- issue_key|grant|revoke_key|fraud_flag|rollout|node_register
    target      TEXT,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);

-- ГЛОБАЛЬНЫЕ НАСТРОЙКИ-СИНГЛТОНЫ (НЕ секреты): rollout, economy, версия и т.п.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""


# ── Исключения (коды совпадают с текущими в keys.py для drop-in замены) ────────
class RedeemError(ValueError):
    """key_invalid | key_revoked | key_expired | key_already_redeemed"""


class AliasTaken(ValueError):
    pass


class AlreadySpun(ValueError):
    pass


def _now() -> int:
    return int(time.time())


# ── Хранилище ─────────────────────────────────────────────────────────────────
class Database:
    """Тонкий репозиторий поверх SQLite. Соединение — по потоку (thread-local)."""

    def __init__(self, path: str = "symbiont.db"):
        self.path = path
        self._local = threading.local()
        # одно соединение для инициализации схемы
        conn = self._conn()
        conn.executescript(SCHEMA)
        self._ensure_columns(conn)
        if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    def _ensure_columns(self, conn) -> None:
        """Идемпотентная миграция: дотачивает недостающие столбцы в существующих БД
        (свежие получают их из SCHEMA). Без потери данных."""
        want = {
            "keys": [("grant_minutes", "INTEGER NOT NULL DEFAULT 0"), ("tier", "TEXT"),
                     ("batch_id", "TEXT"), ("label", "TEXT"), ("note", "TEXT")],
            "nodes": [("health_disabled", "INTEGER NOT NULL DEFAULT 0")],
        }
        for table, cols in want.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, decl in cols:
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # — соединения —
    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA synchronous = NORMAL")
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("PRAGMA busy_timeout = 5000")
            self._local.conn = c
        return c

    def _begin(self) -> sqlite3.Connection:
        """Начать транзакцию записи (IMMEDIATE — сразу берём write-lock, без апгрейда)."""
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        return c

    def close(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    # ── АККАУНТЫ ──────────────────────────────────────────────────────────────
    def create_account(self, account_id: str, *, password_hash: Optional[str] = None,
                        tier: str = "free", invite_code: Optional[str] = None,
                        invited_by: Optional[str] = None, created_at: Optional[int] = None) -> None:
        c = self._begin()
        try:
            c.execute(
                "INSERT INTO accounts(account_id, created_at, password_hash, tier, invite_code, invited_by) "
                "VALUES(?,?,?,?,?,?)",
                (account_id, created_at or _now(), password_hash, tier, invite_code, invited_by))
            c.commit()
        except Exception:
            c.rollback()
            raise

    def get_account(self, account_id: str) -> Optional[dict]:
        r = self._conn().execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        return dict(r) if r else None

    def account_exists(self, account_id: str) -> bool:
        return self._conn().execute(
            "SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone() is not None

    def set_tier(self, account_id: str, tier: str) -> None:
        c = self._begin()
        try:
            c.execute("UPDATE accounts SET tier=? WHERE account_id=?", (tier, account_id))
            c.commit()
        except Exception:
            c.rollback(); raise

    def set_granted_tier(self, account_id: str, grant: dict) -> None:
        c = self._begin()
        try:
            c.execute("UPDATE accounts SET granted_tier_json=?, tier=? WHERE account_id=?",
                      (json.dumps(grant), grant.get("tier", "premium"), account_id))
            self._audit(c, "admin", "grant", account_id, grant)
            c.commit()
        except Exception:
            c.rollback(); raise

    def set_invite_code(self, account_id: str, code: str) -> None:
        c = self._begin()
        try:
            c.execute("UPDATE accounts SET invite_code=? WHERE account_id=? AND invite_code IS NULL",
                      (code, account_id))
            c.commit()
        except Exception:
            c.rollback(); raise

    def set_inviter(self, account_id: str, inviter_id: str) -> bool:
        """Привязать пригласившего (только если ещё не задан и это не сам аккаунт)."""
        if account_id == inviter_id:
            return False
        c = self._begin()
        try:
            cur = c.execute("UPDATE accounts SET invited_by=? WHERE account_id=? AND invited_by IS NULL",
                            (inviter_id, account_id))
            c.commit()
            return cur.rowcount == 1
        except Exception:
            c.rollback(); raise

    def referrals_of(self, account_id: str) -> list[str]:
        rows = self._conn().execute(
            "SELECT account_id FROM accounts WHERE invited_by=?", (account_id,)).fetchall()
        return [r["account_id"] for r in rows]

    def set_fraud(self, account_id: str, flag: bool, reason: Optional[str] = None) -> None:
        c = self._begin()
        try:
            c.execute("UPDATE accounts SET fraud_flag=?, fraud_reason=? WHERE account_id=?",
                      (1 if flag else 0, reason, account_id))
            self._audit(c, "admin", "fraud_flag", account_id, {"flag": flag, "reason": reason})
            c.commit()
        except Exception:
            c.rollback(); raise

    def set_paid_months(self, account_id: str, months: int) -> None:
        c = self._begin()
        try:
            c.execute("UPDATE accounts SET paid_months=? WHERE account_id=?", (months, account_id))
            c.commit()
        except Exception:
            c.rollback(); raise

    # ── БАЛАНС + ЖУРНАЛ ───────────────────────────────────────────────────────
    def apply_grant(self, account_id: str, *, days: int = 0, minutes: int = 0,
                    kind: str = "grant", reason: Optional[str] = None,
                    tier: Optional[str] = None, ref: Optional[str] = None) -> dict:
        """Атомарно: запись в ledger + обновление кэша баланса в accounts.
        tier!=None и текущий tier=='free' → поднимаем до tier (как _grant_days)."""
        c = self._begin()
        try:
            self._apply_grant_tx(c, account_id, days=days, minutes=minutes,
                                 kind=kind, reason=reason, tier=tier, ref=ref)
            c.commit()
        except Exception:
            c.rollback(); raise
        return self.balance(account_id)

    def _apply_grant_tx(self, c, account_id, *, days, minutes, kind, reason, tier, ref):
        c.execute(
            "INSERT INTO ledger(account_id, at, kind, delta_days, delta_minutes, tier, reason, ref) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (account_id, _now(), kind, days, minutes, tier, reason, ref))
        if minutes and not days:
            c.execute("UPDATE accounts SET active_minutes_left = active_minutes_left + ?, "
                      "sub_mode='balance' WHERE account_id=?", (minutes, account_id))
        elif days:
            c.execute("UPDATE accounts SET days_left = days_left + ?, sub_mode='period' "
                      "WHERE account_id=?", (days, account_id))
        if tier:
            # как _grant_days: грант лишь ПОДНИМАЕТ free→tier, не понижает существующий
            c.execute("UPDATE accounts SET tier=? WHERE account_id=? AND tier='free'", (tier, account_id))

    def spend_minutes(self, account_id: str, minutes: int, *, ref: Optional[str] = None) -> int:
        """Списать активные минуты (узел отчитался). Не уходит ниже нуля. Возвращает остаток."""
        c = self._begin()
        try:
            row = c.execute("SELECT active_minutes_left, sub_mode FROM accounts WHERE account_id=?",
                            (account_id,)).fetchone()
            if row is None:
                c.rollback()
                raise KeyError("no_account")
            spent = min(minutes, row["active_minutes_left"]) if row["sub_mode"] == "balance" else 0
            if spent:
                c.execute("UPDATE accounts SET active_minutes_left = active_minutes_left - ? "
                          "WHERE account_id=?", (spent, account_id))
                c.execute("INSERT INTO ledger(account_id, at, kind, delta_minutes, ref) VALUES(?,?,?,?,?)",
                          (account_id, _now(), "spend", -spent, ref))
            left = c.execute("SELECT active_minutes_left FROM accounts WHERE account_id=?",
                             (account_id,)).fetchone()["active_minutes_left"]
            c.commit()
            return left
        except Exception:
            c.rollback(); raise

    def balance(self, account_id: str) -> dict:
        r = self._conn().execute(
            "SELECT tier, sub_mode, days_left, active_minutes_left, auto_renew, sub_source "
            "FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if not r:
            return {}
        return {"tier": r["tier"], "mode": r["sub_mode"], "days_left": r["days_left"],
                "active_minutes_left": r["active_minutes_left"],
                "auto_renew": bool(r["auto_renew"]), "source": r["sub_source"]}

    def ledger_sum(self, account_id: str) -> dict:
        """Сумма движений по журналу — для сверки с кэшем баланса."""
        r = self._conn().execute(
            "SELECT COALESCE(SUM(delta_days),0) d, COALESCE(SUM(delta_minutes),0) m "
            "FROM ledger WHERE account_id=?", (account_id,)).fetchone()
        return {"days": r["d"], "minutes": r["m"]}

    def payouts_in_window(self, account_id: str, window_sec: int = 30 * 86400) -> int:
        """Сколько реферальных выплат сделано за окно (анти-фрод лимит из ledger)."""
        since = _now() - window_sec
        r = self._conn().execute(
            "SELECT COUNT(*) n FROM ledger WHERE account_id=? AND kind='referral_out' AND at>=?",
            (account_id, since)).fetchone()
        return r["n"]

    # ── СЕССИИ ────────────────────────────────────────────────────────────────
    def create_session(self, token: str, *, account_id: Optional[str] = None,
                        device_id: Optional[str] = None, label: str = "guest",
                        plan: str = "free", max_sessions: int = 1) -> None:
        c = self._begin()
        try:
            c.execute("INSERT INTO sessions(token, account_id, device_id, label, plan, max_sessions, created_at) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (token, account_id, device_id, label, plan, max_sessions, _now()))
            c.commit()
        except Exception:
            c.rollback(); raise

    def get_session(self, token: str) -> Optional[dict]:
        r = self._conn().execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        return dict(r) if r else None

    def update_session_plan(self, token: str, *, plan: str, paid_until: str,
                            max_sessions: Optional[int] = None) -> None:
        c = self._begin()
        try:
            if max_sessions is None:
                c.execute("UPDATE sessions SET plan=?, paid_until=? WHERE token=?",
                          (plan, paid_until, token))
            else:
                c.execute("UPDATE sessions SET plan=?, paid_until=?, max_sessions=? WHERE token=?",
                          (plan, paid_until, max_sessions, token))
            c.commit()
        except Exception:
            c.rollback(); raise

    # ── УСТРОЙСТВА ────────────────────────────────────────────────────────────
    def register_device(self, account_id: str, device_id: str, *, name: str = "Устройство",
                        platform: str = "unknown") -> str:
        """Первое НЕотозванное устройство аккаунта → owner. Повтор — touch last_seen."""
        c = self._begin()
        try:
            ex = c.execute("SELECT account_id FROM devices WHERE device_id=?", (device_id,)).fetchone()
            if ex and ex["account_id"] == account_id:
                c.execute("UPDATE devices SET last_seen=?, name=?, revoked=0 WHERE device_id=?",
                          (_now(), name, device_id))
            else:
                has_owner = c.execute(
                    "SELECT 1 FROM devices WHERE account_id=? AND role='owner' AND revoked=0",
                    (account_id,)).fetchone()
                role = "normal" if has_owner else "owner"
                c.execute("INSERT INTO devices(device_id, account_id, name, platform, role, "
                          "created_at, last_seen, revoked) VALUES(?,?,?,?,?,?,?,0)",
                          (device_id, account_id, name, platform, role, _now(), _now()))
            c.commit()
            return device_id
        except Exception:
            c.rollback(); raise

    def list_devices(self, account_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM devices WHERE account_id=? ORDER BY created_at", (account_id,)).fetchall()
        return [dict(r) for r in rows]

    def revoke_device(self, account_id: str, device_id: str) -> bool:
        c = self._begin()
        try:
            cur = c.execute("UPDATE devices SET revoked=1, revoked_at=? "
                            "WHERE device_id=? AND account_id=? AND revoked=0",
                            (_now(), device_id, account_id))
            c.commit()
            return cur.rowcount == 1
        except Exception:
            c.rollback(); raise

    def promote_device(self, account_id: str, device_id: str) -> bool:
        """Сделать устройство owner'ом. Старый owner атомарно понижается (инвариант ≤1 owner)."""
        c = self._begin()
        try:
            tgt = c.execute("SELECT 1 FROM devices WHERE device_id=? AND account_id=? AND revoked=0",
                            (device_id, account_id)).fetchone()
            if not tgt:
                c.rollback()
                return False
            c.execute("UPDATE devices SET role='normal' WHERE account_id=? AND role='owner'", (account_id,))
            c.execute("UPDATE devices SET role='owner' WHERE device_id=? AND account_id=?",
                      (device_id, account_id))
            c.commit()
            return True
        except Exception:
            c.rollback(); raise

    def is_owner_device(self, device_id: str) -> bool:
        r = self._conn().execute(
            "SELECT 1 FROM devices WHERE device_id=? AND role='owner' AND revoked=0",
            (device_id,)).fetchone()
        return r is not None

    def set_attestation(self, device_id: str, device_class: str) -> None:
        c = self._begin()
        try:
            c.execute("UPDATE devices SET attest_class=?, attest_at=? WHERE device_id=?",
                      (device_class, _now(), device_id))
            c.commit()
        except Exception:
            c.rollback(); raise

    # ── АЛИАСЫ (всё-или-ничего; гонку решает PRIMARY KEY) ─────────────────────
    def claim_aliases(self, account_id: str, hmacs: list[str]) -> None:
        if len(set(hmacs)) != len(hmacs):
            raise AliasTaken("duplicate_alias_in_request")
        c = self._begin()
        try:
            for h in hmacs:
                try:
                    c.execute("INSERT INTO aliases(alias_hmac, account_id) VALUES(?,?)", (h, account_id))
                except sqlite3.IntegrityError:
                    c.rollback()
                    raise AliasTaken("alias_taken")
            c.commit()
        except AliasTaken:
            raise
        except Exception:
            c.rollback(); raise

    def alias_owner(self, alias_hmac: str) -> Optional[str]:
        r = self._conn().execute("SELECT account_id FROM aliases WHERE alias_hmac=?",
                                 (alias_hmac,)).fetchone()
        return r["account_id"] if r else None

    # ── КЛЮЧИ ─────────────────────────────────────────────────────────────────
    def issue_key(self, kid: str, *, plan: str, grant_days: int = 0, grant_minutes: int = 0,
                  tier: Optional[str] = None, uses: int = 1, expires_at: int,
                  issued_by: Optional[str] = None, batch_id: Optional[str] = None,
                  label: Optional[str] = None, note: Optional[str] = None) -> None:
        c = self._begin()
        try:
            c.execute("INSERT INTO keys(kid, plan, grant_days, grant_minutes, tier, uses_total, "
                      "uses_left, expires_at, revoked, issued_at, issued_by, batch_id, label, note) "
                      "VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
                      (kid, plan, grant_days, grant_minutes, tier, uses, uses, expires_at, _now(),
                       issued_by, batch_id, label, note))
            self._audit(c, issued_by or "admin", "issue_key", kid,
                        {"plan": plan, "grant_days": grant_days, "grant_minutes": grant_minutes,
                         "tier": tier, "uses": uses, "batch_id": batch_id, "label": label})
            c.commit()
        except Exception:
            c.rollback(); raise

    def revoke_key(self, kid: str) -> bool:
        c = self._begin()
        try:
            cur = c.execute("UPDATE keys SET revoked=1 WHERE kid=? AND uses_left>0", (kid,))
            if cur.rowcount:
                self._audit(c, "admin", "revoke_key", kid, None)
            c.commit()
            return cur.rowcount == 1
        except Exception:
            c.rollback(); raise

    def revoke_batch(self, batch_id: str) -> int:
        """Отозвать всю партию (ещё не погашенные ключи)."""
        c = self._begin()
        try:
            cur = c.execute("UPDATE keys SET revoked=1 WHERE batch_id=? AND uses_left>0", (batch_id,))
            if cur.rowcount:
                self._audit(c, "admin", "revoke_batch", batch_id, {"count": cur.rowcount})
            c.commit()
            return cur.rowcount
        except Exception:
            c.rollback(); raise

    def redeem_key(self, kid: str, account_token: str) -> dict:
        """Атомарное погашение «ровно один раз».
        Гарантии: UNIQUE(kid, account) + условный UPDATE uses_left>0.
        Возвращает {plan, grant_days, grant_minutes, tier, idempotent}. Бросает RedeemError."""
        c = self._begin()
        try:
            k = c.execute("SELECT plan, grant_days, grant_minutes, tier, uses_left, revoked, expires_at "
                          "FROM keys WHERE kid=?", (kid,)).fetchone()
            if k is None:
                c.rollback()
                raise RedeemError("key_invalid")
            if k["revoked"]:
                c.rollback()
                raise RedeemError("key_revoked")
            if k["expires_at"] < _now():
                c.rollback()
                raise RedeemError("key_expired")
            # пытаемся вписать привязку: если уже была — это идемпотентный повтор
            try:
                c.execute("INSERT INTO key_redemptions(kid, account_token, redeemed_at) VALUES(?,?,?)",
                          (kid, account_token, _now()))
                new_binding = True
            except sqlite3.IntegrityError:
                new_binding = False
            if new_binding:
                cur = c.execute("UPDATE keys SET uses_left = uses_left - 1 WHERE kid=? AND uses_left > 0",
                                (kid,))
                if cur.rowcount != 1:
                    c.rollback()  # откатывает и INSERT привязки
                    raise RedeemError("key_already_redeemed")
            c.commit()
            return {"plan": k["plan"], "grant_days": k["grant_days"],
                    "grant_minutes": k["grant_minutes"], "tier": k["tier"],
                    "idempotent": not new_binding}
        except RedeemError:
            raise
        except Exception:
            c.rollback(); raise

    def key_state(self, kid: str) -> Optional[dict]:
        r = self._conn().execute("SELECT * FROM keys WHERE kid=?", (kid,)).fetchone()
        return dict(r) if r else None

    def list_batch(self, batch_id: str) -> list[dict]:
        """Сводка по партии: каждый ключ + статус (для панели реселлера/админа)."""
        rows = self._conn().execute(
            "SELECT kid, plan, grant_days, grant_minutes, tier, uses_total, uses_left, revoked, "
            "expires_at, label FROM keys WHERE batch_id=? ORDER BY issued_at", (batch_id,)).fetchall()
        return [dict(r) for r in rows]

    def batch_summary(self, batch_id: str) -> dict:
        """Агрегаты по партии: всего/погашено/отозвано/осталось."""
        r = self._conn().execute(
            "SELECT COUNT(*) total, "
            "SUM(CASE WHEN uses_left < uses_total THEN 1 ELSE 0 END) used, "
            "SUM(revoked) revoked, "
            "SUM(CASE WHEN uses_left > 0 AND revoked = 0 THEN 1 ELSE 0 END) active "
            "FROM keys WHERE batch_id=?", (batch_id,)).fetchone()
        return {"total": r["total"] or 0, "used": r["used"] or 0,
                "revoked": r["revoked"] or 0, "active": r["active"] or 0}

    # ── КОЛЕСО (идемпотентность через PRIMARY KEY(account, day)) ──────────────
    def spin_wheel(self, account_id: str, day: str, seg_index: int, prize: dict) -> dict:
        """Записать спин за сутки. Если уже спинил — AlreadySpun. Идемпотентность — на БД."""
        c = self._begin()
        try:
            try:
                c.execute("INSERT INTO wheel_spins(account_id, day, seg_index, prize_json, at) "
                          "VALUES(?,?,?,?,?)",
                          (account_id, day, seg_index, json.dumps(prize), _now()))
            except sqlite3.IntegrityError:
                c.rollback()
                raise AlreadySpun("already_spun_today")
            # начисление приза в той же транзакции
            if prize.get("kind") == "days":
                self._apply_grant_tx(c, account_id, days=prize["amount"], minutes=0,
                                     kind="wheel", reason="wheel", tier=None, ref=day)
            else:
                self._apply_grant_tx(c, account_id, days=0, minutes=prize["amount"],
                                     kind="wheel", reason="wheel", tier=None, ref=day)
            c.commit()
            return {"index": seg_index, "prize": prize}
        except AlreadySpun:
            raise
        except Exception:
            c.rollback(); raise

    def wheel_today(self, account_id: str, day: str) -> Optional[dict]:
        r = self._conn().execute(
            "SELECT seg_index, prize_json FROM wheel_spins WHERE account_id=? AND day=?",
            (account_id, day)).fetchone()
        if not r:
            return None
        return {"index": r["seg_index"], "prize": json.loads(r["prize_json"])}

    # ── УЗЛЫ ──────────────────────────────────────────────────────────────────
    def upsert_node(self, node: dict, *, secret: Optional[str] = None) -> None:
        """Идемпотентный upsert узла + его множеств protocols/roles. Известные поля —
        в колонки, остальное — в extra_json (forward-compat для манифеста)."""
        known = {"id", "country", "code", "host", "transport", "protocols", "roles",
                 "loadPct", "xrayCore", "whiteIp", "provider"}
        extra = {k: v for k, v in node.items() if k not in known}
        c = self._begin()
        try:
            c.execute(
                "INSERT INTO nodes(node_id, country, code, host, transport, load_pct, xray_core, "
                "white_ip, provider, enabled, registered_at, last_seen, secret, extra_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?) "
                "ON CONFLICT(node_id) DO UPDATE SET country=excluded.country, code=excluded.code, "
                "host=excluded.host, transport=excluded.transport, load_pct=excluded.load_pct, "
                "xray_core=excluded.xray_core, white_ip=excluded.white_ip, provider=excluded.provider, "
                "last_seen=excluded.last_seen, extra_json=excluded.extra_json, "
                "secret=COALESCE(excluded.secret, nodes.secret)",
                (node["id"], node["country"], node["code"], node.get("host"), node.get("transport"),
                 int(node.get("loadPct", 0)), 1 if node.get("xrayCore") else 0,
                 1 if node.get("whiteIp") else 0, node.get("provider"),
                 _now(), _now(), secret, json.dumps(extra) if extra else None))
            c.execute("DELETE FROM node_protocols WHERE node_id=?", (node["id"],))
            for p in node.get("protocols", []):
                c.execute("INSERT OR IGNORE INTO node_protocols(node_id, protocol) VALUES(?,?)",
                          (node["id"], p))
            c.execute("DELETE FROM node_roles WHERE node_id=?", (node["id"],))
            for r in node.get("roles", []):
                c.execute("INSERT OR IGNORE INTO node_roles(node_id, role) VALUES(?,?)", (node["id"], r))
            self._audit(c, node["id"], "node_register", node["id"],
                        {"code": node.get("code"), "roles": node.get("roles")})
            c.commit()
        except Exception:
            c.rollback(); raise

    def get_node_secret(self, node_id: str) -> Optional[str]:
        r = self._conn().execute("SELECT secret FROM nodes WHERE node_id=?", (node_id,)).fetchone()
        return r["secret"] if r and r["secret"] else None

    def set_node_secret(self, node_id: str, secret: str) -> None:
        c = self._begin()
        try:
            c.execute("UPDATE nodes SET secret=? WHERE node_id=?", (secret, node_id))
            c.commit()
        except Exception:
            c.rollback(); raise

    def list_nodes(self, *, enabled_only: bool = True) -> list[dict]:
        q = "SELECT * FROM nodes" + (" WHERE enabled=1" if enabled_only else "")
        rows = self._conn().execute(q).fetchall()
        out = []
        for r in rows:
            nid = r["node_id"]
            protocols = [x["protocol"] for x in self._conn().execute(
                "SELECT protocol FROM node_protocols WHERE node_id=?", (nid,)).fetchall()]
            roles = [x["role"] for x in self._conn().execute(
                "SELECT role FROM node_roles WHERE node_id=?", (nid,)).fetchall()]
            node = {"id": nid, "country": r["country"], "code": r["code"],
                    "loadPct": r["load_pct"], "protocols": protocols, "roles": roles}
            if r["host"]: node["host"] = r["host"]
            if r["transport"]: node["transport"] = r["transport"]
            if r["xray_core"]: node["xrayCore"] = True
            if r["white_ip"]: node["whiteIp"] = True
            if r["provider"]: node["provider"] = r["provider"]
            if r["extra_json"]: node.update(json.loads(r["extra_json"]))
            out.append(node)
        return out

    # ── САМО-ПОЧИНКА: heartbeat + свип протухших ──────────────────────────────
    def node_heartbeat(self, node_id: str, *, load_pct: Optional[int] = None) -> bool:
        """Узел отчитался о жизни. Обновляем last_seen (и нагрузку). enabled НЕ трогаем —
        это делает свип (по протуханию last_seen), чтобы heartbeat не воскрешал
        узел, выключенный оператором вручную."""
        c = self._begin()
        try:
            if load_pct is None:
                cur = c.execute("UPDATE nodes SET last_seen=? WHERE node_id=?", (_now(), node_id))
            else:
                cur = c.execute("UPDATE nodes SET last_seen=?, load_pct=? WHERE node_id=?",
                                (_now(), int(load_pct), node_id))
            c.commit()
            return cur.rowcount == 1
        except Exception:
            c.rollback(); raise

    def set_node_enabled(self, node_id: str, enabled: bool) -> None:
        """Ручное вкл/выкл оператором. Сбрасывает health_disabled → свип не тронет."""
        c = self._begin()
        try:
            c.execute("UPDATE nodes SET enabled=?, health_disabled=0 WHERE node_id=?",
                      (1 if enabled else 0, node_id))
            self._audit(c, "admin", "node_enabled", node_id, {"enabled": enabled})
            c.commit()
        except Exception:
            c.rollback(); raise

    def sweep_stale_nodes(self, max_age_sec: int) -> dict:
        """Авто-починка: узлы без свежего heartbeat → выключить (выпадут из манифеста);
        вернувшиеся (снова шлют heartbeat) → включить обратно. Трогает ТОЛЬКО узлы,
        выключенные свипом (health_disabled=1), не ручные. Возвращает {disabled, restored}."""
        cutoff = _now() - max_age_sec
        c = self._begin()
        try:
            stale = [r["node_id"] for r in c.execute(
                "SELECT node_id FROM nodes WHERE enabled=1 AND COALESCE(last_seen,0) < ?",
                (cutoff,)).fetchall()]
            for nid in stale:
                c.execute("UPDATE nodes SET enabled=0, health_disabled=1 WHERE node_id=?", (nid,))
                self._audit(c, "system", "node_auto_disabled", nid, None)
            recovered = [r["node_id"] for r in c.execute(
                "SELECT node_id FROM nodes WHERE enabled=0 AND health_disabled=1 "
                "AND COALESCE(last_seen,0) >= ?", (cutoff,)).fetchall()]
            for nid in recovered:
                c.execute("UPDATE nodes SET enabled=1, health_disabled=0 WHERE node_id=?", (nid,))
                self._audit(c, "system", "node_auto_restored", nid, None)
            c.commit()
            return {"disabled": stale, "restored": recovered}
        except Exception:
            c.rollback(); raise

    # ── ПРАВИЛА МАРШРУТИЗАЦИИ ─────────────────────────────────────────────────
    def add_rule(self, kind: str, match: str, action: str, *, priority: int = 0) -> int:
        c = self._begin()
        try:
            cur = c.execute("INSERT INTO routing_rules(kind, match, action, priority) VALUES(?,?,?,?)",
                            (kind, match, action, priority))
            c.commit()
            return cur.lastrowid
        except Exception:
            c.rollback(); raise

    def list_rules(self) -> list[dict]:
        rows = self._conn().execute(
            "SELECT kind, match, action FROM routing_rules WHERE enabled=1 ORDER BY priority, id").fetchall()
        return [{"kind": r["kind"], "match": r["match"], "action": r["action"]} for r in rows]

    # ── ПОДДЕРЖКА ─────────────────────────────────────────────────────────────
    def add_message(self, token: str, sender: str, text: str) -> None:
        c = self._begin()
        try:
            c.execute("INSERT INTO support_messages(token, sender, text, at) VALUES(?,?,?,?)",
                      (token, sender, text, _now()))
            c.commit()
        except Exception:
            c.rollback(); raise

    def thread(self, token: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT sender, text, at FROM support_messages WHERE token=? ORDER BY id", (token,)).fetchall()
        return [{"from": r["sender"], "text": r["text"], "at": r["at"]} for r in rows]

    # ── АУДИТ + НАСТРОЙКИ ─────────────────────────────────────────────────────
    def _audit(self, c, actor: str, action: str, target: Optional[str], detail) -> None:
        c.execute("INSERT INTO audit_log(at, actor, action, target, detail_json) VALUES(?,?,?,?,?)",
                  (_now(), actor, action, target, json.dumps(detail) if detail is not None else None))

    def audit(self, actor: str, action: str, target: Optional[str] = None, detail=None) -> None:
        c = self._begin()
        try:
            self._audit(c, actor, action, target, detail)
            c.commit()
        except Exception:
            c.rollback(); raise

    def recent_audit(self, limit: int = 50) -> list[dict]:
        rows = self._conn().execute(
            "SELECT at, actor, action, target, detail_json FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"at": r["at"], "actor": r["actor"], "action": r["action"], "target": r["target"],
                 "detail": json.loads(r["detail_json"]) if r["detail_json"] else None} for r in rows]

    def set_setting(self, key: str, value) -> None:
        c = self._begin()
        try:
            c.execute("INSERT INTO settings(key, value_json) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                      (key, json.dumps(value)))
            c.commit()
        except Exception:
            c.rollback(); raise

    def get_setting(self, key: str, default=None):
        r = self._conn().execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value_json"]) if r else default

    # ── ОПС: сводка по таблицам ──────────────────────────────────────────────
    def stats(self) -> dict:
        c = self._conn()
        t = lambda name: c.execute(f"SELECT COUNT(*) n FROM {name}").fetchone()["n"]
        return {"accounts": t("accounts"), "sessions": t("sessions"), "devices": t("devices"),
                "aliases": t("aliases"), "keys": t("keys"), "redemptions": t("key_redemptions"),
                "ledger": t("ledger"), "wheel_spins": t("wheel_spins"), "nodes": t("nodes"),
                "rules": t("routing_rules"), "support": t("support_messages"), "audit": t("audit_log")}
