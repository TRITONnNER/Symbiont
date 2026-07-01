# Контракт `window.SYM_ENGINE` и `window.SYM_CONFIG`

Оболочка (`webapp/Симбионт.dc.html`) не знает про конкретную ОС. Она общается с
движком только через два глобальных объекта, которые задаёт обёртка **до**
загрузки страницы (или сразу после — до первого подключения).

## `window.SYM_CONFIG`

```js
window.SYM_CONFIG = {
  apiBase: 'http://127.0.0.1:8600', // адрес бэкенда; '' = тот же origin
  live: true                        // false → демо-режим (сеть не трогаем)
};
```

Читается один раз при загрузке `symbiont-bridge.js`. Определяет, куда ходит
`window.SYM_API`.

## `window.SYM_ENGINE`

Если объект задан — оболочка **делегирует** ему подключение вместо симуляции
каскада. Если `null`/не задан — работает демо-каскад (для браузера/портала).

```ts
interface SymEngine {
  // Подключиться к узлу. node — элемент из списка узлов оболочки:
  //   { code, host, ping, load, id?, protocols? }  (может быть undefined → авто).
  // Должен вернуть Promise (resolve — процесс пошёл; reject — сразу ошибка).
  connect(node?: Node): Promise<void>;

  // Отключиться. Синхронно; финальное состояние придёт событием onEvent{conn:'idle'}.
  disconnect(): void;

  // Подписка на события движка. cb вызывается при каждом изменении.
  onEvent(cb: (ev: EngineEvent) => void): void;

  // (необязательно) Разовый снимок состояния.
  status?(): Promise<EngineEvent>;
}

interface EngineEvent {
  conn?:  'idle'|'measuring'|'connecting'|'connected'|'blocking'|'error';
  stage?: 'direct'|'bypass'|'tunnel'|'relay';  // ступень «живого каскада»
  ping?:  number;   // мс
  down?:  number;   // Мбит/с (входящий)
  up?:    number;   // Мбит/с (исходящий)
  toast?: string;      // необязательное всплывающее сообщение
  toastKind?: 'ok'|'warn'|'error'|'muted';
}
```

### Как оболочка это использует (уже реализовано)

- `connect()` → если есть `SYM_ENGINE.connect`, ставит `conn:'measuring'`, зовёт
  движок и ждёт события; иначе играет анимацию каскада.
- `disconnect()` → зовёт `SYM_ENGINE.disconnect()` (если есть) и ставит `idle`.
- `componentDidMount` → подписывается через `SYM_ENGINE.onEvent(...)`; события
  переносятся в состояние (`conn`, `stage`, `live.ping/down/up`) и тосты.

### Минимальная реализация со стороны хоста (псевдокод)

```js
window.SYM_ENGINE = {
  _cb: null,
  onEvent(cb){ this._cb = cb; },
  connect(node){
    NATIVE.connect(node && (node.id || node.host));   // вызов в нативный слой
    return Promise.resolve();
  },
  disconnect(){ NATIVE.disconnect(); }
};
// нативный слой шлёт обратно:  window.SYM_ENGINE._cb({conn:'connected', stage:'tunnel', ping:42})
```

Именно это делают `flutter-host/` (через JS-канал в Dart-движок) и
`browser-extension/` (через native-messaging в установленное приложение).
