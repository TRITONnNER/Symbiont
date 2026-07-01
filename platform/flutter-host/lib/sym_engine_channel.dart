// sym_engine_channel.dart
//
// Мост WebView ↔ существующий движок (SymbiontEngine из
// symbiont_build/lib/engine/). Оболочка (webapp/) вызывает window.SYM_ENGINE,
// эти вызовы приходят сюда JS-хендлером 'symEngine', а поток статуса движка
// уезжает обратно в оболочку через window.SYM_ENGINE._emit(...).
//
// Так один и тот же UI работает на Windows/macOS/Linux/Android/iOS — меняется
// лишь реализация SymbiontEngine под платформу.

import 'dart:async';
import 'dart:convert';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

// Путь подставьте под свой проект (обычно пакет symbiont_build или относительный импорт).
import 'package:symbiont/engine/engine.dart';

class SymEngineChannel {
  final SymbiontEngine engine;
  InAppWebViewController? _web;
  StreamSubscription<ConnStatus>? _sub;
  List<NodeInfo> _nodes = const [];

  SymEngineChannel(this.engine);

  /// Зарегистрировать хендлер на контроллере WebView (в onWebViewCreated).
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

    // Поток статуса движка → события в оболочку.
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
          // Передаём активный узел (с secrets transport) движку, если знаем его.
          final match = _matchNode(nodeId, node);
          if (match != null) engine.setActiveNode(match);
          await engine.connect(nodeId: match?.id ?? nodeId);
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

  NodeInfo? _matchNode(String? id, Map<String, dynamic>? node) {
    if (id == null) return null;
    for (final n in _nodes) {
      if (n.id == id || n.host == id) return n;
    }
    return null;
  }

  /// (необязательно) закешировать узлы из манифеста, чтобы находить transport-секреты.
  void setNodes(List<NodeInfo> nodes) => _nodes = nodes;

  void _pushStatus(ConnStatus s) {
    const phaseMap = {
      ConnPhase.off: 'idle',
      ConnPhase.connecting: 'connecting',
      ConnPhase.on: 'connected',
      ConnPhase.error: 'error',
    };
    final ev = <String, dynamic>{
      'conn': phaseMap[s.phase] ?? 'idle',
      // Ступень каскада: по умолчанию tunnel в состоянии on. Если движок
      // сообщает relay/bypass — прокиньте здесь через s.protocol/mode.
      'stage': s.phase == ConnPhase.on ? 'tunnel' : 'tunnel',
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
