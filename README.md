# Симбионт (Symbiont)

Единый сервис связности против блокировок: **живой каскад** `прямой → обход DPI → VPN → реле`,
детект «занавеса» и самоподстройка под сеть. Сигнатурная идея — не тумблер «вкл/выкл», а
адаптивное «ядро связи», которое показывает, **каким путём** через каскад установлена защита.

Этот репозиторий — рабочий код проекта: Flutter-клиент, FastAPI-бэкенд, Go-движок обхода DPI,
веб-чекер ключей и полный комплект спецификаций.

## Карта репозитория

```
symbiont_build/     Flutter-клиент + бэкенд + веб (как авторская сборка)
  lib/              приложение: экраны, state, движки (engine.dart контракт + mock/singbox/desktop)
  backend/          FastAPI: аккаунты, ключи (Ed25519), манифест, колесо, рефералы, устройства, поддержка
  web/index.html    лендинг (каскад + приватность), самодостаточный, без CDN
  web/check.html    самодостаточная страница офлайн-проверки ключа (без CDN)
  schema/           JSON-схема подписанного манифеста
  assets/fonts/     JetBrains Mono (бандл для цифр/метрик)
  *.md              авторские дев-доки (STATUS, RUNBOOK, ARCHITECTURE, DEPLOY, …)
engine/             Go-движок обхода DPI (WinDivert, desync/conns_reset/autohostlist) — только Windows
docs/               спецификации: мастер-интерфейс, UI-спека, колесо, архитектура, психология, инвентарь
mockups/            самодостаточные HTML-макеты (системные шрифты, ноль CDN)
claude_design/      экспорт из Claude Design с исправленными текстами (рендерится в его рантайме)
```

## Что где работает (проверено в этой среде — Linux, без Windows/Flutter)

| Слой | Статус | Как проверить |
|---|---|---|
| **Бэкенд** (FastAPI, Python 3.11) | ✅ запускается, 31 маршрут, 11/11 тест-наборов зелёные (~133 проверки) | см. ниже |
| **Go-движок обхода DPI** | ✅ кросс-компиляция под Windows чистая, `go vet` чист | `cd engine && GOOS=windows go build ./...` |
| **Веб-чекер / макеты** | ✅ статический HTML, открывается в браузере | открыть `symbiont_build/web/check.html` |
| **Flutter-клиент** | ⚠️ здесь не собрать (нет Flutter SDK) — только статическая вычитка; собирается на Windows | `flutter run -d windows` на машине с Flutter |
| **VPN-туннель / узлы / маршрутизация** | 🔴 нужен реальный сервер (подписанный манифест + флот узлов) | инфраструктура, вне этой среды |

Граница честности: **новый движок туннеля не пишем** — за контрактом `SymbiontEngine`
живёт готовый OSS (Sing-box для туннеля, zapret/byedpi для локального пробоя DPI).
См. `symbiont_build/ARCHITECTURE.md` и `docs/SYMBIONT_ARCHITECTURE.md`.

## Быстрый старт — бэкенд

```bash
cd symbiont_build/backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# самотесты (не требуют сети)
python smoke_test.py
for f in selftest_*.py; do python "$f"; done

# сервер
uvicorn server:app --reload   # http://127.0.0.1:8000/docs
```

## Быстрый старт — Go-движок (на Windows)

```bash
cd engine
go build ./...        # на Windows даст symbiont-engine.exe
# в этой среде — кросс-проверка: GOOS=windows GOARCH=amd64 go build ./...
```

## Клиент (Flutter, на Windows/десктопе)

```bash
cd symbiont_build
flutter pub get
flutter run -d windows
```
Приложение стартует на `MockEngine` (всё живое без сервера). Боевой движок —
замена одной строки в `lib/main.dart` + шаги `NATIVE_INTEGRATION.md`.

## Документация

Точка входа — **`docs/СИМБИОНТ_МАСТЕР_идеальный_интерфейс.md`** (единая спека интерфейса).
Текущее фактическое состояние по слоям — **`symbiont_build/STATUS.md`**.
