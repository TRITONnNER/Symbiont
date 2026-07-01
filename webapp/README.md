# Симбионт — веб-клиент (продуктовый UI)

Это **ваш дизайн из Claude Design**, сделанный автономным (offline) веб-приложением:
Design-Components-оболочка + рантайм (`support.js`), с локально вшитыми React/Babel,
шрифтами (Golos Text, JetBrains Mono) и иконками (Material Symbols) — **без CDN**
(важно для аудитории в РФ, где CDN блокируемы).

## Запуск
```bash
python3 serve.py 8080          # затем открыть http://127.0.0.1:8080/Симбионт.dc.html
```
Рантаму нужен именно HTTP-сервер (компоненты грузятся через fetch), не `file://`.

## Что внутри
- `Симбионт.dc.html` — главный экран (весь UI: защита/узлы/маршруты/анализ/аккаунт/…).
- `ConnectionCore/Card/ListRow/Picker.dc.html` — компоненты.
- `support.js` — рантайм Design-Components (пропатчен: React/Babel из локальных файлов).
- `react*.js`, `babel.min.js`, `fonts/`, `fonts.css` — вшитые зависимости (offline).
- `symbiont-strings.js`, `symbiont-i18n.js` — тексты и локализация.

## Подключение к бэкенду
Вся «живая» логика — в `state` компонента внутри `Симбионт.dc.html`. Данные пока
демонстрационные; их подменяем на реальные вызовы `/v1/...` (см. `symbiont_build/backend/API.md`).
