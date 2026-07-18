# Симбионт — приложение (исходный код)

Полное приложение **Симбионт** (не сайт-витрина): Защита/Подключение, Узлы,
Маршрутизация, Анализ, Аккаунт (кошелёк/баланс, история, рефералы, устройства,
код восстановления), Поддержка, проверка ключа, колесо, ваучеры.

Исходники **отдельными файлами** — без минификации и без сборки в один файл.

---

## Как запустить локально

Компоненты и данные подгружаются через `fetch`, поэтому нужен статический сервер
(через `file://` не заработает):

```bash
python3 -m http.server 8080
# открыть:
#   http://localhost:8080/Симбионт.dc.html
```

Точка входа — `Симбионт.dc.html`. Для быстрого старта на конкретном экране/языке
можно передать query-параметры: `?screen=nodes&lang=en&conn=connected`.

---

## Структура

```
Симбионт.dc.html      — приложение (все экраны в одном компоненте)
ConnectionCore.dc.html — ядро связи (кольцо + каскад)
ListRow.dc.html        — строка списка (узлы, приложения, настройки)
Picker.dc.html         — оверлей выбора (узел, протокол, маршрут)
Card.dc.html           — карточки (данные/панель/уведомление)
support.js             — движок компонентов (.dc.html). Не редактируется.
symbiont-strings.js    — базовые строки (ru/en/es/de)
symbiont-i18n.js       — расширенная локализация экранов
app-config.js          — ДАННЫЕ по умолчанию (фолбэк): цены/тарифы, узлы,
                         кошелёк/баланс, история, рефералы, устройства,
                         ваучеры, флаги, скидки, parse-bridge, колесо
app-api.js             — слой обращения к вашему API
```

> Формат — **Design Components** (`.dc.html`): разметка + класс логики, стили
> инлайновые. Это обычные HTML/JS-файлы; `support.js` собирает из них экран
> в браузере. Приложение — один компонент со всеми экранами (общий стейт), а не
> отдельные HTML-страницы; поэтому «файлы экранов» здесь — это `Симбионт.dc.html`
> плюс переиспользуемые компоненты (ConnectionCore/ListRow/Picker/Card).

---

## Данные и подключение вашего API

Все числа и списки вынесены в **`app-config.js`** (`window.SYM_CONFIG`) и служат
фолбэком. **`app-api.js`** (`window.SYM_API`) заменяет их ответом сервера.

По умолчанию приложение **ничего не запрашивает** и работает на значениях из
`app-config.js`. Когда бэкенд готов:

```js
SYM_API.base = 'https://api.simbiont.app';
SYM_API.headers = { Authorization: 'Bearer ' + token };
SYM_API.bootstrap();   // тянет economy/flags/discounts/nodes/account/ledger/…,
                       // вливает в SYM_CONFIG и шлёт 'sym-config' → перерисовка
```

Точечно, без сети:

```js
SYM_CONFIG.merge({ economy: /* ответ /v1/config/economy */ });
window.dispatchEvent(new CustomEvent('sym-config'));
```

### Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/v1/config/economy` | тарифы, цены, продукты, `crypto_discount` |
| GET | `/v1/config/flags` | фиче-флаги + настройки по умолчанию |
| GET | `/v1/config/discounts` | промокоды, комбо, рефералы |
| GET | `/v1/config/parse-bridge` | конфиг «мост/парсер» |
| GET | `/v1/config/nodes` | список узлов (если сервер их отдаёт) |
| POST | `/v1/billing/purchase` | `{ product, method, currency }` → `{ status, checkoutUrl? }` |
| GET | `/v1/billing/status` | подписка + грант |
| GET | `/v1/billing/ledger` | баланс активного времени + история |
| GET | `/v1/billing/payment/{id}` | статус платежа |
| GET | `/v1/account/status` | профиль, план, токен |
| GET | `/v1/account/referrals` | реферальная статистика |
| GET | `/v1/account/devices` | устройства |

Пути можно переопределить: `SYM_API.endpoints.economy = '...'`.

### Какие данные что кормят

- **Цены/оплата** ← `SYM_CONFIG.economy` (цены `standard`/`pro` × `rub`/`usd` ×
  `month`/`year`), `crypto_discount`. Кнопка оплаты (`buyPlan`) вызывает
  `SYM_API.purchase(...)`, если задан `SYM_API.base`; иначе — демо-подтверждение.
  `product` = `pro_month` / `standard_year` / `balance_100h` и т. п.
- **Узлы** ← `SYM_CONFIG.nodes` (`code`/`host`/`ping`/`load`/`fav`; названия
  страны/города — из локализации или полей `country`/`city`).
- **Кошелёк/история** ← `SYM_CONFIG.ledger` (`balance` + `entries`).
- **Рефералы** ← `SYM_CONFIG.referral`, **устройства** ← `SYM_CONFIG.devices`,
  **ваучеры** ← `SYM_CONFIG.vouchers`, **карта трафика** ← `SYM_CONFIG.traffic`,
  **колесо** ← `SYM_CONFIG.wheel`, **аккаунт** ← `SYM_CONFIG.account`.

### Формы ответов

Значения по умолчанию в `app-config.js` — это и есть ожидаемая форма ответов
сервера. Достаточно вернуть такой же JSON по соответствующему разделу.
`SYM_CONFIG.merge()` сливает частичные ответы (переопределяются только
присланные поля). Реальные числа и жизненный цикл оплаты — в `PRICING.md`.

---

## Замечания

- Валюта по региону: `ru → ₽`, иначе `→ $` (см. `economy.currency_by_region`).
- Имена `app-config.js` / `app-api.js` — это аналог `site-config.js` /
  `site-api.js` из витрины (переименованы, чтобы не конфликтовать при размещении
  приложения и сайта в одной папке). Функция та же.
- Тексты (глоссарий, FAQ, подсказки) остаются в `symbiont-strings.js` /
  `symbiont-i18n.js` — это локализация, а не «данные для сервера».
- Шрифты и флаги подключаются извне (Google Fonts, flagcdn.com); локальных
  бинарных ассетов нет.
