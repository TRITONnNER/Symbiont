// lib/api/api_client.dart
// Реальный слой клиент↔бэкенд (см. backend/API.md). Делает то, что можно без
// боевого окружения: создаёт анонимный аккаунт, тянет ПОДПИСАННЫЙ манифест и
// ПРОВЕРЯЕТ подпись Ed25519, гасит ключ, пишет в поддержку.
//
// Зависимости (добавлены в pubspec): http, cryptography.
//
// ВАЖНО про подпись: бэкенд подписывает каноническую форму тела манифеста БЕЗ
// поля sig: json с отсортированными ключами, без пробелов, UTF-8
// (Python: json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)).
// Канонизатор ниже воспроизводит ровно это. Если меняете формат — синхронизируйте
// обе стороны (backend/manifest.py ↔ этот файл).

import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:cryptography/cryptography.dart';

import '../engine/engine.dart';

class Manifest {
  final int version;
  final int rollout;        // % волны canary-выкатки (0..100); 100 = всем
  final List<NodeInfo> nodes;
  final List<NodeInfo> relays;  // «белые» relay-узлы для detour (не показываем в списке)
  final List<RoutingRule> rules;
  final Map<String, dynamic> raw;
  const Manifest({required this.version, this.rollout = 100, required this.nodes,
      this.relays = const [], required this.rules, required this.raw});
}

class ApiClient {
  final String baseUrl;            // напр. http://10.0.2.2:8000 (эмулятор Android → localhost ПК)
  String? _trustedPubKeyB64;       // открытый ключ Ed25519 (base64 raw32); вшивается заранее
  String? token;                   // Bearer-токен аккаунта (может прийти из Store)

  ApiClient(this.baseUrl, {String? trustedPubKeyB64, this.token}) : _trustedPubKeyB64 = trustedPubKeyB64;

  Map<String, String> get _auth =>
      {'content-type': 'application/json', if (token != null) 'authorization': 'Bearer $token'};

  // ── Сетевой таймаут на КАЖДЫЙ запрос ────────────────────────────────────────
  // package:http сам таймаутов не ставит: зависший/чёрнодырный бэкенд (LB глотает
  // один путь, captive-proxy, перегруз) подвешивал future НАВСЕГДА, а в state-слое
  // залипал busy-флаг (спиннер онбординга/подключения крутился вечно). Все вызовы
  // идут через эти обёртки — таймаут гарантирован и для будущих методов.
  static const Duration _netTimeout = Duration(seconds: 15);
  static Never _timedOut() => throw ApiError(0, 'timeout');
  Future<http.Response> _hget(Uri u, {Map<String, String>? headers, Duration? timeout}) =>
      http.get(u, headers: headers).timeout(timeout ?? _netTimeout, onTimeout: _timedOut);
  Future<http.Response> _hpost(Uri u, {Map<String, String>? headers, Object? body}) =>
      http.post(u, headers: headers, body: body).timeout(_netTimeout, onTimeout: _timedOut);
  Future<http.Response> _hpatch(Uri u, {Map<String, String>? headers, Object? body}) =>
      http.patch(u, headers: headers, body: body).timeout(_netTimeout, onTimeout: _timedOut);

  /// Доступен ли бэкенд (для онбординга: офлайн-режим vs реальный). Короткий
  /// таймаут (4с) для быстрого определения офлайна.
  Future<bool> ping() async {
    try {
      final r = await _hget(Uri.parse('$baseUrl/v1/pubkey'), timeout: const Duration(seconds: 4));
      return r.statusCode == 200;
    } catch (_) { return false; }
  }

  // ── 1. анонимный аккаунт ────────────────────────────────────────────────────
  Future<String> createAnonAccount({String? label}) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/account/anon'),
        headers: {'content-type': 'application/json'}, body: jsonEncode({'label': label}));
    _need(r, 200);
    token = jsonDecode(r.body)['token'] as String;
    return token!;
  }

  /// Переименование метки (косметика). PATCH /v1/account/label.
  Future<void> setLabel(String label) async {
    final r = await _hpatch(Uri.parse('$baseUrl/v1/account/label'),
        headers: _auth, body: jsonEncode({'label': label}));
    _need(r, 200);
  }

  // ── 2. погашение ключа ──────────────────────────────────────────────────────
  Future<Map<String, dynamic>> redeemKey(String code) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/key/redeem'), headers: _auth, body: jsonEncode({'code': code}));
    if (r.statusCode != 200) {
      throw ApiError(r.statusCode, _detail(r)); // 409 already, 410 revoked, 422 invalid
    }
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Проверить ключ БЕЗ активации (публичный чекер). Не требует авторизации и
  /// ничего не мутирует. Возвращает {status, ok, message, grants, uses_left,
  /// uses_total, expires_at, registered}. status ∈ valid|already_redeemed|
  /// expired|revoked|not_found|invalid. Всегда 200 (статус — в теле).
  Future<Map<String, dynamic>> checkKey(String code) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/key/check'),
        headers: {'content-type': 'application/json'}, body: jsonEncode({'code': code}));
    _need(r, 200);
    return jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
  }

  // ── 2b. Аккаунты через алиасы (крипто-личность) ─────────────────────────────
  /// PoW-челлендж для регистрации (анти-фрод). bits=0 → PoW выключен.
  Future<Map<String, dynamic>> getPow() async {
    final r = await _hget(Uri.parse('$baseUrl/v1/account/pow'));
    _need(r, 200);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Решить PoW-челлендж: найти nonce, у которого sha256("challenge:nonce") имеет
  /// >= [bits] ведущих нулевых бит. 1:1 с backend identity.pow_ok
  /// (там: int(sha256) < 2^(256-bits)). bits<=0 → PoW выключен, ответ не нужен.
  Future<String?> solvePow(String challenge, int bits) async {
    if (bits <= 0) return null;
    final sha = Sha256();
    // Верхняя граница как у JS-клиентов (5M): враждебный/битый бэкенд с огромным
    // bits не должен жечь CPU и вечно подвешивать регистрацию — лучше чистая ошибка.
    const maxIter = 5000000;
    for (var i = 0; i < maxIter; i++) {
      final h = await sha.hash(utf8.encode('$challenge:$i'));
      if (_leadingZeroBits(h.bytes) >= bits) return '$i';
    }
    throw ApiError(0, 'pow_unsolved');
  }

  static int _leadingZeroBits(List<int> bytes) {
    var count = 0;
    for (final b in bytes) {
      if (b == 0) { count += 8; continue; }
      for (var mask = 0x80; mask != 0; mask >>= 1) {
        if ((b & mask) != 0) return count;
        count++;
      }
      break;
    }
    return count;
  }

  /// Регистрация: любые алиасы [{value,kind}], опц. пароль/устройство/инвайт/PoW.
  Future<Map<String, dynamic>> register({
    required List<Map<String, String>> aliases,
    String? password,
    Map<String, String>? device,
    String? invite,
    String? powChallenge,
    String? powNonce,
  }) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/account/register'),
        headers: {'content-type': 'application/json'},
        body: jsonEncode({
          'aliases': aliases,
          if (password != null) 'password': password,
          if (device != null) 'device': device,
          if (invite != null) 'invite': invite,
          if (powChallenge != null) 'pow_challenge': powChallenge,
          if (powNonce != null) 'pow_nonce': powNonce,
        }));
    _need(r, 200);
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    token = d['token'] as String?;
    return d;
  }

  /// Вход по алиасу ИЛИ recovery-коду (опц. пароль/устройство).
  Future<Map<String, dynamic>> login({
    Map<String, String>? alias,
    String? recoveryCode,
    String? password,
    Map<String, String>? device,
  }) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/account/login'),
        headers: {'content-type': 'application/json'},
        body: jsonEncode({
          if (alias != null) 'alias': alias,
          if (recoveryCode != null) 'recovery_code': recoveryCode,
          if (password != null) 'password': password,
          if (device != null) 'device': device,
        }));
    _need(r, 200);
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    token = d['token'] as String?;
    return d;
  }

  Future<Map<String, dynamic>> recover(String recoveryCode, {Map<String, String>? device}) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/account/recover'),
        headers: {'content-type': 'application/json'},
        body: jsonEncode({'recovery_code': recoveryCode, if (device != null) 'device': device}));
    _need(r, 200);
    final d = jsonDecode(r.body) as Map<String, dynamic>;
    token = d['token'] as String?;
    return d;
  }

  // ── 2c. Устройства ───────────────────────────────────────────────────────────
  Future<List<Map<String, dynamic>>> listDevices() async {
    final r = await _hget(Uri.parse('$baseUrl/v1/account/devices'), headers: _auth);
    _need(r, 200);
    return (jsonDecode(r.body)['devices'] as List).cast<Map<String, dynamic>>();
  }

  Future<void> revokeDevice(String id) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/account/devices/revoke'), headers: _auth, body: jsonEncode({'device_id': id}));
    _need(r, 200);
  }

  Future<void> promoteDevice(String id) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/account/devices/promote'), headers: _auth, body: jsonEncode({'device_id': id}));
    _need(r, 200);
  }

  // ── 2d. Экономика / оплата / баланс / рефералы ────────────────────────────────
  /// Серверная экономика: тарифы/цены/награды/репутация (рендерим из этого).
  Future<Map<String, dynamic>> economy() async {
    final r = await _hget(Uri.parse('$baseUrl/v1/config/economy'));
    _need(r, 200);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Фиче-флаги: видимость блоков (тот же манифест, что читает сайт). Гейтим UI
  /// из этого; неизвестный/отсутствующий флаг считаем включённым (fail-open).
  Future<Map<String, dynamic>> flags() async {
    final r = await _hget(Uri.parse('$baseUrl/v1/config/flags'));
    _need(r, 200);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Разбор пользовательской ссылки «свой мост» (vless/ss/hysteria2) → узел каскада.
  /// Stateless: сервер ничего не хранит; мост клиент держит локально. Ответ:
  /// {ok, node:{protocol,server,port,params,warnings}} либо {ok:false, error, message}.
  Future<Map<String, dynamic>> parseBridge(String uri) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/config/parse-bridge'),
        headers: {'Content-Type': 'application/json'}, body: jsonEncode({'uri': uri}));
    _need(r, 200);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Покупка: product ('premium_month'…'balance_100h'), method ('mir'|'visa'|'mastercard'|'sbp'|'crypto'…).
  /// region ('ru'→₽ / 'intl'→$) — иначе бэкенд считает по умолчанию 'ru' и intl-юзер
  /// видит цену в $, а списывают ₽.
  Future<Map<String, dynamic>> purchase(String product, String method, {String region = 'ru'}) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/billing/purchase'), headers: _auth, body: jsonEncode({'product': product, 'method': method, 'region': region}));
    _need(r, 200);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> billingStatus() async {
    final r = await _hget(Uri.parse('$baseUrl/v1/billing/status'), headers: _auth);
    _need(r, 200);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Статус конкретного платежа (экран «Платёж обрабатывается» → «Оплачено»).
  /// status ∈ pending | completed | failed. 404 — чужой/несуществующий платёж.
  Future<Map<String, dynamic>> paymentStatus(String paymentId) async {
    final r = await _hget(Uri.parse('$baseUrl/v1/billing/payment/$paymentId'), headers: _auth);
    _need(r, 200);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// История начислений/списаний (экран «История начислений»). Новые записи сверху.
  /// Записи: {at, kind, ...}; kind ∈ purchase|grant|wheel|ref_earn|debit.
  Future<List<Map<String, dynamic>>> ledger() async {
    final r = await _hget(Uri.parse('$baseUrl/v1/billing/ledger'), headers: _auth);
    _need(r, 200);
    return (jsonDecode(utf8.decode(r.bodyBytes))['entries'] as List).cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> referralInfo() async {
    final r = await _hget(Uri.parse('$baseUrl/v1/referral'), headers: _auth);
    _need(r, 200);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  // ── 2e. Колесо фортуны (ежедневный бонус) ─────────────────────────────────────
  Future<Map<String, dynamic>> wheelInfo() async {
    final r = await _hget(Uri.parse('$baseUrl/v1/wheel'), headers: _auth);
    _need(r, 200);
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  /// Крутить колесо. 409 — уже крутил сегодня.
  Future<Map<String, dynamic>> wheelSpin() async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/wheel/spin'), headers: _auth);
    if (r.statusCode != 200) throw ApiError(r.statusCode, _detail(r));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  // ── 3. подписанный манифест + проверка подписи ──────────────────────────────
  Future<Manifest?> fetchManifest({int since = 0}) async {
    final r = await _hget(Uri.parse('$baseUrl/v1/manifest?since=$since'));
    if (r.statusCode == 304) return null; // актуально
    _need(r, 200);
    final m = jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;

    final pub = await _pubKey();
    final ok = await _verify(m, pub);
    if (!ok) throw ApiError(0, 'manifest_signature_invalid'); // отвергаем неподписанное

    // Толерантные приведения: даже подписанный манифест мог сериализовать число
    // как double/строку (напр. port:"443", loadPct:12.0) — жёсткий `as int` кинул
    // бы CastError, и весь список узлов молча не загрузился бы. Коэрсим мягко.
    int _asInt(Object? v, int fallback) {
      if (v is int) return v;
      if (v is num) return v.toInt();
      if (v is String) return int.tryParse(v) ?? fallback;
      return fallback;
    }
    NodeInfo _toNode(Map<String, dynamic> n) => NodeInfo(
        id: '${n['id']}', country: '${n['country'] ?? n['code'] ?? ''}', code: '${n['code'] ?? ''}',
        pingMs: null, // РЕАЛЬНЫЙ пинг измерит клиент (если задан host)
        loadPct: _asInt(n['loadPct'], 0),
        host: n['host'] as String?, port: _asInt(n['port'], 443),
        transport: (n['transport'] as Map?)?.cast<String, dynamic>());

    final allNodes = ((m['nodes'] as List?) ?? const [])
        .map((e) => (e as Map).cast<String, dynamic>()).toList();
    bool isRelay(Map<String, dynamic> n) => (n['roles'] as List?)?.contains('relay') ?? false;
    // relay-узлы («белые» IP для detour) — инфраструктура, не точки выхода: в списке
    // пользователю их НЕ показываем, но движку отдаём (для обхода занавеса/whitelist).
    final nodes = allNodes.where((n) => !isRelay(n)).map(_toNode).toList();
    final relays = allNodes.where(isRelay).map(_toNode).toList();
    final rules = ((m['rules'] as List?) ?? const [])
        .map((e) => RoutingRule.fromJson((e as Map).cast<String, dynamic>())).toList();
    return Manifest(version: _asInt(m['version'], 0), rollout: _asInt(m['rollout'], 100),
        nodes: nodes, relays: relays, rules: rules, raw: m);
  }

  // ── 4. поддержка ────────────────────────────────────────────────────────────
  Future<List<Map<String, dynamic>>> supportThread() async {
    final r = await _hget(Uri.parse('$baseUrl/v1/support/thread'), headers: _auth);
    _need(r, 200);
    return (jsonDecode(r.body)['messages'] as List).cast<Map<String, dynamic>>();
  }

  Future<void> supportSend(String text, {Map<String, dynamic>? diag}) async {
    final r = await _hpost(Uri.parse('$baseUrl/v1/support/message'),
        headers: _auth, body: jsonEncode({'text': text, if (diag != null) 'diag': diag}));
    _need(r, 200);
  }

  // ── вспомогательное ──────────────────────────────────────────────────────────
  Future<List<int>> _pubKey() async {
    if (_trustedPubKeyB64 != null) return base64.decode(_trustedPubKeyB64!);
    // ТОЛЬКО для разработки: в проде ключ вшивается в приложение, а не качается.
    final r = await _hget(Uri.parse('$baseUrl/v1/pubkey'));
    _need(r, 200);
    _trustedPubKeyB64 = jsonDecode(r.body)['ed25519'] as String;
    return base64.decode(_trustedPubKeyB64!);
  }

  Future<bool> _verify(Map<String, dynamic> manifest, List<int> pubBytes) async {
    final m = Map<String, dynamic>.from(manifest);
    final sig = (m.remove('sig') as String?) ?? '';
    if (!sig.startsWith('ed25519:')) return false;
    final sigBytes = base64.decode(sig.substring('ed25519:'.length));
    final msg = utf8.encode(_canonical(m));
    final algo = Ed25519();
    final pk = SimplePublicKey(pubBytes, type: KeyPairType.ed25519);
    return algo.verify(msg, signature: Signature(sigBytes, publicKey: pk));
  }

  /// Каноническая JSON-сериализация: ключи отсортированы, без пробелов, UTF-8.
  /// Должна совпадать с backend/manifest.py:canonical().
  static String _canonical(Object? v) {
    if (v is Map) {
      final keys = v.keys.map((e) => e.toString()).toList()..sort();
      return '{${keys.map((k) => '${jsonEncode(k)}:${_canonical(v[k])}').join(',')}}';
    } else if (v is List) {
      return '[${v.map(_canonical).join(',')}]';
    }
    return jsonEncode(v);
  }

  void _need(http.Response r, int code) { if (r.statusCode != code) throw ApiError(r.statusCode, _detail(r)); }
  String _detail(http.Response r) { try { return jsonDecode(r.body)['detail']?.toString() ?? r.body; } catch (_) { return r.body; } }
}

class ApiError implements Exception {
  final int status; final String message;
  ApiError(this.status, this.message);
  @override String toString() => 'ApiError($status): $message';
}
