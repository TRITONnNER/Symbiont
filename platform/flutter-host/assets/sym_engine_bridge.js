/*
 * sym_engine_bridge.js — внедряется в WebView Flutter-обёрткой ДО загрузки
 * оболочки. Определяет window.SYM_ENGINE поверх JS-канала flutter_inappwebview:
 * вызовы уходят в Dart (SymbiontEngine), события возвращаются через _cb.
 *
 * Dart-сторона: см. lib/sym_engine_channel.dart.
 */
(function () {
  'use strict';
  var cb = null;

  function callHost(payload) {
    try {
      if (window.flutter_inappwebview && window.flutter_inappwebview.callHandler) {
        return window.flutter_inappwebview.callHandler('symEngine', payload);
      }
    } catch (e) {}
    return Promise.resolve({ ok: false, error: 'no_host' });
  }

  window.SYM_ENGINE = {
    onEvent: function (fn) { cb = fn; },

    // Оболочка зовёт при подключении. node — выбранный узел ({id,host,code,...}).
    connect: function (node) {
      return Promise.resolve(callHost({ cmd: 'connect', node: node || null }));
    },
    disconnect: function () { callHost({ cmd: 'disconnect' }); },
    status: function () { return Promise.resolve(callHost({ cmd: 'status' })); },

    // Dart вызывает это через evaluateJavascript, передавая событие движка.
    _emit: function (ev) { try { if (cb) cb(ev); } catch (e) {} }
  };
})();
