// Единая точка настройки расширения-компаньона.
window.SYM_EXT = {
  // Бэкенд, куда ходит SYM_API (тот же, что у приложения/портала).
  apiBase: 'http://127.0.0.1:8600',
  // Имя native-messaging хоста, установленного вместе с приложением Симбионт.
  nativeHost: 'com.symbiont.host',
  // URL полноценного веб-портала (кнопка «Открыть портал»).
  portalUrl: 'http://127.0.0.1:8099/%D0%A1%D0%B8%D0%BC%D0%B1%D0%B8%D0%BE%D0%BD%D1%82.dc.html'
};

// Конфиг для symbiont-bridge.js — должен быть задан ДО его загрузки.
window.SYM_CONFIG = { apiBase: window.SYM_EXT.apiBase, live: true };
