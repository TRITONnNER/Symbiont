// lib/engine/mock_engine.dart
//
// Лёгкая реализация SymbiontEngine БЕЗ выдуманных данных. Туннеля пока нет
// (это нативный шаг — SingboxEngine), поэтому здесь только машина ФАЗ
// подключения (off/connecting/on) и режим охвата. Никаких фейковых узлов,
// пинга, потерь или протокола: реальный пинг измеряет AppState (Probe),
// список узлов приходит из подписанного манифеста, протокол — из манифеста.
//
// diagnose() делает РЕАЛЬНУЮ проверку доступности цели (TCP-connect), а не
// заранее заданный ответ.

import 'dart:async';
import 'engine.dart';
import '../net/probe.dart';

class MockEngine implements SymbiontEngine {
  final _ctrl = StreamController<ConnStatus>.broadcast();
  ConnStatus _cur = ConnStatus.off;

  MockEngine() { _emit(ConnStatus.off); }
  void _emit(ConnStatus s) { _cur = s; _ctrl.add(s); }

  @override
  Stream<ConnStatus> get status => _ctrl.stream;

  @override
  Future<void> connect({String? nodeId, CoverageMode? mode}) async {
    // только фаза: реальные метрики (ping/протокол) проставляет AppState/манифест.
    _emit(ConnStatus(phase: ConnPhase.connecting, mode: mode ?? _cur.mode));
    await Future.delayed(const Duration(milliseconds: 700));
    _emit(ConnStatus(phase: ConnPhase.on, mode: mode ?? CoverageMode.smart));
  }

  @override
  Future<void> disconnect() async => _emit(ConnStatus(phase: ConnPhase.off, mode: _cur.mode));

  @override
  Future<List<NodeInfo>> listNodes() async => const []; // узлы — из манифеста, не отсюда

  @override
  Future<NodeInfo> fastestNode() async =>
      throw StateError('fastestNode не используется: выбор по реальному пингу в AppState');

  @override
  Future<void> setCoverage(CoverageMode mode) async {
    if (mode == CoverageMode.off) { await disconnect(); return; }
    _emit(ConnStatus(phase: _cur.phase, mode: mode));
  }

  @override
  Future<void> applyRules(List<RoutingRule> rules) async {
    await Future.delayed(const Duration(milliseconds: 80)); // в боевом — передаётся движку
  }

  @override
  Future<List<ScanItem>> runAnalysis() async {
    // Реальный анализ запущенных приложений/соединений требует нативного кода
    // на каждой платформе. Пока его нет — НЕ выдаём выдуманный список, возвращаем
    // пусто; экран «Анализ» честно показывает, что это будет после движка.
    return const [];
  }

  @override
  Future<void> setProtection(Protection p) async {
    await Future.delayed(const Duration(milliseconds: 60));
  }

  @override
  Future<String> diagnose(String target) async {
    // РЕАЛЬНАЯ проверка: доступен ли target по 443. Не доказывает отсутствие
    // блокировки по TLS, но честно отличает «есть связь» от «недоступно».
    final p = await Probe.tcpPing(target, 443, timeout: const Duration(seconds: 3));
    return p == null ? 'unreachable' : 'reachable';
  }

  @override
  Future<String?> autoTuneBypass(List<String> testHosts, {void Function(String stage)? onProgress}) async => null; // нет инструмента обхода

  @override
  Future<void> requestAdmin() async {} // только десктоп
  @override
  void setActiveNode(NodeInfo? node) {} // узлы-серверы только на десктопе
  @override
  void setProtoPreference(String choice) {} // выбор протокола — только десктоп
  @override
  void setRelay(NodeInfo? relay) {} // relay/detour — только десктоп
  @override
  Future<List<TrafficConn>> trafficConnections() async => const [];
  @override
  Future<List<InstalledApp>> scanApps() async => const []; // скан приложений — только десктоп // карта трафика — только десктоп (Clash API)
}
