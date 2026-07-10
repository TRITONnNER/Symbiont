# Симбионт — проект (каркас)

Единый сервис связности: **VPN + пробой блокировок + ускорение**. Этот репозиторий —
каркас: рабочий клиент на моке, запускаемый бэкенд и гайд интеграции готового движка.

> **Граница честности.** Движок обхода (туннель/пробой/маршрутизация) — это готовый
> open-source **Sing-box/zapret** за интерфейсом `SymbiontEngine`. Мы строим всё
> вокруг (клиент, бэкенд, манифест, продуктовую логику), но не пишем новый обходной
> движок как код. См. `ARCHITECTURE.md`.

## Структура

```
ARCHITECTURE.md            карта слоёв и границы UI↔движок
NATIVE_INTEGRATION.md      пошаговая интеграция готового Sing-box (Android/iOS)
pubspec.yaml               Flutter-проект
lib/
  main.dart                вход; AppState(MockEngine())  ← подмена на SingboxEngine = 1 строка
  theme.dart               палитра + тема (Golos Text + IBM Plex Mono) + виджеты
  i18n/strings.dart        словарь RU/EN (+точка расширения ES/DE) и глоссарий
  state/app_state.dart     ChangeNotifier со всем состоянием и действиями
  app_shell.dart           адаптив: rail (десктоп) / нижние вкладки (мобайл) + topbar
  screens/                 home, nodes, scan, account, support, overlays(settings+glossary)
  api/api_client.dart      HTTP клиент↔бэкенд + проверка подписи манифеста (Ed25519)
  engine/
    engine.dart            КОНТРАКТ SymbiontEngine + типы
    mock_engine.dart       мок (для разработки UI без движка)
    singbox_engine.dart    обёртка-стаб над нативным Sing-box (точка интеграции)
    singbox_config.dart    сборщик sing-box config из узла+протокола+правил
  models/models.dart       аккаунт без данных, подписка, ключи, поддержка
backend/
  server.py                FastAPI: токен → ключ → манифест → поддержка → аттестация
  keys.py                  выпуск/погашение ключей (Ed25519)
  manifest.py              сборка и подпись манифеста (Ed25519)
  key_redemption_reference.py  standalone-демо логики ключей
  requirements.txt
schema/manifest.schema.json  JSON-схема подписанного манифеста
```

## Запуск клиента (Flutter)

```bash
flutter pub get
flutter run            # desktop / эмулятор / устройство
```
Приложение работает на `MockEngine`: подключение, метрики, «Анализ», аккаунт,
поддержка, словарь, 4-язычный переключатель — всё живое, без движка и серверов.
Перенос на боевой движок — заменить в `lib/main.dart`:
```dart
app = AppState(SingboxEngine());   // вместо MockEngine()
```
и выполнить шаги `NATIVE_INTEGRATION.md`.

## Запуск бэкенда (FastAPI)

```bash
cd backend
pip install -r requirements.txt
uvicorn server:app --reload
# Документация: http://127.0.0.1:8000/docs
```
Проверка вручную:
```bash
# 1) анонимный аккаунт
curl -s -X POST localhost:8000/v1/account/anon -H 'content-type: application/json' -d '{"label":"гость"}'
# 2) выпустить ключ (админ)
curl -s -X POST localhost:8000/v1/admin/issue -H 'x-admin-token: demo-admin-token' \
     -H 'content-type: application/json' -d '{"plan":"pro","grant_days":30,"uses":1}'
# 3) погасить ключ (Bearer = token из шага 1)
curl -s -X POST localhost:8000/v1/key/redeem -H "authorization: Bearer <TOKEN>" \
     -H 'content-type: application/json' -d '{"code":"<CODE>"}'
# 4) подписанный манифест
curl -s localhost:8000/v1/manifest | head -c 400
```

## Что дальше

1. `flutter pub get && flutter run` — увидеть живое приложение на моке.
2. Поднять бэкенд, подключить клиент к `/v1/manifest` (проверка подписи на клиенте).
3. По `NATIVE_INTEGRATION.md` встроить готовый Sing-box (Android VpnService → iOS NEPacketTunnel).
4. Добавить ES/DE в `i18n/strings.dart` (1:1 с RU/EN; полный набор есть в HTML-прототипе).
5. Анти-абьюз (аттестация), биллинг, оркестрация флота (Server-in-a-Box) — по томам IV–VI.
