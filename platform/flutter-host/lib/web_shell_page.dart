// web_shell_page.dart
//
// Экран-обёртка: поднимает loopback-сервер с вшитой оболочкой, показывает её в
// WebView и связывает window.SYM_ENGINE с реальным движком (SymEngineChannel).
//
// Подключение в приложении:
//   home: WebShellPage(engine: SingboxEngine(), backendBase: 'https://api.symbiont.net')
// На десктопе engine — тот же, что уже используется в symbiont_build.

import 'dart:collection';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import 'package:symbiont/engine/engine.dart';
import 'sym_web_server.dart';
import 'sym_engine_channel.dart';

class WebShellPage extends StatefulWidget {
  final SymbiontEngine engine;
  final String backendBase; // адрес бэкенда /v1 ('' = тот же origin)
  const WebShellPage({super.key, required this.engine, this.backendBase = ''});

  @override
  State<WebShellPage> createState() => _WebShellPageState();
}

class _WebShellPageState extends State<WebShellPage> {
  final _srv = SymWebServer();
  late final SymEngineChannel _channel;
  String? _url;

  @override
  void initState() {
    super.initState();
    _channel = SymEngineChannel(widget.engine);
    _boot();
  }

  Future<void> _boot() async {
    await _srv.start();
    setState(() => _url = '${_srv.baseUrl}/Симбионт.dc.html');
  }

  @override
  void dispose() {
    _channel.dispose();
    _srv.stop();
    super.dispose();
  }

  // Внедряем ДО загрузки страницы: конфиг бэкенда + мост движка.
  UnmodifiableListView<UserScript> _userScripts() {
    final cfg = 'window.SYM_CONFIG = ${jsonEncode({'apiBase': widget.backendBase, 'live': true})};';
    return UnmodifiableListView<UserScript>([
      UserScript(source: cfg, injectionTime: UserScriptInjectionTime.AT_DOCUMENT_START),
      // sym_engine_bridge.js берём из ассетов пакета (или вставьте строкой).
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
            transparentBackground: true,
            supportZoom: false,
            disableContextMenu: true,
          ),
          onWebViewCreated: (controller) async {
            _channel.attach(controller);
            // Внедряем мост движка из ассета пакета.
            final bridge = await DefaultAssetBundle.of(context)
                .loadString('packages/symbiont_flutter_host/assets/sym_engine_bridge.js');
            await controller.addUserScript(
              userScript: UserScript(
                source: bridge,
                injectionTime: UserScriptInjectionTime.AT_DOCUMENT_START,
              ),
            );
          },
        ),
      ),
    );
  }
}
