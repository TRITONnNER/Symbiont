/* Service worker: мост между попапом и native-messaging хостом приложения.
   Хост (com.symbiont.host) реально управляет VPN-движком на устройстве. */
'use strict';

var NATIVE = 'com.symbiont.host';
var port = null;
var lastConn = 'idle';

function connectHost() {
  if (port) return port;
  try {
    port = chrome.runtime.connectNative(NATIVE);
    port.onMessage.addListener(function (msg) {
      // Хост шлёт события движка: {conn, stage, ping, ...}
      if (msg && msg.conn) lastConn = msg.conn;
      try { chrome.runtime.sendMessage(Object.assign({ type: 'engine-event' }, msg)); } catch (e) {}
    });
    port.onDisconnect.addListener(function () { port = null; });
  } catch (e) { port = null; }
  return port;
}

var _askSeq = 0;
function ask(payload) {
  return new Promise(function (resolve) {
    var p = connectHost();
    if (!p) { resolve({ ok: false, error: 'no_native_host', conn: 'error' }); return; }
    // Корреляция запрос↔ответ по _id: на одном порту живут и незапрошенные
    // engine-события, и ответы параллельных ask(); без id ask резолвился первым
    // попавшимся сообщением и перекрёстно мешал ответы разных команд.
    var id = 'r' + (++_askSeq);
    payload = Object.assign({ _id: id }, payload || {});
    var done = false;
    function finish(res) {
      if (done) return; done = true;
      try { p.onMessage.removeListener(onMsg); } catch (e) {}
      resolve(res);
    }
    function onMsg(msg) {
      if (!msg || msg._id !== id) return;   // не наш ответ (чужая команда/событие) — игнор
      finish(msg);
    }
    try {
      p.onMessage.addListener(onMsg);
      p.postMessage(payload);
      // Таймаут — не подвешиваем попап, если хост молчит (снимаем слушатель).
      setTimeout(function () { finish({ ok: false, error: 'timeout', conn: lastConn }); }, 4000);
    } catch (e) { finish({ ok: false, error: String(e), conn: 'error' }); }
  });
}

chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
  if (!msg || !msg.type) return;
  if (msg.type === 'status') {
    // Если хост уже подключён — спросим у него свежий статус (активирует ветку
    // status в native-хосте), иначе мгновенно отвечаем из кэша, не поднимая хост
    // на пустом месте. При ошибке/таймауте хоста тоже откатываемся на кэш.
    if (port) {
      ask({ cmd: 'status' }).then(function (m) {
        sendResponse(m && m.conn && m.conn !== 'error' ? m : { conn: lastConn });
      });
      return true;
    }
    sendResponse({ conn: lastConn });
    return;
  }
  if (msg.type === 'connect') { ask({ cmd: 'connect', node: msg.node }).then(sendResponse); return true; }
  if (msg.type === 'disconnect') { ask({ cmd: 'disconnect' }).then(sendResponse); return true; }
});
