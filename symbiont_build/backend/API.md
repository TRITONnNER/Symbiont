# Симбионт — контракт клиент ↔ бэкенд

Управляющий слой (слой C). Принципы: **никаких личных данных**, авторизация по
непрозрачному токену, конфигурация — только подписанным манифестом, бэкенд не
знает «кто-куда» (тома IV–VI). Транспорт — HTTPS. Все ответы JSON.

Авторизация: заголовок `Authorization: Bearer <account_token>` (кроме выдачи токена).

---

## 1. Аккаунт без данных

### POST /v1/account/anon
Создаёт анонимный аккаунт. Тело не требует личных данных; `label` — косметический.
```json
// request
{ "label": "гость-7K2Q" }            // необязательно; формат НЕ проверяется
// response 200
{ "token": "b32_OPAQUE_TOKEN", "label": "гость-7K2Q",
  "subscription": { "plan": "free", "maxConcurrentSessions": 1 } }
```
`token` — непрозрачный (например, 160-битный случайный, base32). Уникальность держит он, не `label`.

### PATCH /v1/account/label
```json
{ "label": "мой_акк" } // → 200 { "label": "мой_акк" }
```

---

## 2. Оплата ключами

### POST /v1/key/redeem
Погашает ключ активации. Идемпотентно, атомарно, с привязкой при первой активации.
```json
// request
{ "code": "SYMB-AB12-CD34-EF56" }
// response 200
{ "plan": "pro", "paidUntil": "2026-07-18T00:00:00Z",
  "added": { "days": 30 } }
// response 409 (уже погашен / привязан к другому аккаунту)
{ "error": "key_already_redeemed" }
// response 410 (отозван)            422 (подпись неверна / подделка)
{ "error": "key_revoked" }          { "error": "key_invalid" }
```
Логика — см. `key_redemption_reference.py`. Подделка исключается подписью+реестром;
кража смягчается bind-on-redeem + одноразовостью + отзывом (том VI §4).

### POST /v1/key/check
Проверяет ключ **без активации** и **без авторизации** — публичный «чекер» (сайт/лендинг/
экран аккаунта). Ничего не мутирует. Оффлайн-часть (подпись) ловит подделку/опечатку;
реестр даёт статус в этой системе.
```json
// request
{ "code": "SYMB-AB12-CD34-EF56" }
// response 200 (всегда 200 — статус в теле)
{ "status": "valid", "ok": true,
  "message": "Ключ действителен и готов к активации",
  "valid": true, "registered": true,
  "grants": { "days": 30, "tier": "pro" },
  "uses_left": 1, "uses_total": 1, "expires_at": 1790000000 }
```
`status ∈ valid | already_redeemed | expired | revoked | not_found | invalid`.
`valid` — прошла ли криптоподпись (подлинность); `ok` — можно ли активировать прямо сейчас.

### POST /v1/subscription/checkout
Открывает анонимную оплату через провайдера (Stripe/YooKassa). Возвращает ссылку/сессию.
```json
{ "plan": "pro", "period": "month" }
// → { "checkoutUrl": "https://pay.example/...", "ref": "anon_ref" }
```

### POST /v1/billing/purchase  (auth)
Покупка продукта. В sandbox (`SYMBIONT_SANDBOX_PAY=1`) платёж авто-подтверждается;
в проде возвращает `pending` — подтверждение придёт вебхуком.
```json
{ "product": "premium_month", "method": "sbp" }
// sandbox → { "payment_id":"…", "status":"completed", "sandbox":true, "subscription":{…} }
// prod    → { "payment_id":"…", "status":"pending", "method":"sbp" }
```
`product ∈ premium_month|premium_year|ultimate_month|ultimate_year|balance_100h|balance_300h`.
`method ∈ sbp|mir|visa|mastercard|yoomoney|crypto`.

### GET /v1/billing/payment/{payment_id}  (auth)
Статус конкретного платежа — для экрана «Платёж обрабатывается» → «Оплачено».
`{ "payment_id":"…", "status":"pending|completed|failed", "product":"…", "method":"…", … }`.
Виден только владельцу (иначе `404`).

### POST /v1/billing/webhook
Колбэк платёжного провайдера: подтверждает/проваливает pending-платёж. Тело подписано
HMAC-SHA256 на `PAY_WEBHOOK_SECRET`, заголовок `x-pay-sig`. **Идемпотентно**.
```json
{ "payment_id": "…", "status": "completed" }   // или "failed"
// → { "ok":true, "status":"completed", "subscription":{…} }
// повтор того же события → { "ok":true, "status":"completed", "idempotent":true }
```

### GET /v1/billing/ledger  (auth)
История начислений/списаний (экран «История начислений»).
```json
{ "entries": [ { "at": 1790000000, "kind": "purchase", "product":"…", "days":30, "tier":"premium" },
               { "at": 1789990000, "kind": "wheel", "minutes": 120 } ],
  "subscription": {…} }
```
`kind ∈ purchase | grant | wheel | ref_earn | debit`. Новые записи — сверху.

---

## 3. Подписанный манифест (узлы + правила)

### GET /v1/manifest?since=<version>
Возвращает конфигурацию для клиента. Если `since` актуален — `304`.
Подпись Ed25519 проверяется на клиенте; без валидной подписи манифест отвергается.
```json
{
  "version": 184,
  "issuedAt": "2026-05-31T00:00:00Z",
  "nodes": [
    { "id": "nl-01", "country": "Netherlands", "code": "NL", "loadPct": 18,
      "protocols": ["reality","hysteria2"], "roles": ["edge"] }
  ],
  "rules": [
    { "kind": "domainSuffix", "match": "sberbank.ru",   "action": "direct" },
    { "kind": "tld",          "match": ".gov.ru",        "action": "direct" },
    { "kind": "domainSuffix", "match": "youtube.com",    "action": "bypass" }
  ],
  "categories": {
    "ads":      ["doubleclick.net", "..."],
    "trackers": ["..."],
    "threats":  ["..."],
    "directApps":  ["bank", "gov", "marketplace"],
    "bypassApps":  ["messenger", "video"],
    "boostApps":   ["game", "voice"]
  },
  "sig": "ed25519:BASE64_SIGNATURE_OVER_CANONICAL_BODY"
}
```
Схема — `schema/manifest.schema.json`. Раскатка через canary (1→5→25→100%), том IV §3.6.

---

## 4. Поддержка (диалог по токену)

### GET /v1/support/thread  → 200
```json
{ "messages": [ { "from": "support", "text": "Здравствуйте! …", "at": "…" } ] }
```
### POST /v1/support/message
```json
{ "text": "YouTube не открывается",
  "diag": { "consent": true, "report": "reachable_but_tls_reset", "node": "nl-01" } }
// → 201 { "ok": true }
```
Ветка привязана к `account_token`; почта/телефон не нужны. `diag` — обезличенный отчёт
по согласию (том V §8.2). Внешний контакт пользователь может указать сам, но не обязан.

---

## 5. Аттестация устройства (анти-абьюз free/триала)

### POST /v1/attest
```json
{ "platform": "ios|android", "token_blob": "<App Attest / Play Integrity payload>" }
// → 200 { "ok": true, "deviceClass": "genuine" }   // личность НЕ раскрывается
```
Подтверждает «настоящее устройство», не личность. Десктоп — слабее, компенсируется
лимитом одновременных сессий (том V §6.3). Базовый Privacy Pass стабилен; rate-limited/
credit-токены (контроль конкурентности) — закладывать как v1.5+.

---

## Коды ошибок (общие)
`401` нет/просрочен токен · `409` конфликт состояния (двойное погашение/сессии) ·
`410` отозвано · `422` неверная подпись/формат · `429` rate-limit.
