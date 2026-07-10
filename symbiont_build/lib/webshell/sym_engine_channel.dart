// sym_engine_channel.dart
//
// Мост WebView ↔ движок приложения (SymbiontEngine). Оболочка (assets/webapp/)
// вызывает window.SYM_ENGINE — вызовы приходят JS-хендлером 'symEngine', а поток
// статуса движка уезжает обратно через window.SYM_ENGINE._emit(...).

import 'dart:async';
import 'dart:convert';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import '../engine/engine.dart';

class SymEngineChannel {
  final SymbiontEngine engine;
  InAppWebViewController? _web;
  StreamSubscription<ConnStatus>? _sub;

  SymEngineChannel(this.engine);

  void attach(InAppWebViewController controller) {
    _web = controller;
    controller.addJavaScriptHandler(
      handlerName: 'symEngine',
      callback: (args) async {
        final msg = (args.isNotEmpty && args.first is Map)
            ? Map<String, dynamic>.from(args.first as Map)
            : <String, dynamic>{};
        return await _handle(msg);
      },
    );
    _sub?.cancel();
    _sub = engine.status.listen(_pushStatus);
  }

  void dispose() {
    _sub?.cancel();
  }

  Future<Map<String, dynamic>> _handle(Map<String, dynamic> msg) async {
    final cmd = msg['cmd'] as String?;
    try {
      switch (cmd) {
        case 'connect':
          final node = msg['node'] is Map ? Map<String, dynamic>.from(msg['node']) : null;
          final nodeId = node?['id'] as String? ?? node?['host'] as String?;
          await engine.connect(nodeId: nodeId);
          return {'ok': true};
        case 'disconnect':
          await engine.disconnect();
          return {'ok': true};
        case 'status':
          return {'ok': true};
        default:
          return {'ok': false, 'error': 'unknown_cmd'};
      }
    } catch (e) {
      _emit({'conn': 'error', 'toast': 'Ошибка движка', 'toastKind': 'error'});
      return {'ok': false, 'error': e.toString()};
    }
  }

  void _pushStatus(ConnStatus s) {
    const phaseMap = {
      ConnPhase.off: 'idle',
      ConnPhase.connecting: 'connecting',
      ConnPhase.on: 'connected',
      ConnPhase.error: 'error',
    };
    final ev = <String, dynamic>{
      'conn': phaseMap[s.phase] ?? 'idle',
      'stage': 'tunnel', // расширьте, когда движок начнёт сообщать активную ступень
      if (s.pingMs != null) 'ping': s.pingMs,
      if (s.error != null) 'toast': s.error,
      if (s.error != null) 'toastKind': 'error',
    };
    _emit(ev);
  }

  void _emit(Map<String, dynamic> ev) {
    final js = 'window.SYM_ENGINE && window.SYM_ENGINE._emit(${jsonEncode(ev)});';
    _web?.evaluateJavascript(source: js);
  }
}
