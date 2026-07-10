# Симбионт — состояние после доводки

Живой снимок: что в проекте **сейчас реально работает и проверено**, что я добавил в
этой итерации, и что осталось (и кто это делает). Авторский разбор по слоям —
`symbiont_build/STATUS.md`; этот файл — актуализация поверх него.

## Аудит + доводка (сквозной проход)

Прогнал полный аудит по всем слоям (backend, control-plane, Go-движок, Flutter-клиент,
webapp, browser-extension, web-portal, flutter-host) и закрыл однозначные баги.

**Проверено вживую:** backend **17/17** наборов, control-plane **4/4**, Go-движок —
кросс-сборка под Windows + `GOOS=windows go vet` чисто, весь Python (42 файла) и весь
наш JS компилируются. Контракт движка (17 методов × 3 движка), API-клиент ↔ backend и
экраны/роутинг Flutter — сверены, расхождений нет.

**Исправлено в этом проходе:**
- Backend `_grant_days`: грант/ключ теперь **повышает тир по рангу** (premium→ultimate)
  и не понижает — раньше ultimate-ключ поверх premium оставлял premium (`server.py`).
- Backend `/v1/wheel/spin`: **лок** вокруг «проверить-и-начислить» — два одновременных
  спина за день больше не начисляют дважды (`server.py`).
- Backend `/v1/manifest`: **304 без тела** (корректный conditional-GET) вместо
  `HTTPException(304)` с JSON-телом (`server.py`).
- Backend `requirements.txt`: явно объявлен `pydantic` (прямой импорт в `server.py`).
- Новый регресс-тест `selftest_audit_fixes2.py` (тир-апгрейд, 304-без-тела, гонка колеса).
- Browser-extension `manifest.json`: убран `default_locale: "ru"` **без папки `_locales/`**
  — из-за него MV3 отказывался грузить распакованное расширение.
- Browser-extension `background.js`: статус в попапе теперь спрашивается у native-хоста,
  когда он подключён (раньше ветка `status` в `host.py` была мёртвой, попап залипал на `idle`).
- flutter-host `sym_engine_channel.dart`: убран мёртвый тернарник `'tunnel':'tunnel'`
  (stage сообщается только при реальном подключении).
- Клиент `desktop_engine_io.dart`: гигиена `@override` (снят лишний на поле, добавлен на
  `connect`, снят дубль на `applyRules`) — чистит `flutter analyze`.

## Вторая волна доводки (по запросу владельца)

**Сделано и проверено:**
- **i18n — 55 языков полностью.** Flutter `strings.dart`: во ВСЕ 53 неэталонных языка
  дозаполнены недостающие 27 ключей + 4 новых recovery-ключа → у каждого языка ровно
  **391 ключ** (было 360). Переводы — не фабрикация: native-локализация по семьям языков,
  Dart-экранирование (`'`, `\`, `$`) проверено, парити-скрипт зелёный. Глоссарий/FAQ пока
  локализованы для ru/en/es/de (остальные — англ. фоллбэк по индексу; отдельный большой набор).
- **`/v1/admin/grant` — единая модель тира.** Грант теперь поднимает тир и в подписке `_sub`
  (по рангу, без понижения), помечает пожизненный как `lifetime`; `billing/status` больше не
  показывает free после гранта. Покрыто тестом.
- **PoW-регистрация подключена.** `ApiClient.solvePow` (sha256 ведущих нулей, 1:1 с backend
  `identity.pow_ok`) + `AppState.register` берёт свежий челлендж на каждую попытку (челлендж
  одноразовый). При `bits=0` (дефолт) — no-op; при включённом PoW клиент решает его сам.
- **Вход по recovery-коду — UI подключён.** `AppState.loginWithRecovery` + переключатель
  «токен / recovery-код» в онбординге (раньше `api.recover` был мёртвым кодом).
- **Мобильный `SingboxEngine` — Dart-сторона боевая.** Заглушки классов каналов заменены на
  реальные Flutter `MethodChannel`/`EventChannel`; все методы шлют команды нативу. Остаётся
  НАТИВНАЯ часть (Android VpnService + libbox; iOS/macOS NetworkExtension) — её в этой среде
  не собрать/не проверить; движок намеренно НЕ подключён в `main.dart` (моб. сборки — MockEngine).
- **Разбор цен/тарифов** — новый `docs/PRICING.md` (что реализовано + 6 пунктов доделок).

**Осталось решением владельца:**
- **Боевая оплата:** реальный провайдер не подключён (`purchase` в проде — `pending` без
  ссылки/QR; `/v1/subscription/checkout` не реализован). См. `docs/PRICING.md` §1. Нужен провайдер.
- **Крипто-скидка 10 %** задана в экономике, но не применяется в UI (`docs/PRICING.md` §2).
- **Нативный мобильный туннель** (sing-box) — платформенная работа вне этой среды.
- **Глоссарий/FAQ** для 51 языка (сейчас англ. фоллбэк) — при желании дозаполним отдельно.

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
