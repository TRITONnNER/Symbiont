// lib/engine/singbox_config.dart
// Сборщик КОНФИГА для готового движка (Sing-box; для XHTTP-узлов — Xray-core).
// По выбранному узлу + правилам + защите формирует config JSON: КАСКАД outbounds
// с автопереключением, DNS, route.rules, и (при наличии) RELAY-ЦЕПОЧКУ через
// «белый» внутрироссийский узел.
//
// Здесь НЕТ логики обхода — её выполняет готовый движок. Это только генератор
// конфига вокруг него. Все секреты (server/uuid/public_key/sni/пароли/relay-IP)
// приходят из подписанного Ed25519-манифеста; здесь — плейсхолдеры 'FROM_MANIFEST'.
//
// Выводы research (актуальность 31.05.2026), реализованные тут:
//   • основной транспорт Reality ПОВЕРХ XHTTP (без xtls-rprx-vision) — против
//     детекта Vision (#546) и структурного обхода «занавеса 16–20 КБ». XHTTP
//     поддерживает ТОЛЬКО Xray-core (sing-box — нет); для sing-box-движка есть
//     фолбэк transport=http/h3 (см. _proxyOutbound).
//   • uTLS-fingerprint синхронизируется с ЖИВЫМ Chrome (kTargetChromeVersion),
//     иначе устаревший отпечаток сам становится сигнатурой (урок Telegram: JA4
//     Chrome 134 при живом 148). Ротация — ПО СЕССИИ (не per-connection: постоянная
//     смена отпечатка с одного IP сама по себе аномалия). uTLS ≥ 1.8.2
//     (CVE-2026-26995 / CVE-2026-27017).
//   • КАСКАД с автопереключением: Reality/XHTTP (TCP) → Hysteria2 (UDP, Salamander+
//     port-hopping) → Shadowsocks-2022 (TCP). При блокировке UDP деградируем на TCP.
//   • health-check тянет >32 КБ (generate_204 < 16 КБ прошёл бы сквозь «занавес»);
//     полноценная проверка прокачки — в нативной обёртке (urltest меряет задержку).
//   • RELAY-цепочка: foreign-узел уходит через detour на российский «белый» IP —
//     главный приём против CIDR/SNI-whitelist. detour работает на стоковом sing-box.

import 'engine.dart';

/// Целевая версия Chrome для uTLS-отпечатка. ОБНОВЛЯТЬ при выходе новых релизов —
/// устаревший отпечаток детектируется (урок Telegram MTProto, май 2026).
/// Реальное значение приходит из манифеста; это — дефолт на момент сборки.
const int kTargetChromeVersion = 148;

/// Минимально безопасная версия uTLS (ниже — CVE-2026-26995/27017). Справочно.
const String kMinUtlsVersion = '1.8.2';

/// Параметры подключения к узлу (приходят из манифеста; здесь — плейсхолдеры).
class NodeEndpoint {
  final String id;
  final String server;
  final int port;
  final String protocol;   // "reality" | "hysteria2" | "ss2022"
  final bool xrayCore;     // true → узел обслуживается Xray-core (нужно для XHTTP)
  final Map<String, dynamic> params;
  const NodeEndpoint({
    required this.id, required this.server, required this.port,
    required this.protocol, this.xrayCore = false, this.params = const {},
  });

  factory NodeEndpoint.placeholder(String id, {String protocol = 'reality', bool xrayCore = false}) =>
      NodeEndpoint(
        id: id, server: 'REPLACE_WITH_SERVER_FROM_MANIFEST', port: 443,
        protocol: protocol, xrayCore: xrayCore,
        params: const {'note': 'значения транспорта приходят из подписанного манифеста'},
      );
}

class SingboxConfig {
  /// Пул uTLS-отпечатков (НЕ дефолтный chrome статикой). Конкретный — по сессии.
  static const fingerprintPool = ['chrome', 'firefox', 'safari', 'edge', 'ios'];

  /// Выбор отпечатка по сессии (а не per-connection): стабильность в пределах
  /// сессии + различие между сессиями. `sessionSeed` задаёт натив/обёртка.
  static String pickFingerprint(String seed) =>
      fingerprintPool[seed.hashCode.abs() % fingerprintPool.length];

  /// Основной билдер.
  /// [cascade] — список протоколов для автопереключения (primary первым).
  /// [relay] — необязательный «белый» внутрироссийский узел: все proxy-узлы
  ///           уходят через него (detour) против whitelist и «занавеса 16 КБ».
  /// [healthCheckUrl] — URL, отдающий >32 КБ (из манифеста), для urltest.
  /// [sessionSeed] — семя ротации отпечатка на текущую сессию.
  static Map<String, dynamic> build({
    required NodeEndpoint endpoint,
    required List<RoutingRule> rules,
    required Protection protection,
    List<NodeEndpoint>? cascade,
    NodeEndpoint? relay,
    String? healthCheckUrl,
    String? sessionSeed,
    String inbound = 'tun', // 'tun' (полный VPN, нужен админ+Wintun) | 'mixed' (локальный прокси, без админа)
    bool clashApi = true,   // включить Clash API (127.0.0.1:9090) для управления/метрик
  }) {
    final proxies = (cascade == null || cascade.isEmpty) ? [endpoint] : cascade;
    final seed = sessionSeed ?? endpoint.id;

    final proxyOutbounds = <Map<String, dynamic>>[];
    for (var i = 0; i < proxies.length; i++) {
      // если задан relay — каждый proxy уходит через него (detour)
      proxyOutbounds.add(_proxyOutbound(proxies[i], tag: 'proxy-$i', seed: seed,
          detour: relay != null ? 'relay' : null));
    }
    final proxyTags = [for (var i = 0; i < proxies.length; i++) 'proxy-$i'];

    final outbounds = <Map<String, dynamic>>[
      {
        // селектор авто-выбора живого протокола (каскад/failover)
        'type': 'urltest', 'tag': 'proxy',
        'outbounds': proxyTags,
        // >32 КБ: generate_204 (<16 КБ) прошёл бы сквозь «занавес», скрыв throttling.
        // ВАЖНО: urltest меряет ЗАДЕРЖКУ до первого ответа, не реальную прокачку —
        // полноценный throughput-health-check делает нативная обёртка (Clash API).
        'url': healthCheckUrl ?? 'https://speed.cloudflare.com/__down?bytes=50000',
        'interval': '3m', 'tolerance': 50, 'idle_timeout': '15m',
      },
      ...proxyOutbounds,
      if (relay != null) _relayOutbound(relay, seed: seed),
      {'type': 'direct', 'tag': 'direct'},
      // dns-outbound и block-outbound удалены в sing-box 1.13 — их заменяют
      // действия в правилах (action: hijack-dns / reject). См. _routeRules.
    ];

    return {
      'log': {'level': 'warn'},
      'dns': {
        'servers': [
          // новый формат sing-box 1.12+: сервер описывается типом, а не строкой address.
          // detour:proxy — DoH-запрос идёт ЧЕРЕЗ туннель, иначе сам DNS-запрос
          // может резаться (сайты получают статус dns, как видно в логах).
          {..._dnsServer(protection.dns), 'tag': 'secure', 'detour': 'proxy'},
          {'type': 'udp', 'tag': 'direct-dns', 'server': '223.5.5.5'},
        ],
        // в sing-box 1.13 правило {outbound:any, server:...} удалено; DNS-сервер
        // по умолчанию задаётся через final. Весь резолвинг идёт через 'secure'.
        'final': 'secure',
        'strategy': 'prefer_ipv4',
      },
      'inbounds': [
        if (inbound == 'mixed')
          // локальный SOCKS/HTTP-прокси: работает БЕЗ прав администратора.
          // sniff удалён в sing-box 1.13 (теперь это действие в route, не поле inbound).
          {'type': 'mixed', 'tag': 'mixed-in', 'listen': '127.0.0.1', 'listen_port': 2080}
        else
          // полный VPN: перехват всего трафика. Нужны админ + Wintun.dll (Windows).
          {'type': 'tun', 'tag': 'tun-in', 'address': ['172.19.0.1/30'],
           'auto_route': true, 'strict_route': true, 'stack': 'system'},
      ],
      'outbounds': outbounds,
      'route': {
        'rules': _routeRules(rules, protection),
        'final': 'proxy',
        'auto_detect_interface': true,
        // sing-box 1.13: резолвер по умолчанию для адресов серверов-узлов
        // (наши узлы обычно по IP, но это убирает предупреждение и работает на доменных).
        'default_domain_resolver': {'server': 'direct-dns'},
        // ОПРЕДЕЛЕНИЯ rule-set'ов для блокировки (иначе ссылка 'ads' не резолвится).
        // Списки тянутся с официального репозитория sing-box (формата .srs).
        if (_enabledBlockSets(protection).isNotEmpty)
          'rule_set': [
            for (final s in _enabledBlockSets(protection))
              {
                'type': 'remote', 'tag': s, 'format': 'binary',
                'url': _ruleSetUrl(s),
                'download_detour': 'proxy',
              },
          ],
      },
      // Clash API для управления (переключение узла) и реальных метрик трафика.
      // Это ВАЛИДНОЕ поле sing-box (в отличие от произвольных ключей, которые
      // sing-box отвергает при строгой проверке конфига).
      // cache_file НЕ включаем: его инициализация может зависать по таймауту
      // (особенно когда сеть режется), а для работы туннеля он не нужен.
      if (clashApi) 'experimental': {
        'clash_api': {'external_controller': '127.0.0.1:9090'},
      },
    };
  }

  /// Relay-узел («белый» внутрироссийский IP). Обычно VLESS+Reality (TCP),
  /// чтобы выглядеть как HTTPS к российскому серверу из whitelist.
  static Map<String, dynamic> _relayOutbound(NodeEndpoint r, {required String seed}) {
    final o = _proxyOutbound(r, tag: 'relay', seed: seed, detour: null);
    return o;
  }

  // outbound по протоколу — ШАБЛОНЫ (секреты = плейсхолдеры). [detour] — тег
  // вышестоящего outbound (relay): если задан, трафик идёт сначала через него.
  static Map<String, dynamic> _proxyOutbound(NodeEndpoint e,
      {required String tag, required String seed, String? detour}) {
    switch (e.protocol) {
      case 'hysteria2':
        // UDP-резерв: Salamander (маскировка QUIC под шум) + Brutal + port-hopping.
        // При тотальной блокировке UDP каскад деградирует на TCP-узлы.
        return {
          'type': 'hysteria2', 'tag': tag,
          'server': e.server, 'server_port': e.port,
          if (e.params['server_ports'] != null) 'server_ports': e.params['server_ports'],
          if (e.params['hop_interval'] != null) 'hop_interval': e.params['hop_interval'],
          'password': e.params['password'] ?? 'FROM_MANIFEST',
          // obfs (salamander) шлём ТОЛЬКО если узел его реально включил (manifest даёт
          // пароль obfs). Наш gen_server obfs не настраивает → клиент его НЕ добавляет,
          // иначе сервер без obfs отвергнет рукопожатие Hysteria2.
          if (e.params['obfs'] != null)
            'obfs': {'type': 'salamander', 'password': e.params['obfs']},
          'tls': {
            'enabled': true,
            'server_name': e.params['sni'] ?? 'FROM_MANIFEST',
            'alpn': ['h3'],
            // Узел поднимается с САМОПОДПИСАННЫМ сертом (install.sh генерит cert.pem
            // без CA) — клиент не проверит цепочку и без этого отверг бы рукопожатие.
            // Hysteria2 аутентифицируется ПАРОЛЕМ (+obfs), а не сертом, поэтому
            // самоподписанный серт безопасно принять. Узел с настоящим сертом
            // (домен+CA) выключает это, прислав insecure:false в transport манифеста.
            'insecure': e.params['insecure'] != false,
          },
          'up_mbps': e.params['up_mbps'] ?? 50,
          'down_mbps': e.params['down_mbps'] ?? 200,
          if (detour != null) 'detour': detour,
        };
      case 'ss2022':
        // последний резерв (TCP). mux ВЫКЛЮЧЕН по умолчанию: мультиплекс схлопывает
        // трафик в один поток — это аномалия против браузерного профиля (research).
        return {
          'type': 'shadowsocks', 'tag': tag,
          'server': e.server, 'server_port': e.port,
          // метод берём из манифеста (сервер может сменить шифр) с безопасным дефолтом
          'method': e.params['method'] ?? '2022-blake3-aes-128-gcm',
          'password': e.params['password'] ?? 'FROM_MANIFEST',
          if (detour != null) 'detour': detour,
        };
      case 'reality':
      default:
        // ОСНОВНОЙ: VLESS+Reality. Транспорт зависит от движка узла:
        //   • Xray-core → XHTTP (mode=auto, X-Padding, XMUX) — обход 16 КБ и TLS-in-TLS;
        //   • sing-box  → transport=http поверх h3 (XHTTP недоступен) + tls.fragment.
        final useXhttp = e.xrayCore;
        final tls = <String, dynamic>{
          'enabled': true,
          'server_name': e.params['sni'] ?? 'FROM_MANIFEST',
          'utls': {
            'enabled': true,
            'fingerprint': e.params['fingerprint'] ?? pickFingerprint(seed),
          },
          'reality': {
            'enabled': true,
            'public_key': e.params['public_key'] ?? 'FROM_MANIFEST',
            'short_id': e.params['short_id'] ?? 'FROM_MANIFEST',
          },
        };
        // tls.fragment — лёгкая фрагментация рукопожатия (помогает на sing-box-пути,
        // где нет XHTTP). На Xray-пути дробление даёт сам XHTTP.
        if (!useXhttp) {
          tls['fragment'] = true;
          tls['fragment_fallback_delay'] = '500ms';
        }
        final out = <String, dynamic>{
          'type': 'vless', 'tag': tag,
          'server': e.server, 'server_port': e.port,
          'uuid': e.params['uuid'] ?? 'FROM_MANIFEST',
          'flow': '', // без xtls-rprx-vision (детектируется #546; несовместим с XHTTP)
          'tls': tls,
          if (detour != null) 'detour': detour,
        };
        if (useXhttp) {
          // XHTTP — ТОЛЬКО Xray-core. mode=auto (Reality → stream-one), X-Padding, XMUX.
          out['transport'] = {
            'type': 'xhttp',
            'path': e.params['path'] ?? '/',
            'mode': 'auto',
            'x_padding_bytes': e.params['x_padding'] ?? '100-1000',
          };
        }
        // sing-box-узел (наш gen_server): VLESS+Reality поверх RAW-TCP, БЕЗ transport —
        // ровно так его поднимает сервер. В VLESS транспорт клиента и сервера обязан
        // совпадать; добавлять transport:http нельзя — сервер его не слушает, и
        // рукопожатие не сойдётся (а sing-box check синтаксис пропустит — провал был
        // бы только в рантайме). Дробление рукопожатия на этом пути даёт tls.fragment.
        return out;
    }
  }

  // RoutingRule → route.rules sing-box
  static List<Map<String, dynamic>> _routeRules(List<RoutingRule> rules, Protection p) {
    final out = <Map<String, dynamic>>[];
    // sing-box 1.13: DNS перехватывается действием hijack-dns, а не outbound'ом dns-out.
    out.add({'protocol': 'dns', 'action': 'hijack-dns'});

    final blockSets = _enabledBlockSets(p);
    if (blockSets.isNotEmpty) {
      // блокировка — теперь action: reject (block-outbound удалён в 1.13)
      out.add({'rule_set': blockSets, 'action': 'reject'});
    }

    for (final r in rules) {
      final m = <String, dynamic>{};
      switch (r.kind) {
        case MatchKind.domainExact: m['domain'] = [r.match]; break;
        case MatchKind.domainSuffix: m['domain_suffix'] = [r.match]; break;
        case MatchKind.tld: m['domain_suffix'] = [r.match]; break;
        case MatchKind.keyword: m['domain_keyword'] = [r.match]; break;
        case MatchKind.processId: m['process_name'] = [r.match]; break;
      }
      // block → action:reject (1.13); остальное → outbound (direct/proxy)
      if (r.nodeId != null) {
        m['outbound'] = 'proxy';
      } else if (r.action == RouteAction.block) {
        m['action'] = 'reject';
      } else {
        m['outbound'] = _actionTag(r.action);
      }
      out.add(m);
    }
    return out;
  }

  static List<String> _enabledBlockSets(Protection p) => [
    if (p.ads) 'ads', if (p.trackers) 'trackers', if (p.phishing) 'threats',
  ];

  // Официальные гео-списки sing-box (.srs). Маппинг наших тегов на реальные наборы.
  static String _ruleSetUrl(String tag) {
    const base = 'https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set';
    switch (tag) {
      case 'ads': return '$base/geosite-category-ads-all.srs';
      case 'trackers': return '$base/geosite-category-ads-all.srs'; // трекеры в том же наборе
      case 'threats': return '$base/geosite-malware.srs';
      default: return '$base/geosite-category-ads-all.srs';
    }
  }

  static String _actionTag(RouteAction a) {
    switch (a) {
      case RouteAction.direct: return 'direct';
      case RouteAction.block: return 'block';
      case RouteAction.tunnel:
      case RouteAction.bypass:
      case RouteAction.boost:
        return 'proxy';
    }
  }

  // Новый формат DNS-сервера sing-box 1.12+ (вместо устаревшей строки address).
  // DoH/DoT/DoQ/ODoH → соответствующий type + server/host.
  static Map<String, dynamic> _dnsServer(String dns) {
    switch (dns) {
      case 'DoT':
        return {'type': 'tls', 'server': '1.1.1.1'};
      case 'DoQ':
        return {'type': 'quic', 'server': '1.1.1.1'};
      case 'ODoH':
        // oblivious DoH; если сборка sing-box не поддерживает — будет обычный https
        return {'type': 'https', 'server': 'odoh.cloudflare-dns.com', 'path': '/dns-query'};
      case 'DoH':
      default:
        return {'type': 'https', 'server': '1.1.1.1', 'path': '/dns-query'};
    }
  }
}
