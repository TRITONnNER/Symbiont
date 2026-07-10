// lib/engine/engine.dart
//
// Контракт «UI ↔ движок». Весь интерфейс приложения общается ТОЛЬКО с
// SymbiontEngine и ничего не знает о протоколах/туннелях. За этим контрактом
// стоят две реализации: MockEngine (разработка) и SingboxEngine (боевая,
// оборачивает готовый Sing-box). См. ARCHITECTURE.md, разделы 2–3.

// ── перечисления ────────────────────────────────────────────────────────────
enum ConnPhase { off, connecting, on, error }

/// Режим охвата (см. том IV–V). smart — по умолчанию: помогаем только где нужно.
enum CoverageMode { smart, whole, selected, off }

/// Что делать с назначением. Совпадает с действиями экрана «Анализ».
enum RouteAction { direct, tunnel, bypass, boost, block }

/// На какой уровень вешается правило (модель domain-suffix, см. том V §3.2).
enum MatchKind { domainExact, domainSuffix, tld, keyword, processId }

// ── данные ──────────────────────────────────────────────────────────────────
class NodeInfo {
  final String id;          // напр. "nl-01"
  final String country;     // локализуемое имя выводит UI по code
  final String code;        // ISO-страна, для флага: "NL"
  final int? pingMs;        // РЕАЛЬНЫЙ замер; null = ещё не измерен / хост неизвестен
  final int loadPct;        // 0..100
  final bool favorite;
  final String? host;       // реальный хост для замера пинга (из манифеста)
  final int port;           // порт для замера (по умолчанию 443)
  final Map<String, dynamic>? transport; // секреты подключения по протоколам (reality/hysteria2/ss2022) из манифеста
  const NodeInfo({
    required this.id, required this.country, required this.code,
    this.pingMs, required this.loadPct, this.favorite = false,
    this.host, this.port = 443, this.transport,
  });

  NodeInfo copyWith({int? pingMs, bool? favorite}) => NodeInfo(
    id: id, country: country, code: code,
    pingMs: pingMs ?? this.pingMs, loadPct: loadPct,
    favorite: favorite ?? this.favorite, host: host, port: port, transport: transport,
  );
}

class ConnStatus {
  final ConnPhase phase;
  final NodeInfo? node;     // активный узел (если on)
  final int? pingMs;        // живая метрика
  final double? lossPct;
  final String? protocol;   // напр. "Reality" — для показа, не для логики
  final CoverageMode mode;
  final String? error;
  const ConnStatus({
    required this.phase, this.node, this.pingMs, this.lossPct,
    this.protocol, this.mode = CoverageMode.smart, this.error,
  });

  static const off = ConnStatus(phase: ConnPhase.off);
}

/// Гранулярное правило. Пример: {kind: domainSuffix, match: "sberbank.ru",
/// action: direct}. Приоритет разрешает движок: exact > suffix > tld > category.
class RoutingRule {
  final MatchKind kind;
  final String match;       // "sberbank.ru", ".ru", "online.sberbank.ru", processId
  final RouteAction action;
  final String? nodeId;     // опционально закрепить узел (для boost — игровой PoP)
  const RoutingRule({required this.kind, required this.match, required this.action, this.nodeId});

  Map<String, dynamic> toJson() => {
    'kind': kind.name, 'match': match, 'action': action.name,
    if (nodeId != null) 'nodeId': nodeId,
  };
  factory RoutingRule.fromJson(Map<String, dynamic> j) => RoutingRule(
    kind: MatchKind.values.byName(j['kind'] as String),
    match: j['match'] as String,
    action: RouteAction.values.byName(j['action'] as String),
    nodeId: j['nodeId'] as String?,
  );
}

/// Элемент результата «Анализа»: что нашли и что рекомендуем.
class ScanItem {
  final String id;
  final String name;        // "Банк / Госуслуги" (локализацию даёт каталог)
  final String kind;        // "bank" | "messenger" | "game" | "video" | ...
  final RouteAction recommended;
  final String reasonCode;  // "vpn_detect" | "blocked" | "lower_ping" | "no_help"
  RouteAction override;     // пользователь может переопределить
  ScanItem({
    required this.id, required this.name, required this.kind,
    required this.recommended, required this.reasonCode, RouteAction? override,
  }) : override = override ?? recommended;
}

/// Глобальные тумблеры защиты (экран «Настройки»).
class Protection {
  final bool ads, trackers, phishing, killSwitch;
  final String dns; // "DoH" | "DoT" | "DoQ" | "ODoH"
  const Protection({
    this.ads = false, this.trackers = false, this.phishing = false,
    this.killSwitch = false, this.dns = 'DoH',
  });
  Protection copyWith({bool? ads, bool? trackers, bool? phishing, bool? killSwitch, String? dns}) =>
    Protection(ads: ads ?? this.ads, trackers: trackers ?? this.trackers,
      phishing: phishing ?? this.phishing, killSwitch: killSwitch ?? this.killSwitch, dns: dns ?? this.dns);
}

/// Один сетевой поток из Clash API (карта трафика «что куда идёт»).
class TrafficConn {
  final String host;      // куда (домен/IP:порт)
  final String rule;      // как маршрутизировано: proxy / direct / reject
  final String network;   // tcp/udp
  final int up;           // отдано байт
  final int down;         // принято байт
  const TrafficConn({required this.host, required this.rule, required this.network, required this.up, required this.down});
}

/// Установленное приложение (для маршрутизации per-app). Найдено сканом системы.
class InstalledApp {
  final String name;     // имя программы
  final String exe;      // имя exe (для process_name правила), напр. "chrome.exe"
  final String? path;    // полный путь (если известен) — для извлечения иконки
  const InstalledApp({required this.name, required this.exe, this.path});
}

// ── контракт ──────────────────────────────────────────────────────────────────
abstract class SymbiontEngine {
  /// Поток статуса соединения — UID подписывается и перерисовывает экран «Защита».
  Stream<ConnStatus> get status;

  Future<void> connect({String? nodeId, CoverageMode? mode});
  Future<void> disconnect();

  Future<List<NodeInfo>> listNodes();
  Future<NodeInfo> fastestNode();

  Future<void> setCoverage(CoverageMode mode);

  /// Применить набор гранулярных правил (из манифеста + пользовательские).
  Future<void> applyRules(List<RoutingRule> rules);

  /// «Анализ»: вернуть список сервисов с рекомендованными действиями.
  /// На Android/desktop включает приложения; на iOS — по доменам/категориям.
  Future<List<ScanItem>> runAnalysis();

  Future<void> setProtection(Protection p);

  /// Локальная диагностика для «Починить интернет» (том V §4.2): тип блокировки и т.п.
  Future<String> diagnose(String target);

  /// Авто-подбор рабочей стратегии обхода: пробует варианты и проверяет, открылись ли
  /// заблокированные хосты. Возвращает применённую стратегию (строку) или null, если
  /// подобрать не удалось / движок не поддерживает. testHosts — заблокированные домены.
  /// onProgress — необязательный колбэк прогресса (для показа «пробую N из M» в UI).
  Future<String?> autoTuneBypass(List<String> testHosts, {void Function(String stage)? onProgress});

  /// Перезапустить приложение с правами администратора (UAC). Нужно для прозрачного
  /// обхода (zapret/GoodbyeDPI используют драйвер WinDivert). На не-десктопе — no-op.
  Future<void> requestAdmin();

  /// Передать активный узел (с секретами transport из манифеста) перед connect().
  /// Десктоп-движок строит из него клиентский конфиг и поднимает VPN. На остальных — no-op.
  void setActiveNode(NodeInfo? node);
  // Выбор протокола: 'auto' | 'reality' | 'hysteria2' | 'ss2022'.
  // По умолчанию — пустышка (движок волен игнорировать), чтобы новые фичи не ломали старые движки.
  void setProtoPreference(String choice) {}
  // Передать relay-узел («белый» IP) для detour против «16 КБ занавеса» / CIDR-whitelist.
  // null — без relay (прямой выход). По умолчанию — пустышка (движок волен игнорировать).
  void setRelay(NodeInfo? relay) {}
  // Карта трафика: активные соединения из Clash API. По умолчанию — пусто.
  Future<List<TrafficConn>> trafficConnections() async => const [];
  // Скан установленных приложений (для per-app маршрутизации). По умолчанию — пусто.
  Future<List<InstalledApp>> scanApps() async => const [];
}
