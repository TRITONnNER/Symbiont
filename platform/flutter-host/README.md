# Симбионт — Flutter-обёртка (Win / macOS / Linux / Android / iOS)

Один экран `WebShellPage` показывает дизайн-оболочку (`webapp/`) в WebView и
связывает её `window.SYM_ENGINE` с уже готовым движком
(`symbiont_build/lib/engine/`). UI — общий для всех платформ; под капотом
меняется только реализация `SymbiontEngine`.

```
webapp/ (дизайн)  ──WebView──  sym_engine_bridge.js
                                      │  window.SYM_ENGINE
                                      ▼
                              SymEngineChannel (Dart)
                                      │  SymbiontEngine
                                      ▼
      Windows: winws/sing-box/byedpi · Android: VpnService · iOS: NEPacketTunnel · …
```

## Почему переиспользуем существующий код

- Движок (`lib/engine/desktop_engine_io.dart`, `singbox_engine.dart`, контракт
  `engine.dart`) остаётся как есть — WebShellPage лишь вызывает его методы.
- Дизайн-оболочка остаётся как есть (`webapp/`) — обёртка её не правит.
- Экраны `lib/screens/*` больше не нужны как основной UI (можно оставить как
  фолбэк/справку). Вместо них — `WebShellPage`.

## Установка в приложение

1. Добавьте зависимости и ассеты из `pubspec.snippet.yaml`.
2. Вшейте оболочку: `bash sync_webapp.sh` (копирует `webapp/` → `assets/webapp/`).
3. Скопируйте `lib/*.dart` и `assets/sym_engine_bridge.js` в проект. Поправьте
   импорт `package:symbiont/engine/engine.dart` под имя вашего пакета.
4. Точка входа:

```dart
MaterialApp(
  home: WebShellPage(
    engine: buildEngineForPlatform(),          // ваш существующий выбор движка
    backendBase: 'https://api.symbiont.net',   // или '' если бэкенд на том же origin
  ),
);
```

## Файлы

| Файл | Роль |
|------|------|
| `lib/web_shell_page.dart` | экран: loopback-сервер + WebView + внедрение конфига/моста |
| `lib/sym_web_server.dart` | раздаёт вшитую оболочку по http (DC-рантайму нужен fetch) |
| `lib/sym_engine_channel.dart` | JS `symEngine` ↔ `SymbiontEngine`; поток статуса → `_emit` |
| `assets/sym_engine_bridge.js` | определяет `window.SYM_ENGINE` поверх JS-канала |
| `pubspec.snippet.yaml` | зависимости и список ассетов |
| `sync_webapp.sh` | вшивает `webapp/` в `assets/webapp/` |

## Замечания по платформам

- **Windows / macOS / Linux.** `backendBase` — боевой API. Движок — тот же, что
  сейчас (winws/sing-box). Для прозрачного обхода нужны права администратора
  (`engine.requestAdmin()`), WebView — `flutter_inappwebview` (webview2 на Win).
- **Android.** Движок за `VpnService` + sing-box; WebView штатный.
- **iOS.** Движок за `NEPacketTunnelProvider` + sing-box; App Extension.
- Ступень каскада (`stage: direct/bypass/tunnel/relay`) прокидывается в
  `SymEngineChannel._pushStatus` — расширьте маппинг, когда движок начнёт
  сообщать активную ступень (сейчас `on → tunnel`).

## Проверка оболочки без Flutter

Быструю визуальную проверку самой оболочки можно делать через `webapp/serve.py`
(тот же контент, что вшивается). Движок там симулируется (SYM_ENGINE отсутствует).
