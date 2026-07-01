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

function ask(payload) {
  return new Promise(function (resolve) {
    var p = connectHost();
    if (!p) { resolve({ ok: false, error: 'no_native_host', conn: 'error' }); return; }
    var done = false;
    function onMsg(msg) {
      if (done) return; done = true;
      try { p.onMessage.removeListener(onMsg); } catch (e) {}
      resolve(msg || { ok: true });
    }
    try {
      p.onMessage.addListener(onMsg);
      p.postMessage(payload);
      // Таймаут — не подвешиваем попап, если хост молчит.
      setTimeout(function () { if (!done) { done = true; resolve({ ok: false, error: 'timeout', conn: lastConn }); } }, 4000);
    } catch (e) { resolve({ ok: false, error: String(e), conn: 'error' }); }
  });
}

chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
  if (!msg || !msg.type) return;
  if (msg.type === 'status') { sendResponse({ conn: lastConn }); return; }
  if (msg.type === 'connect') { ask({ cmd: 'connect', node: msg.node }).then(sendResponse); return true; }
  if (msg.type === 'disconnect') { ask({ cmd: 'disconnect' }).then(sendResponse); return true; }
});
