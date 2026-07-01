# Симбионт — состояние после доводки

Живой снимок: что в проекте **сейчас реально работает и проверено**, что я добавил в
этой итерации, и что осталось (и кто это делает). Авторский разбор по слоям —
`symbiont_build/STATUS.md`; этот файл — актуализация поверх него.

## Проверено в этой среде (Linux, Python 3.11 + Go 1.24, без Windows/Flutter)

| Слой | Статус | Доказательство |
|---|---|---|
| **Бэкенд** (FastAPI) | ✅ 13 тест-наборов зелёные | `cd symbiont_build/backend && pip install -r requirements.txt && for f in smoke_test.py selftest_*.py; do python "$f"; done` |
| **Контрол-плейн узлов** (server/) | ✅ 4 тест-набора зелёные | `cd symbiont_build/server && for f in selftest_*.py; do python "$f"; done` |
| **Генератор конфига узла** | ✅ валидный sing-box config | `python symbiont_build/server/gen_server.py --localhost --out ./out` |
| **Go-движок обхода DPI** | ✅ кросс-компиляция + `go vet` | `cd engine && GOOS=windows GOARCH=amd64 go build ./... && go vet ./...` |
| **Веб-чекер + лендинг** | ✅ E2E в браузере | `symbiont_build/web/{index,check}.html` (checker проверяет подпись Ed25519 в браузере + живой статус) |
| **Итого автотестов** | **17/17 наборов, ~339 проверок** | — |

Не запускается здесь по природе среды: **Flutter-клиент** (нет SDK — правки статически
вычитаны, собирать на Windows) и **реальный VPN-туннель/узлы** (нужны боевые серверы).

## Что добавлено в этой итерации

**Бэкенд**
- `POST /v1/key/check` — публичный game-style чекер ключа без активации
  (valid/used/expired/revoked/not_found/invalid), read-only, без авторизации.
- Жизненный цикл платежа: `POST /v1/billing/webhook` (HMAC, идемпотентно) +
  `GET /v1/billing/payment/{id}` — pending → подтверждение → completed.
- `GET /v1/billing/ledger` — история начислений (purchase/grant/wheel/ref_earn/debit).
- Permissive CORS (безопасно: авторизация по Bearer-заголовку, не по кукам).
- Новые тесты: `selftest_key_check.py` (12), `selftest_payments.py` (16).
- Фикс: `httpx` в `requirements.txt` (без него API-самотесты не импортировались).

**Веб**
- Чекер переведён на реальный `POST /v1/key/check` (был вызов несуществующего
  `/v1/voucher/check`); авто-загрузка pubkey; `?api=` override.
- Новый самодостаточный лендинг (`web/index.html`) на финальной дизайн-системе.

**Flutter (статически)**
- `api_client.dart`: `checkKey()`, `paymentStatus()`, `ledger()`.
- `app_state.dart`: `checkKey()` (online, read-only).
- Экран «Аккаунт»: кнопка «Проверить ключ» + диалог статуса (спека этого просила).
- i18n `check.*` (RU+EN).

## Что осталось (и кто делает)

1. **Собрать и прогнать Flutter-клиент на Windows** (`flutter run -d windows`) —
   в т.ч. новую кнопку «Проверить ключ». Делает владелец (среда сборки — Windows).
2. **Боевые узлы** (🔴): поднять VPS по `server/bootstrap_vps.sh` / `install.sh`,
   `gen_server.py` → `register_node.py` → узел появляется в подписанном манифесте.
   Требует реальных серверов вне РФ — операторская задача.
3. **Опционально**: полноценный веб-клиент всех экранов (сейчас в вебе — лендинг +
   чекер; остальной UI живёт в Flutter). Решение — за владельцем.
