// lib/engine/singbox_engine.dart
//
// Боевая реализация SymbiontEngine — ТОНКАЯ обёртка над нативным Sing-box.
// Здесь НЕТ логики туннелирования и обхода: всё это делает готовый open-source
// Sing-box, который запускается нативно (Android: VpnService; iOS/macOS:
// NetworkExtension Packet Tunnel; desktop: процесс/служба). Этот класс лишь
// переводит вызовы контракта в команды нативному слою через MethodChannel и
// конвертирует статус обратно. См. ARCHITECTURE.md §2–3.
//
// Что нужно сделать в нативной части (вне этого файла, готовыми средствами):
//   • встроить библиотеку sing-box (libbox) и поднять её как VPN-сервис ОС;
//   • принять конфиг (outbounds/route) и применить правила маршрутизации;
//   • отдавать статус и метрики в канал событий.
// Никакого собственного движка обхода мы не пишем — используем sing-box как есть.

import 'dart:async';
import 'dart:convert';
import 'package:flutter/services.dart';   // MethodChannel / EventChannel — реальный мост к нативу
import 'engine.dart';
import 'singbox_config.dart';

// Каналы — настоящие Flutter MethodChannel/EventChannel. Dart-сторона моста боевая;
// остаётся реализовать НАТИВНУЮ сторону (Android VpnService + libbox; iOS/macOS
// NetworkExtension) — обработчики канала 'symbiont/engine' и поток статуса
// 'symbiont/engine/status'. До появления натива движок не подключён в main.dart
// (мобильные сборки идут на MockEngine), поэтому MissingPluginException не возникает.

class SingboxEngine implements SymbiontEngine {
  static const _cmd = MethodChannel('symbiont/engine');       // команды → нативу
  static const _events = EventChannel('symbiont/engine/status'); // статус ← натива

  @override
  Stream<ConnStatus> get status =>
      _events.receiveBroadcastStream().map(_decodeStatus);

  ConnStatus _decodeStatus(dynamic e) {
    final m = (e as Map).cast<String, dynamic>();
    return ConnStatus(
      phase: ConnPhase.values.byName(m['phase'] as String? ?? 'off'),
      pingMs: m['pingMs'] as int?,
      lossPct: (m['lossPct'] as num?)?.toDouble(),
      protocol: m['protocol'] as String?,
      mode: CoverageMode.values.byName(m['mode'] as String? ?? 'smart'),
      error: m['error'] as String?,
    );
  }

  // последние применённые правила (для сборки конфига)
  List<RoutingRule> _rules = const [];

  @override
  Future<void> connect({String? nodeId, CoverageMode? mode}) {
    // Собираем КОНФИГ для готового движка. КАСКАД с автопереключением (urltest):
    //   primary  — VLESS+Reality поверх XHTTP (Xray-core, обход 16 КБ и TLS-in-TLS);
    //   secondary— Hysteria2 (UDP, Salamander+port-hopping);
    //   tertiary — Shadowsocks-2022 (TCP, фолбэк при блокировке UDP).
    // RELAY — «белый» внутрироссийский узел: весь трафик уходит через него (detour)
    // против CIDR/SNI-whitelist. Секреты/адреса/relay-IP — из подписанного манифеста.
    final id = nodeId ?? 'nl-01';
    final cascade = <NodeEndpoint>[
      NodeEndpoint.placeholder(id, protocol: 'reality', xrayCore: true), // XHTTP → Xray-core
      NodeEndpoint.placeholder(id, protocol: 'hysteria2'),
      NodeEndpoint.placeholder(id, protocol: 'ss2022'),
    ];
    // relay-узел приходит из манифеста; здесь плейсхолдер (Reality на «белом» РФ-IP).
    final relay = NodeEndpoint.placeholder('ru-relay', protocol: 'reality');
    final config = SingboxConfig.build(
      endpoint: cascade.first, cascade: cascade, relay: relay,
      rules: _rules, protection: const Protection(),
      sessionSeed: '$id-${DateTime.now().millisecondsSinceEpoch ~/ 86400000}', // стабилен в пределах суток
    );
    return _cmd.invokeMethod('connect', {
      'nodeId': nodeId, 'mode': mode?.name, 'config': jsonEncode(config),
    });
  }

  @override
  Future<void> disconnect() => _cmd.invokeMethod('disconnect');

  @override
  Future<List<NodeInfo>> listNodes() async {
    final raw = await _cmd.invokeMethod<List>('listNodes') ?? const [];
    return raw.map((e) {
      final m = (e as Map).cast<String, dynamic>();
      return NodeInfo(
        id: m['id'], country: m['country'], code: m['code'],
        pingMs: m['pingMs'], loadPct: m['loadPct'], favorite: m['favorite'] ?? false,
      );
    }).toList();
  }

  @override
  Future<NodeInfo> fastestNode() async {
    final nodes = await listNodes();
    // pingMs может быть null (узел ещё не измерен) — считаем такой пинг «худшим».
    return nodes.reduce((a, b) => (a.pingMs ?? 1 << 30) <= (b.pingMs ?? 1 << 30) ? a : b);
  }

  @override
  Future<void> setCoverage(CoverageMode mode) =>
      _cmd.invokeMethod('setCoverage', {'mode': mode.name});

  @override
  Future<void> applyRules(List<RoutingRule> rules) {
    _rules = rules; // запоминаем для следующей сборки конфига
    // Превращается в route.rules конфига sing-box на нативной стороне.
    return _cmd.invokeMethod('applyRules', {'rules': rules.map((r) => r.toJson()).toList()});
  }

  @override
  Future<List<ScanItem>> runAnalysis() async {
    // Натив сверяет установленные приложения (где можно) + домены с категориями
    // манифеста и делает лёгкие локальные пробы. Никакой инспекции чужого трафика.
    final raw = await _cmd.invokeMethod<List>('runAnalysis') ?? const [];
    return raw.map((e) {
      final m = (e as Map).cast<String, dynamic>();
      return ScanItem(
        id: m['id'], name: m['name'], kind: m['kind'],
        recommended: RouteAction.values.byName(m['recommended']),
        reasonCode: m['reasonCode'],
      );
    }).toList();
  }

  @override
  Future<void> setProtection(Protection p) =>
      _cmd.invokeMethod('setProtection', {
        'ads': p.ads, 'trackers': p.trackers, 'phishing': p.phishing,
        'killSwitch': p.killSwitch, 'dns': p.dns,
      });

  @override
  Future<String> diagnose(String target) async =>
      await _cmd.invokeMethod<String>('diagnose', {'target': target}) ?? 'unknown';

  @override
  Future<String?> autoTuneBypass(List<String> testHosts, {void Function(String stage)? onProgress}) async => null; // TODO: native

  @override
  Future<void> requestAdmin() async {} // только десктоп
  @override
  void setActiveNode(NodeInfo? node) {} // узлы-серверы только на десктопе
  @override
  void setProtoPreference(String choice) {} // выбор протокола (каркас mobile)
  @override
  void setRelay(NodeInfo? relay) {} // relay/detour (каркас mobile)
  @override
  Future<List<TrafficConn>> trafficConnections() async => const [];
  @override
  Future<List<InstalledApp>> scanApps() async => const []; // скан приложений — только десктоп // карта трафика (каркас mobile)
}
