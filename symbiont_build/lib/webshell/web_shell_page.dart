// web_shell_page.dart
//
// Главный экран приложения: показывает дизайн-оболочку (assets/webapp/) в WebView
// и связывает её window.SYM_ENGINE с реальным движком (SymEngineChannel).
// Один и тот же UI работает на Windows/macOS/Linux/Android/iOS.
//
// Включается флагом kUseWebShell в main.dart. Бэкенд проксируется loopback-сервером
// (/v1 → backendBase), поэтому оболочка и API — на одном origin, без CORS.

import 'dart:collection';
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import '../engine/engine.dart';
import '../log.dart';
import 'sym_web_server.dart';
import 'sym_engine_channel.dart';

// Мост движка — внедряется В КАЖДУЮ загрузку страницы ДО скриптов оболочки.
const String _kEngineBridgeJs = r'''
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
    connect: function (node) { return Promise.resolve(callHost({ cmd: 'connect', node: node || null })); },
    disconnect: function () { callHost({ cmd: 'disconnect' }); },
    status: function () { return Promise.resolve(callHost({ cmd: 'status' })); },
    _emit: function (ev) { try { if (cb) cb(ev); } catch (e) {} }
  };
})();
''';

class WebShellPage extends StatefulWidget {
  final SymbiontEngine engine;
  final String backendBase; // адрес бэкенда /v1 (проксируется на том же origin)
  const WebShellPage({super.key, required this.engine, this.backendBase = ''});

  @override
  State<WebShellPage> createState() => _WebShellPageState();
}

class _WebShellPageState extends State<WebShellPage> {
  late final SymWebServer _srv = SymWebServer(widget.backendBase);
  late final SymEngineChannel _channel = SymEngineChannel(widget.engine);
  String? _url;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    await _srv.start();
    if (!mounted) return;
    setState(() => _url = '${_srv.baseUrl}/Симбионт.dc.html');
  }

  @override
  void dispose() {
    _channel.dispose();
    _srv.stop();
    super.dispose();
  }

  UnmodifiableListView<UserScript> _userScripts() {
    // API на том же origin (через прокси) → apiBase:''.
    const cfg = "window.SYM_CONFIG = { apiBase: '', live: true };";
    return UnmodifiableListView<UserScript>([
      UserScript(source: cfg, injectionTime: UserScriptInjectionTime.AT_DOCUMENT_START),
      UserScript(source: _kEngineBridgeJs, injectionTime: UserScriptInjectionTime.AT_DOCUMENT_START),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    if (_url == null) {
      return const Scaffold(
        backgroundColor: Color(0xFF070A0F),
        body: Center(child: CircularProgressIndicator(color: Color(0xFF34E5B0))),
      );
    }
    return Scaffold(
      backgroundColor: const Color(0xFF070A0F),
      body: SafeArea(
        child: InAppWebView(
          initialUrlRequest: URLRequest(url: WebUri(_url!)),
          initialUserScripts: _userScripts(),
          initialSettings: InAppWebViewSettings(
            // ВНИМАНИЕ: на Windows/WebView2 transparentBackground:true рендерит
            // страницу в ЧЁРНОЕ (движок не композитит прозрачный слой). Фон и так
            // тёмный у самой оболочки — прозрачность не нужна. Держим false.
            transparentBackground: false,
            supportZoom: false,
            disableContextMenu: true,
            javaScriptCanOpenWindowsAutomatically: false,
          ),
          onWebViewCreated: _channel.attach,
          // Диагностика загрузки оболочки (видно в symbiont.log): старт/финиш,
          // сетевые/HTTP-ошибки ресурса и сообщения JS-консоли (ошибки рантайма).
          onLoadStop: (controller, url) => Log.w('webshell', 'страница загружена: $url'),
          onReceivedError: (controller, request, error) =>
              Log.w('webshell', 'ошибка загрузки ${request.url}: ${error.type} ${error.description}'),
          onReceivedHttpError: (controller, request, errorResponse) =>
              Log.w('webshell', 'HTTP ${errorResponse.statusCode} для ${request.url}'),
          onConsoleMessage: (controller, consoleMessage) =>
              Log.w('webshell', 'js: ${consoleMessage.message}'),
        ),
      ),
    );
  }
}
