# Карта интерфейса «Симбионт» — полная инвентаризация и спецификация переработки

Единый источник правды по UI. Для **каждого** объекта: что есть сейчас → что меняем
→ на какие токены/паттерны → какая доступность нужна. Иду по этой карте экран за
экраном. Статусы: ✅ сделано · 🔶 частично · ⬜ не начато.

**Легенда токенов:** `Sp` — отступы 8-pt (tokens.dart) · `Tg` — типографика (theme.dart)
· `Dur/Ease` — motion · `TT` — тач-таргеты · APCA — контраст Lc≥75 тело/≥60 лейблы.

**Сквозные правила (ко всем объектам):**
- Любой `InkWell/GestureDetector` без видимого текста → обернуть в `Semantics(button:true,label:…)` + `Tooltip` на десктопе.
- Любой `TextField` → `Semantics`/`label` + видимый лейбл (не только hint).
- Тач-таргет ≥ `TT.min` (44); между таргетами ≥ `Sp.sm` (8).
- Отступы только из `Sp`; кегли только из `Tg`; никаких новых магических чисел.
- Статус/состояние — никогда только цветом: цвет + иконка + текст.
- Анимации из `Dur` (≤400мс), уважать reduced-motion.

---

## 0. ГЛОБАЛЬНЫЙ СЛОЙ КОМПОНЕНТОВ — `lib/theme.dart` ✅ (тянет все экраны)

| Объект | Что есть | Что меняем | Токены/паттерны | a11y |
|---|---|---|---|---|
| `cardBox()` | паддинг `EdgeInsets.all(17)`, радиус из Style | паддинг → `Sp.lg` (16); радиус → `Rad`; тень-elevation level1 | Sp.lg, Rad.lg, elevation | если `onTap` — `Semantics(button)` 🔶 |
| `gradButton()` | вертикальный паддинг 15, размер 15 | паддинг → `Sp.lg`; высота ≥ `TT.min`; текст → `Tg.title`/label; тёмный текст на мяте (есть) | Sp, TT.min, Tg | `Semantics(button,label)` ⬜ |
| `sectionLabel()` | 11px, tracking 1.4, muted | → `Tg.label` (13/600/трекинг), отступ `Sp.xl/Sp.md` | Tg.label, Sp | `Semantics(header:true)` ⬜ |
| `titleText()` | 24/w800 | → `Tg.h1` (32) или `Tg.h2` (25) по месту | Tg.h1/h2 | header ⬜ |
| `subText()` | 13.5/txt2 | → `Tg.body` | Tg.body | — |
| `fieldDeco()` | радиус 13, fill `K.ink` | радиус → `Rad.md`(12); фокус-бордер 2px мята (есть) | Rad, фокус-индикатор | связать с label ⬜ |
| `mono()` | JetBrains Mono ✅ | tabular figures (fontFeatures) для выравнивания цифр | — | — |
| `Tg` (шкала) | ✅ создана | применить во всех экранах | — | — |
| `loadColor()` | mint/amber/rose по нагрузке | → server-load пороги (≤75 зелёный/76–90 жёлтый/91–100 красный) + иконка | semantic-цвета | не только цвет ⬜ |

---

## 1. НАВИГАЦИЯ — `lib/app_shell.dart` 🔶 (токены+a11y готовы; перф-Selector — ⬜)

| Объект | Что есть | Что меняем | Токены | a11y |
|---|---|---|---|---|
| `_TopBar` | лого, статус-пилюля, кнопки | высоты/отступы → Sp; текст → Tg | Sp, Tg | landmark `header` |
| `_statusPill` | цвет+текст подключения | + иконка (щит/щит-выкл) — не только цвет | semantic | `Semantics(label)` |
| `_iconBtn()` | InkWell+Icon, радиус 11 | размер ≥ `TT.min`; радиус `Rad.sm` | TT, Rad | **`Tooltip`+`Semantics(button)`** |
| `_StyleButton` / `_pick` | выбор стиля (bottom sheet) | отступы Sp; пункты ≥TT.min | Sp, TT | label «Сменить стиль» |
| `_Logo` | значок+текст | Tg для текста | Tg | `excludeSemantics` (декор) |
| `_Langs`/`_LangButton` | переключатель языка | таргеты ≥TT.min | TT | label «Язык: RU/EN» |
| `_Rail` (десктоп) | боковая навигация, радиус 13 | ширина из Responsive; активный пункт — индикатор+вес; ≥TT | Sp, Rad | `Semantics(selected)` на пункте |
| `_TabBar` (моб.) | нижние вкладки, радиус 12 | таргеты ≥TT.min×48; активный — цвет+вес+иконка-fill | TT, Tg | `selected`, label каждой вкладки |
| `_ConnBadge` | индикатор подключения | цвет+иконка+текст | semantic | label |
| `_Content` | AnimatedSwitcher по экрану | оставить; рассмотреть IndexedStack для сохранения состояния | Dur/Ease | — |
| **Перф** | один `AnimatedBuilder(app)` на весь shell | вынести динамику (статус-пилюля, бэйдж) в свои `Selector`/listenable | — | — |

---

## 2. ГЛАВНЫЙ — `lib/screens/home_screen.dart` 🔶

| Объект | Что есть | Что меняем | Токены | a11y |
|---|---|---|---|---|
| `ConnectionCore` (ядро) | ✅ новый герой, автомат, путь каскада | докрутить пропорции по скриншоту | Sp, Dur, Ease | ✅ Semantics |
| `_metricsRow`/`_metric` | пинг/потери/проток, 3 в ряд | значения → `mono` tabular; лейблы → `Tg.caption`; паддинги Sp | Tg, mono, Sp | `Semantics(label+value)` |
| Ping-график (`_Sparkline`) | спарклайн пинга | оставить; легенда/подпись оси | — | `Semantics(label «график пинга»)` |
| `_modes` (охват) | сетка режимов smart/whole/selected/off | **увести в выбор/лист** (разгрузка экрана); карточки → common-region | Sp, Hick | `selected`, label |
| `_protoChips` | чипы auto/reality/hy2/ss | в тот же вторичный блок; чип ≥TT | TT, Sp | `selected`, label |
| `_gameBoostCard` | карточка бустера | во вторичную поверхность | Sp | label |
| Карточка узла | флаг+страна+хост+ping | → label-value; ping mono; ≥TT | Tg, mono | `button`, label |
| Карточка обхода | альтернативная (нет серверов) | унифицировать со стилем узла | Sp | button |
| `_errorBox`/`_ErrorCard` | ошибка+детали+действия | паддинг Sp; кнопки ≥TT; текст Tg | Sp, TT, Tg | `liveRegion`, label |
| `_noteBox` | amber-заметка | Sp, Tg | Sp, Tg | label |
| Кнопка «Анализ» | gradButton | через обновлённый gradButton | TT | button (наследует) |
| `_Dial`/`_ArcPainter` | **мёртвый код** | **удалить** | — | — |

**Разгрузка:** на главном оставить ядро + метрики + текущий узел + «Анализ».
Режимы/протоколы/бустер → вторичная поверхность (sheet «Настройки защиты» или вкладка).

---

## 3. УЗЛЫ — `lib/screens/nodes_screen.dart` ⬜

| Объект | Что есть | Что меняем | Токены | a11y |
|---|---|---|---|---|
| Заголовок | `titleText` | `Tg.h2` | Tg | header |
| `_search()` | TextField поиска | видимый лейбл/иконка; радиус Rad | Rad, Tg | `Semantics(textField,label)` |
| `_filterChip` | чипы фильтра, радиус 10 | ≥TT; активный цвет+вес | TT, Rad | `selected`, label |
| `_quick()` | быстрые действия (2 шт) | карточки common-region; ≥TT | Sp, TT | button, label |
| `_grid()`/`_node()` | сетка карточек узла | server-load цвет+иконка; ping mono; ≥TT; отступы Sp | Sp, mono, semantic | `button`, label «страна, нагрузка, пинг» |
| `_StatusDot` | пульсирующая точка | + текст/иконка статуса рядом | Dur | не только цвет |
| `_Appear` | fade-in карточек | оставить; reduced-motion | Dur/Ease | reduced-motion |
| Звезда избранного | InkWell+иконка | ≥TT | TT | `Tooltip`, `Semantics(toggled)` |
| Пустое состояние | ✅ есть (none/empty) | текст → Tg; CTA-кнопка | Tg | — |

---

## 4. ПРАВИЛА (Routes) — `RulesScreen` в `lib/screens/overlays.dart` ⬜

| Объект | Что есть | Что меняем | Токены | a11y |
|---|---|---|---|---|
| Список правил | процессы/домены + действие | строки label-value; ≥TT | Sp, Tg | label |
| Переключатель действия | direct/bypass/boost | сегмент-контрол; цвет+текст | semantic | `selected` |
| `_AddSheet` | добавление правила (bottom sheet) | поля с лейблами; кнопки ≥TT | Sp, TT, Tg | textField label, focus-trap |
| `_ServiceIcon` | иконка сервиса | — | — | `excludeSemantics` |
| `_PressFX` | анимация нажатия | Dur/Ease; reduced-motion | Dur | — |

---

## 5. АНАЛИЗ — `lib/screens/scan_screen.dart` ⬜ (самый плотный данными)

| Объект | Что есть | Что меняем | Токены | a11y |
|---|---|---|---|---|
| Кнопка «Запустить» | gradButton | ≥TT | TT | button |
| `_trafficCard` | счётчики трафика (2с таймер) | значения mono tabular; down=зелёный/up=красный пунктир | mono, semantic | label+value, `liveRegion` |
| `_liveConnPanel`/`_PingGraph` | живой график пинга | легенда; ось | — | label графика |
| `_stat`/`_metric` | плитки метрик | mono; Tg.caption лейблы; Sp | Tg, mono, Sp | label+value |
| `_monitorCard`/`_monPill` | монитор региона | пилюли цвет+текст | semantic | label |
| `_connCard`/`_kv` | детали соединения (IP/порт/проток) | label-value; значения mono | mono, Tg | label |
| `_hostRow` | строка хоста + статус | статус цвет+иконка+текст | semantic | label «хост: статус» |
| `_recommendation` | рекомендация по блокировкам | Tg.body; иконка | Tg | `liveRegion` |
| `_tuneResult` | результат автонастройки | Tg | Tg | label |
| `_pulseCard` | «пульс» региона | цвет+иконка+текст уровня | semantic | label |
| **Занавес** | детект есть (app) | показать вердикт занавеса + «увод на relay» карточкой | semantic | `liveRegion` |
| **Перф** | таймер трафика 2с → ребилд | вынести в локальный listenable | — | — |

---

## 6. АККАУНТ — `lib/screens/account_screen.dart` ⬜

| Объект | Что есть | Что меняем | Токены | a11y |
|---|---|---|---|---|
| Заголовок | titleText | Tg.h2 | Tg | header |
| Токен + копировать | Row+InkWell, показ токена | mono; кнопка ≥TT | mono, TT | `Tooltip`«Скопировать», label |
| Поле «метка» | TextField fieldDeco | видимый лейбл | Tg | textField label |
| Карточка подписки | `_line` label-value | план: pill цвет+текст; срок | Tg, semantic | label+value |
| Активация ключа | поле + gradButton/спиннер | поле лейбл; кнопка ≥TT | TT | textField label, `liveRegion` статус |
| Инвайт-код | хардкод 'СИМБ-7K2Q' + копир. | вынести в данные; ≥TT | mono, TT | `Tooltip`, label |
| `_toast` | SnackBar | — | — | объявляется скринридером |

---

## 7. ПОДДЕРЖКА — `lib/screens/support_screen.dart` ⬜

| Объект | Что есть | Что меняем | Токены | a11y |
|---|---|---|---|---|
| Заголовок | titleText | Tg.h2 | Tg | header |
| Кнопки «Починить»/«Сообщить» | 2× gradButton ghost | ≥TT; иконки | TT | button, label |
| Лента сообщений/`_bubble` | пузыри чата | отступы Sp; Tg.body; время Tg.caption | Sp, Tg | `liveRegion` на новые |
| Поле ввода + отправка | TextField + InkWell | поле лейбл; кнопка ≥TT+иконка | TT | textField label, `Tooltip`«Отправить» |

---

## 8. ОНБОРДИНГ — `lib/screens/onboarding_screen.dart` ⬜

| Объект | Что есть | Что меняем | Токены | a11y |
|---|---|---|---|---|
| `_AuroraBackdrop` | анимированный фон | reduced-motion → статичный | Dur | `excludeSemantics` |
| Лого+заголовок | Row+Text | Tg.h1/h2 | Tg | header |
| Подзаголовок | Text | Tg.body | Tg | — |
| Поля (метка/токен/сервер) | TextField fieldDeco | видимые лейблы (есть sectionLabel) | Tg | textField label |
| «Доп. настройки» | разворот advanced | анимация Dur; стрелка | Dur | `Semantics(expanded)` |
| Кнопка вход/регистрация | gradButton/спиннер | ≥TT | TT | button, `liveRegion` |
| Переключатель режима | вход↔регистрация | ≥TT | TT | button, label |
| Privacy-текст | Text | Tg.caption | Tg | — |

---

## 9. ОВЕРЛЕИ — `lib/screens/overlays.dart` ⬜

### 9.1 `SettingsScreen`
| Объект | Что меняем | Токены | a11y |
|---|---|---|---|
| Группы настроек | строки label-value/тогглы; Sp | Sp, Tg | header групп |
| Тогглы/переключатели | ≥TT; цвет+состояние | TT | `Semantics(toggled)` |
| Выбор (язык/стиль/режим ошибок) | сегменты/листы ≥TT | TT | `selected` |

### 9.2 `GlossaryScreen`
| Список терминов | Tg.body/Tg.label; Sp; раскрытие | Tg, Sp, Dur | `expanded`, header |

### 9.3 `LogsScreen`
| Лог-вывод | `mono` tabular; уровни цвет+префикс | mono, semantic | `liveRegion`, копирование label |
| Кнопки (очистить/копир.) | ≥TT | TT | `Tooltip` |

### 9.4 `RulesScreen` — см. раздел 4.

---

## 10. ОБЩИЕ САБ-ВИДЖЕТЫ/ПЕЙНТЕРЫ

| Объект | Файл | Статус |
|---|---|---|
| `ConnectionCore` + `_RingPainter` | widgets/connection_core.dart | ✅ |
| `_Sparkline` | home | оставить, +a11y label |
| `_PingGraph` | scan | оставить, +a11y label |
| `_Appear`, `_StatusDot`, `_PressFX`, `_AuroraBackdrop` | разные | reduced-motion |
| `_Dial`, `_ArcPainter` | home | **удалить (мёртвый код)** |
| `_flag()`, `_country()` | дубли в home/nodes | вынести в один util |

---

## Порядок работ (от «тянет всё» к точечному)

1. **Глобальный слой** (раздел 0) — cardBox/gradButton/sectionLabel/fieldDeco на Sp/Tg/TT. Поднимает ВСЕ экраны разом.
2. **Навигация** (1) — топбар/rail/вкладки: токены + a11y + перф (Selector).
3. **Главный** (2) — разгрузка + остальные объекты на токены.
4. **Узлы** (3) и **Анализ** (5) — самые «продуктовые» экраны: server-load, mono, label-value.
5. **Аккаунт/Поддержка/Онбординг** (6–8).
6. **Оверлеи** (9) — Settings/Glossary/Logs/Rules.
7. **Чистка** (10) — мёртвый код, дубли util, общий проход по reduced-motion.

После каждого пункта: баланс Dart зелёный, сборка у тебя → скриншот → докрутка.


---

## Прогресс реализации

- ✅ **Раздел 0 (глобальный слой):** cardBox/gradButton/sectionLabel/titleText/subText/
  fieldDeco переведены на Sp/Tg/Rad/TT; gradButton и cardBox(onTap) получили Semantics(button);
  mono → tabular figures; добавлена доступная `iconButton()` (Tooltip+Semantics+тач-таргет 44).
  → подняло отступы, типографику, тач-таргеты и базовую доступность СРАЗУ НА ВСЕХ экранах.
- 🔶 **Раздел 1 (навигация):** иконочные кнопки шапки доступны (Tooltip+Semantics+44);
  пункты rail и нижних вкладок получили Semantics(selected)+тач-таргет 44. Осталось:
  точечные ребилды (Selector) вместо одного AnimatedBuilder на весь shell.
- ✅ Ранее: токены, ядро связи (ConnectionCore), JetBrains Mono, контраст muted.

Следующее по карте: разделы 2–9 (экраны и оверлеи) — перевод на Sp/Tg + a11y + server-load
цвета + label-value. Каждый — баланс зелёный, ты собираешь и шлёшь скрин, я докручиваю.


---

## Прогресс: визуальный слой (иконки/графики/анимации/плавность)

- ✅ **Тайминги анимаций → motion-токены** (Dur/Ease) в app_shell (переходы экранов),
  nodes (_Appear/AnimatedContainer/ping-tween), overlays (_PressFX), home (спарклайн).
  Было вразнобой 90/110/160/200/250/280/380/500/650мс → единые micro/short/medium/long.
- ✅ **Ядро связи:** дуга крутится ТОЛЬКО в connecting/measuring (раньше всегда — лишний
  расход); добавлено уважение reduced-motion (дыхание отключается по системной настройке).
- ✅ **Мёртвый код удалён:** _Dial + _ArcPainter (два вечных контроллера) вырезаны.
- ✅ **Графики:** _Sparkline и _PingGraph — гладкие стыки (strokeJoin.round) и ИСПРАВЛЕН
  shouldRepaint (раньше «замирали» при изменении значений с той же длиной массива).
- ✅ **Плитки:** floor-ширина в сетках режимов и узлов (убраны суб-пиксельные переполнения).
- ✅ **Картинки:** Image.network фавиконов уже с errorBuilder/loadingBuilder (проверено).
- 🔶 **Иконки:** семейства смешаны (outlined/rounded/plain) — приемлемо (Material-паттерн
  «outlined=неактив, filled=актив»); унификацию отложил до визуальной сверки по скриншотам.
- ⬜ **Глобальная плавность:** весь shell в одном AnimatedBuilder(app) — кандидат на точечные
  Selector'ы (крупнее и рискованнее, делаю отдельным заходом с проверкой).


---

## Прогресс: поэкранный проход (корректность/доступность/i18n/переполнения)

- ✅ **Аккаунт:** инвайт-код из единого источника (был дубль-литерал); кнопки токена и
  копирования инвайта — Tooltip+Semantics+тач-таргет 44; ряды label-value во Flexible.
- ✅ **Поддержка:** эмодзи 🛠⚑ в кнопках → иконки (на Windows не рендерились); кнопка
  отправки — Tooltip+Semantics; добавлен i18n-ключ.
- ✅ **Онбординг:** переключатели «доп. настройки» и вход/регистрация — Semantics(+expanded).
- ✅ **Настройки:** эмодзи 📖🧾 → иконки; тогглы — Semantics(toggled); радио ошибок —
  Semantics(selected, группа); DNS-ряд перестроен (лейбл сверху + Wrap-чипы, без переполнения).
- ✅ **Словарь:** карточки терминов — Semantics(button, expanded).
- ✅ **Маршрутизация:** кнопка «Добавить» ЛОКАЛИЗОВАНА (была хардкод-русская → ломалась на
  EN) + Semantics + тёмный текст на мяте (контраст); кнопка «назад» оверлеев — Tooltip+Semantics+44.
- ✅ **Анализ:** юниты трафика МБ/КБ/Б → международные MB/KB/B (были русские на EN).
- ✅ **Флаги:** фолбэк '🏳' → буквы кода (эмодзи-флаг не рендерится на Windows).
- 🔶 Осталось: чипы категорий (Semantics selected) — мелочь; визуальная сверка пропорций по скринам.
