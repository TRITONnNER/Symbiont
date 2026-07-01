# Симбионт — мультиплатформенная упаковка

Одна оболочка — все платформы. UI/UX живёт **только** в `webapp/` (дизайн из
Claude Design). Каждая платформа — тонкая обёртка, которая:

1. показывает `webapp/` (WebView или браузер),
2. задаёт `window.SYM_CONFIG` (адрес бэкенда, режим),
3. предоставляет `window.SYM_ENGINE` — доступ к VPN-движку (см. `ENGINE_CONTRACT.md`).

Так один и тот же экран пиксель-в-пиксель работает везде, а различается лишь
движок под капотом.

## Матрица платформ

| Платформа            | Обёртка                        | Движок (`SYM_ENGINE`)                         | Статус папки        |
|----------------------|--------------------------------|-----------------------------------------------|---------------------|
| Windows              | Flutter + WebView              | нативный (winws / sing-box / byedpi)          | `flutter-host/`     |
| macOS                | Flutter + WebView              | sing-box + Network Extension                  | `flutter-host/`     |
| Linux                | Flutter + WebView              | sing-box                                      | `flutter-host/`     |
| Android              | Flutter + WebView              | VpnService + sing-box                         | `flutter-host/`     |
| iOS                  | Flutter + WebView              | NEPacketTunnelProvider + sing-box             | `flutter-host/`     |
| Веб-портал           | Браузер (как есть)             | нет (демо-каскад; VPN — в приложении)         | `web-portal/`       |
| Расширение браузера  | MV3 popup + native-messaging   | нет прямо; через хост управляет приложением   | `browser-extension/`|

Движок один и тот же по интерфейсу — реализация отличается по ОС. Десктоп
переиспользует уже готовый движок из `symbiont_build/lib/engine/` (см.
`flutter-host/README.md`).

## Почему так

- **Не дублируем UI.** Раньше Flutter-экраны (`symbiont_build/lib/screens/`)
  разошлись с дизайном. Теперь UI — это `webapp/`, а Flutter даёт только окно и
  движок. Экраны `lib/screens/*` остаются как справочный/фолбэк-слой.
- **RF-аудитория.** Оболочка полностью офлайн (React/Babel/шрифты/флаги/иконки
  вшиты, без CDN). Обёртки грузят её из локальных ассетов, не из сети.
- **Единый бэкенд.** Все платформы ходят в один FastAPI `/v1/*`
  (`symbiont_build/backend/`) через `window.SYM_API`.

## Что где

- `ENGINE_CONTRACT.md` — контракт `window.SYM_ENGINE` / `window.SYM_CONFIG`.
- `flutter-host/`      — обёртка Flutter+WebView (Win/mac/Linux/Android/iOS).
- `browser-extension/` — расширение MV3 (компаньон: аккаунт/узлы/статус/правила).
- `web-portal/`        — раздача оболочки как веб-портала (+ обратный прокси на бэкенд).
