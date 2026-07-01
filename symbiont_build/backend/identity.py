# backend/identity.py — Этап 1 системы аккаунтов: крипто-личность и алиасы.
# Принципы (см. SYMBIONT_ARCHITECTURE.md §2):
#  - Истинная личность = 256-битный секрет; account_id = его хеш.
#  - Алиасы (ник/почта/телефон) НЕ верифицируются и хранятся ТОЛЬКО как HMAC
#    (из дампа БД нельзя достать список почт/телефонов — «чего нет, то не утечёт»).
#  - Уникальность алиасов через нормализацию + HMAC (два одинаковых нельзя).
#  - Пароль опционален; по умолчанию — recovery-код. Пароль через scrypt (для prod — Argon2id).
import os, hmac, hashlib, secrets, re, base64, json, time

# Серверный секрет для HMAC алиасов. В prod — из окружения/секрет-хранилища, не дефолт.
ALIAS_SECRET = os.environ.get("SYMBIONT_ALIAS_SECRET", "dev-alias-secret-CHANGE-IN-PROD").encode()


# ── Крипто-личность ───────────────────────────────────────────────────────────
def new_secret() -> bytes:
    """256-битный секрет — истинная личность аккаунта (как номер Mullvad, но полной энтропии)."""
    return secrets.token_bytes(32)

def account_id(secret: bytes) -> str:
    """Публичный id аккаунта = хеш секрета (сам секрет не хранится/не передаётся)."""
    return hashlib.sha256(b"symbiont:acct:" + secret).hexdigest()[:32]

def recovery_code(secret: bytes) -> str:
    """Человекочитаемый код восстановления: base32 группами по 4 (удобно записать)."""
    b32 = base64.b32encode(secret).decode().rstrip("=")
    return "-".join(b32[i:i + 4] for i in range(0, len(b32), 4))

def secret_from_recovery(code: str) -> bytes:
    raw = code.replace("-", "").replace(" ", "").upper()
    pad = "=" * ((8 - len(raw) % 8) % 8)
    return base64.b32decode(raw + pad)


# ── Алиасы (нормализация + HMAC уникальности) ─────────────────────────────────
def normalize_alias(value: str, kind: str) -> str:
    """Канон для сравнения: '+1', '+ 1' → одно; регистр/пробелы убираем."""
    v = value.strip().lower()
    if kind == "phone":
        plus = v.lstrip().startswith("+")
        digits = re.sub(r"\D", "", v)
        return ("+" if plus else "") + digits
    return re.sub(r"\s+", " ", v)

def alias_hmac(value: str, kind: str) -> str:
    norm = normalize_alias(value, kind)
    return hmac.new(ALIAS_SECRET, f"{kind}:{norm}".encode(), hashlib.sha256).hexdigest()


# ── Пароль (опционально; scrypt из stdlib, для prod — Argon2id) ────────────────
def hash_password(password: str, salt: bytes = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()

def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ── Proof-of-Work (анти-фрод при регистрации) ─────────────────────────────────
def pow_ok(challenge: str, nonce: str, bits: int) -> bool:
    """Проверка: sha256(challenge:nonce) имеет >= bits ведущих нулевых бит. Дёшево проверять."""
    h = hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()
    n = int.from_bytes(h, "big")
    return n < (1 << (256 - bits))

def solve_pow(challenge: str, bits: int) -> str:
    """Решатель (для клиента/тестов). Дорого решать при больших bits — в этом и смысл."""
    i = 0
    while True:
        if pow_ok(challenge, str(i), bits):
            return str(i)
        i += 1


# ── Хранилище аккаунтов (хранит ТОЛЬКО хеши) ──────────────────────────────────
class AccountStore:
    def __init__(self, path: str = None):
        self.path = path
        self.accounts = {}   # account_id -> {created_at, password_hash?, reputation, tier}
        self.aliases = {}    # alias_hmac -> account_id
        if path and os.path.exists(path):
            self._load()

    def register(self, aliases, password=None, pow_solution=None, pow_challenge=None, pow_bits=0):
        """aliases: список (value, kind). Возвращает account_id + recovery_code."""
        if pow_bits > 0:
            if not pow_solution or not pow_ok(pow_challenge or "", pow_solution, pow_bits):
                raise ValueError("pow_failed")
        hmacs = [alias_hmac(v, k) for v, k in aliases]
        if len(set(hmacs)) != len(hmacs):
            raise ValueError("duplicate_alias_in_request")
        for h in hmacs:
            if h in self.aliases:
                raise ValueError("alias_taken")
        secret = new_secret()
        aid = account_id(secret)
        self.accounts[aid] = {
            "created_at": int(time.time()),
            "password_hash": hash_password(password) if password else None,
            "reputation": 0,
            "tier": "free",
        }
        for h in hmacs:
            self.aliases[h] = aid
        self._save()
        return {"account_id": aid, "recovery_code": recovery_code(secret)}

    def login_alias(self, value, kind, password=None):
        aid = self.aliases.get(alias_hmac(value, kind))
        if not aid:
            return None
        acc = self.accounts[aid]
        if acc.get("password_hash"):
            if not password or not verify_password(password, acc["password_hash"]):
                return None
        return aid

    def login_recovery(self, code):
        try:
            aid = account_id(secret_from_recovery(code))
        except Exception:
            return None
        return aid if aid in self.accounts else None

    def add_alias(self, aid, value, kind):
        if aid not in self.accounts:
            raise ValueError("no_such_account")
        h = alias_hmac(value, kind)
        if h in self.aliases:
            raise ValueError("alias_taken")
        self.aliases[h] = aid
        self._save()

    def _load(self):
        d = json.load(open(self.path, encoding="utf-8"))
        self.accounts = d.get("accounts", {})
        self.aliases = d.get("aliases", {})

    def _save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"accounts": self.accounts, "aliases": self.aliases}, f)
        os.replace(tmp, self.path)
