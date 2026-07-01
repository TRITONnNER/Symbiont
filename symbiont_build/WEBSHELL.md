# Десктоп/мобайл теперь показывают дизайн-оболочку (webapp/)

С этого шага приложение по умолчанию рендерит **дизайн из Claude Design**
(`webapp/`) в WebView, а не старые Flutter-экраны. Движок (`lib/engine/`)
переиспользуется — оболочка управляет им через `window.SYM_ENGINE`.

## Сборка (Windows и др.)

```bash
# 1) вшить оболочку в assets (после любых правок в webapp/):
python symbiont_build/tools/sync_webapp.py

# 2) подтянуть новую зависимость (flutter_inappwebview) и запустить:
cd symbiont_build
flutter pub get
flutter run -d windows        # или macos / linux / android / ios
```

> Ассеты `assets/webapp/` уже лежат в репозитории (синхронизированы), так что
> первый запуск работает и без шага 1. Шаг 1 нужен, когда меняете `webapp/`.

Требуется среда выполнения **WebView2** (на Windows 10/11 обычно уже стоит).

## Как это устроено

```
WebShellPage ──▶ loopback-HTTP (assets/webapp + прокси /v1 → бэкенд)
      │                       ▲ один origin ⇒ без CORS
      ▼
  InAppWebView(webapp/)  ──window.SYM_ENGINE──▶ SymEngineChannel ──▶ SymbiontEngine
```

- `lib/webshell/web_shell_page.dart` — экран: поднимает сервер, показывает WebView,
  внедряет `SYM_CONFIG` (apiBase:'' — API на том же origin через прокси) и мост движка.
- `lib/webshell/sym_web_server_io.dart` — раздаёт вшитую оболочку + проксирует `/v1`.
- `lib/webshell/sym_engine_channel.dart` — `window.SYM_ENGINE` ↔ `SymbiontEngine`.

## Откат на старые экраны

```bash
flutter run --dart-define=SYMBIONT_WEBSHELL=false
```
Старый UI (`lib/screens/`, `app_shell.dart`) не удалён — остаётся как фолбэк.

## Известные нюансы

- WebView на Windows в `flutter_inappwebview` использует WebView2. Если на вашей
  сборке экран пустой — проверьте, что WebView2 Runtime установлен, либо временно
  вернитесь на старые экраны флагом выше и напишите — переключим на `webview_windows`.
- Бэкенд берётся из `Store.baseUrl ?? kDefaultBaseUrl`. Если он недоступен,
  оболочка сама уходит в демо-режим (дизайн виден, данные демонстрационные).
