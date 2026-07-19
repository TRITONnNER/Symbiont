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
          // КРИТИЧНО: отдаём движку АКТИВНЫЙ УЗЕЛ с секретами transport (host/port/
          // reality|hysteria2|ss2022). Без него desktop-движок не находит узел и
          // сваливается в DPI-обход вместо VPN к выбранному серверу. Оболочка (мост
          // в web_shell_page.dart) обогащает node этими полями из манифеста.
          if (node != null) {
            final t = node['transport'];
            engine.setActiveNode(NodeInfo(
              id: '${node['id'] ?? node['host'] ?? 'node'}',
              country: '${node['country'] ?? node['_country_en'] ?? node['code'] ?? ''}',
              code: '${node['code'] ?? ''}',
              loadPct: (node['load'] is num) ? (node['load'] as num).toInt() : 0,
              host: node['host'] as String?,
              port: (node['port'] is num) ? (node['port'] as num).toInt() : 443,
              transport: t is Map ? Map<String, dynamic>.from(t) : null,
            ));
          }
          final nodeId = node?['id'] as String? ?? node?['host'] as String?;
          await engine.connect(nodeId: nodeId);
          return {'ok': true};
        case 'disconnect':
          await engine.disconnect();
          return {'ok': true};
        case 'status':
          return {'ok': true};
        case 'scanApps':
          final apps = await engine.scanApps();
          return {'ok': true, 'apps': apps.map((a) => {'name': a.name, 'exe': a.exe, 'path': a.path}).toList()};
        case 'traffic':
          final cs = await engine.trafficConnections();
          return {'ok': true, 'conns': cs.map((c) => {'host': c.host, 'rule': c.rule, 'network': c.network, 'up': c.up, 'down': c.down}).toList()};
        case 'applyRules':
          final list = ((msg['rules'] as List?) ?? const [])
              .whereType<Map>()
              .map((r) => RoutingRule.fromJson(Map<String, dynamic>.from(r)))
              .toList();
          await engine.applyRules(list);
          return {'ok': true};
        case 'setProtection':
          final p = (msg['protection'] is Map) ? Map<String, dynamic>.from(msg['protection'] as Map) : <String, dynamic>{};
          await engine.setProtection(Protection(
            ads: p['ads'] == true, trackers: p['trackers'] == true, phishing: p['phishing'] == true,
            killSwitch: p['killSwitch'] == true, dns: (p['dns'] as String?) ?? 'DoH'));
          return {'ok': true};
        case 'analysis':
          final items = await engine.runAnalysis();
          return {'ok': true, 'items': items.map((s) => {
            'id': s.id, 'name': s.name, 'kind': s.kind,
            'recommended': s.recommended.name, 'reasonCode': s.reasonCode, 'override': s.override.name}).toList()};
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
