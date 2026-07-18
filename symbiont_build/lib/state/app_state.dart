// lib/state/app_state.dart
// Единое состояние приложения. РЕАЛЬНАЯ интеграция:
//   • узлы и правила — ТОЛЬКО из подписанного манифеста бэкенда (ApiClient);
//     нет серверов → список пуст, показываем прямое подключение пользователя;
//   • пинг — РЕАЛЬНЫЙ замер (TCP-connect): directPingMs у прямого подключения,
//     и по каждому узлу, если задан host;
//   • аккаунт/метка/ключи/поддержка — реальные HTTP-запросы;
//   • всё, что должно переживать перезапуск, пишется на диск (Store);
//   • туннель — через SymbiontEngine (Mock сейчас; SingboxEngine на нативном шаге).
//
// Презагруженных/выдуманных данных нет: пинг измеряется, узлы приходят с сервера.
import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import '../engine/engine.dart';
import '../models/models.dart';
import '../i18n/strings.dart';
import '../api/api_client.dart';
import '../net/probe.dart';
import '../net/curtain.dart';
import '../log.dart';
import '../store.dart';
import '../style_engine.dart';

/// Открытый ключ бэкенда (Ed25519, base64 raw-32). В реальном релизе ВШИВАЕТСЯ сюда,
/// чтобы клиент не доверял ключу «по сети». Пусто → для разработки клиент возьмёт
/// ключ из GET /v1/pubkey (только dev).
///
/// ПРОД: пекётся в релиз-сборку флагом
///   --dart-define=SYMBIONT_PUBKEY=<ed25519-base64>
/// (его печатает bootstrap_vps.sh после поднятия бэкенда). Если задан — клиент
/// доверяет ТОЛЬКО этому ключу и не качает его по сети. Дефолт пуст → dev-режим.
const kTrustedPubKey = String.fromEnvironment('SYMBIONT_PUBKEY', defaultValue: '');

/// База бэкенда по умолчанию. Меняется в онбординге/настройках и пишется в Store.
/// Для Windows-десктопа и веба localhost; для Android-эмулятора — 10.0.2.2.
///
/// ПРОД: пекётся в релиз-сборку флагом
///   --dart-define=SYMBIONT_BASE_URL=http://<IP_VPS>:8000
/// тогда приложение из коробки смотрит на твой VPS (онбординг можно пропустить).
const kDefaultBaseUrl = String.fromEnvironment('SYMBIONT_BASE_URL',
    defaultValue: 'http://127.0.0.1:8000');

enum AppScreen { home, nodes, routes, scan, account, support }

class AppState extends ChangeNotifier {
  final SymbiontEngine engine;
  final bool nativeTunnel; // true → активен реальный движок sing-box (десктоп с бинарником)
  ApiClient api;

  // ── навигация / язык / онбординг ──
  String lang = 'ru';
  AppScreen screen = AppScreen.home;
  String? overlay; // null|settings|glossary
  bool onboarded = false;
  bool backendOnline = false;
  bool busy = false;
  String? lastError;

  // ── соединение ──
  ConnStatus status = ConnStatus.off;
  List<NodeInfo> nodes = [];
  int nodeIndex = 0;
  CoverageMode mode = CoverageMode.smart;
  Protection protection = const Protection();
  // РЕАЛЬНЫЙ пинг прямого подключения пользователя (TCP до публичного якоря).
  int? directPingMs;
  double? directLossPct;
  final List<double> pingHistory = []; // последние замеры пинга для мини-графика
  bool measuringDirect = false;
  Timer? _pingTimer;
  int manifestVersion = 0;
  int manifestRollout = 100;   // % текущей волны canary (из подписанного манифеста)
  bool manifestInWave = true;  // попал ли ЭТОТ клиент в текущую волну выкатки
  List<NodeInfo> relays = const [];      // «белые» relay-узлы для detour (не в списке UI)
  CurtainVerdict? curtainVerdict;        // последний вердикт пробы «16 КБ занавеса»
  bool preferRelay = false;              // увести трафик через relay (занавес/whitelist)

  // ── анализ ──
  bool scanning = false, scanned = false;
  List<ScanItem> scan = [];

  // ── диагностика сети (как 2ip + проверка блокировок + скорость) ──
  static const diagHosts = ['youtube.com', 'discord.com', 'x.com', 'instagram.com', 'telegram.org'];
  bool diagnosing = false, diagnosed = false;
  Map<String, String> netInfo = {};      // ip, country, isp, as
  int? netPing;
  double? netSpeed;                       // Мбит/с
  Map<String, String> hostStatus = {};    // host -> ok|dns|tcp|tls
  bool tuning = false;
  bool tuneTried = false;
  String? tunedStrategy;
  String tuneProgress = ''; // текущий шаг подбора (для UI)

  // ── МОДУЛЬ: живой мониторинг сети (включается/выключается) ──
  // Тумблер: пользователю не нужен — выключил, и модуль молчит, ядро не трогается.
  bool monitorEnabled = true;          // тумблер модуля
  bool monitorRunning = false;         // идёт ли цикл
  Timer? _monitorTimer;
  int? monPingMs;                      // последний пинг мониторинга
  double? monLossPct;                  // потери, %
  String monHealth = 'unknown';        // 'good' | 'ok' | 'bad' | 'unknown'
  DateTime? _connectedSince;           // когда туннель поднялся (для uptime)

  /// Джиттер — средняя разница между соседними замерами пинга (мс).
  double get monJitter {
    if (pingHistory.length < 2) return 0;
    double sum = 0; int n = 0;
    for (var i = 1; i < pingHistory.length; i++) {
      sum += (pingHistory[i] - pingHistory[i - 1]).abs(); n++;
    }
    return n == 0 ? 0 : sum / n;
  }

  /// Время с момента подключения (для панели Connection).
  Duration get connectedFor =>
      _connectedSince == null ? Duration.zero : DateTime.now().difference(_connectedSince!);
  // ── МОДУЛЬ: спидтест по кнопке ──
  bool speedtestRunning = false;
  double? speedtestMbps;

  // ── аккаунт ──
  String label = '';
  Plan plan = Plan.free;
  DateTime? paidUntil;

  // ── живые данные аккаунта из бэкенда (рендерятся в сервисе) ──
  Map<String, dynamic>? economy;        // тарифы/цены/награды/репутация (серверная экономика)
  Map<String, dynamic>? billing;        // {tier, mode, days_left, active_minutes_left, source}
  Map<String, dynamic>? referral;       // {invite_code, reputation{...}, invited, converted}
  List<Map<String, dynamic>> devicesList = const [];
  String? recoveryCode;                 // показываем один раз после регистрации

  /// Подтянуть серверную экономику (можно без авторизации) — для экранов тарифов.
  Future<void> loadEconomy() async {
    try {
      economy = await api.economy();
      Log.w('billing', 'экономика загружена: тарифов=${(economy?['tiers'] as List?)?.length}');
      notifyListeners();
    } catch (e, st) { Log.e('billing', 'экономика не загружена', e, st); }
  }

  /// Подтянуть статус подписки/баланса + рефералку + устройства (после входа).
  Future<void> refreshAccount() async {
    if (api.token == null) { Log.w('billing', 'refreshAccount: нет токена — пропуск'); return; }
    try {
      billing = await api.billingStatus();
      final sub = billing?['subscription'] as Map<String, dynamic>?;
      Log.w('billing', 'подписка: tier=${sub?['tier']} mode=${sub?['mode']} '
          'дней=${sub?['days_left']} минут=${sub?['active_minutes_left']}');
    } catch (e, st) { Log.e('billing', 'статус подписки не получен', e, st); }
    try {
      referral = await api.referralInfo();
      Log.w('billing', 'рефералы: код=${referral?['invite_code']} '
          'уровень=${(referral?['reputation'] as Map?)?['level']} приглашено=${referral?['invited']}');
    } catch (e, st) { Log.e('billing', 'рефералка не получена', e, st); }
    try {
      devicesList = await api.listDevices();
      Log.w('billing', 'устройств: ${devicesList.length}');
    } catch (e, st) { Log.e('billing', 'список устройств не получен', e, st); }
    notifyListeners();
  }

  /// Покупка тарифа/пакета выбранным методом, затем обновление статуса.
  Future<Map<String, dynamic>?> buy(String product, String method, {String region = 'ru'}) async {
    try {
      Log.w('billing', 'покупка: $product через $method ($region)');
      final res = await api.purchase(product, method, region: region);
      Log.w('billing', 'оплата: ${res['status']}');
      await refreshAccount();
      return res;
    } catch (e, st) { Log.e('billing', 'покупка не удалась', e, st); return null; }
  }

  Future<void> revokeDevice(String id) async {    try { await api.revokeDevice(id); Log.w('billing', 'устройство отозвано: $id'); await refreshAccount(); }
    catch (e, st) { Log.e('billing', 'отзыв устройства', e, st); }
  }

  Future<void> promoteDevice(String id) async {
    try { await api.promoteDevice(id); Log.w('billing', 'устройство повышено: $id'); await refreshAccount(); }
    catch (e, st) { Log.e('billing', 'повышение устройства', e, st); }
  }

  /// Пользователь подтвердил, что сохранил recovery-код — убираем его из показа.
  void ackRecovery() { recoveryCode = null; notifyListeners(); }

  // ── колесо фортуны ──
  Map<String, dynamic>? wheel;       // {segments, available, today_index, last_prize}

  Future<void> loadWheel() async {
    if (api.token == null) return;
    try { wheel = await api.wheelInfo(); notifyListeners(); }
    catch (e, st) { Log.e('wheel', 'инфо колеса не получено', e, st); }
  }

  Future<Map<String, dynamic>?> spinWheel() async {
    try {
      final r = await api.wheelSpin();
      Log.w('wheel', 'спин: индекс=${r['index']} приз=${r['prize']}');
      await loadWheel();
      await refreshAccount();
      return r;
    } catch (e, st) { Log.e('wheel', 'спин не удался', e, st); return null; }
  }

  // ── поддержка ──
  List<SupportMessage> chat = [];

  AppState(this.engine, {ApiClient? client, this.nativeTunnel = false})
      : api = client ?? ApiClient(Store.baseUrl ?? kDefaultBaseUrl, trustedPubKeyB64: kTrustedPubKey.isEmpty ? null : kTrustedPubKey) {
    engine.status.listen((s) {
      final wasOn = status.phase == ConnPhase.on;
      if (s.phase != status.phase) {
        Log.w('status', 'фаза: ${status.phase.name} → ${s.phase.name}'
            '${s.node != null ? ' (узел ${s.node!.host ?? s.node!.id})' : ''}'
            '${s.error != null ? ' ошибка=${s.error}' : ''}');
      }
      status = s;
      if (s.mode != mode && s.phase == ConnPhase.on) mode = s.mode;
      // фиксируем момент подключения для счётчика времени
      if (s.phase == ConnPhase.on && !wasOn) _connectedSince = DateTime.now();
      if (s.phase != ConnPhase.on) _connectedSince = null;
      // модуль мониторинга работает всегда, когда включён (и при активном, и при неактивном).
      // При подключении просто гарантируем, что он запущен; при отключении — НЕ останавливаем.
      if (s.phase == ConnPhase.on && !wasOn) startMonitor();
      // карта трафика — только при активном туннеле (Clash API доступен лишь при подключении)
      if (s.phase == ConnPhase.on && !wasOn && trafficEnabled) startTraffic();
      if (s.phase != ConnPhase.on && wasOn) stopTraffic();
      notifyListeners();
    });
    _boot();
  }

  String tr(String key) => T.of(lang, key);

  // ── загрузка состояния с диска + первичная синхронизация ──
  Future<void> _boot() async {
    lang = Store.lang;
    label = Store.label;
    plan = planFromName(Store.plan);
    paidUntil = Store.paidUntil != null ? DateTime.tryParse(Store.paidUntil!) : null;
    mode = CoverageMode.values.byName(Store.mode);
    onboarded = Store.onboarded && Store.token != null;
    final pr = Store.protection;
    if (pr.isNotEmpty) {
      protection = Protection(
        ads: pr['ads'] ?? false, trackers: pr['trackers'] ?? false,
        phishing: pr['phishing'] ?? false, killSwitch: pr['killSwitch'] ?? false,
        dns: pr['dns'] ?? 'DoH',
      );
    }
    api.token = Store.token;
    protoChoice = Store.protoChoice;
    errorDisplay = Store.errorDisplay;
    _loadRules();
    engine.setProtoPreference(protoChoice);
    notifyListeners();

    // реальный пинг прямого подключения — сразу и затем периодически
    measureDirect();
    _pingTimer ??= Timer.periodic(const Duration(seconds: 12), (_) => measureDirect());

    // мониторинг включён по умолчанию → запускаем сразу (работает и при неактивном VPN)
    if (monitorEnabled) startMonitor();

    if (onboarded) {
      await refreshFromBackend();
    }
  }

  @override
  void dispose() { _pingTimer?.cancel(); _monitorTimer?.cancel(); _trafficTimer?.cancel(); super.dispose(); }

  /// Реальный замер пинга прямого подключения (и грубой доли потерь).
  Future<void> measureDirect() async {
    if (measuringDirect) return;
    measuringDirect = true;
    notifyListeners();
    try {
      directPingMs = await Probe.directPing();
      if (directPingMs != null) {
        pingHistory.add(directPingMs!.toDouble());
        if (pingHistory.length > 40) pingHistory.removeAt(0);
        // Реальные потери прямого канала по стабильному якорю. Без этого поле
        // directLossPct никогда не заполнялось и метрика «потери» на Home всегда
        // показывала «—». (В вебе Probe.lossPct — заглушка null → так и остаётся «—».)
        directLossPct = await Probe.lossPct('1.1.1.1', 443);
      } else {
        directLossPct = null;
      }
    } catch (_) {
      directPingMs = null;
      directLossPct = null;
    } finally {
      measuringDirect = false;
      notifyListeners();
    }
  }

  /// Реальный замер пинга по узлам, у которых задан host (из манифеста).
  /// Узлы без host остаются с pingMs = null → в UI «—».
  Future<void> _measureNodePings() async {
    for (var i = 0; i < nodes.length; i++) {
      final n = nodes[i];
      if (n.host == null) continue;
      final p = await Probe.median(n.host!, n.port, samples: 3);
      if (p != null && i < nodes.length && nodes[i].id == n.id) {
        nodes[i] = nodes[i].copyWith(pingMs: p);
        notifyListeners();
      }
    }
  }

  // ── ОНБОРДИНГ: вход/регистрация ──
  /// Создаёт анонимный аккаунт на бэкенде (или офлайн-аккаунт, если бэкенд недоступен).
  Future<bool> register(String chosenLabel, {String? baseUrl}) async {
    _setBusy(true);
    try {
      if (baseUrl != null && baseUrl.trim().isNotEmpty) {
        Store.baseUrl = baseUrl.trim();
        api = ApiClient(baseUrl.trim(), trustedPubKeyB64: kTrustedPubKey.isEmpty ? null : kTrustedPubKey);
      }
      backendOnline = await api.ping();
      label = chosenLabel.trim().isEmpty ? 'guest' : chosenLabel.trim();

      if (backendOnline) {
        // Регистрация через крипто-личность: метка = ник-алиас, плюс устройство.
        // Сохраняем recovery-код для одноразового показа. Если ник занят — суффикс.
        final platform = kIsWeb ? 'web' : defaultTargetPlatform.name;
        final device = {'name': label, 'platform': platform};
        final aliasVal = (label == 'guest')
            ? 'sym-${DateTime.now().millisecondsSinceEpoch.toRadixString(36)}'
            : label;
        // Одна попытка регистрации: берём СВЕЖИЙ PoW-челлендж и решаем его, если
        // сервер его требует (bits>0). Свежий — потому что челлендж одноразовый и
        // сервер гасит его даже при последующем отказе 409 (ник занят).
        Future<Map<String, dynamic>> registerOnce(String av) async {
          String? powChallenge, powNonce;
          try {
            final pow = await api.getPow();
            final bits = (pow['bits'] as num?)?.toInt() ?? 0;
            if (bits > 0) {
              powChallenge = pow['challenge'] as String?;
              powNonce = await api.solvePow(powChallenge!, bits);
            }
          } catch (_) {
            // PoW-эндпоинт недоступен — пробуем без него (сервер сам решит, нужен ли).
          }
          return api.register(aliases: [{'value': av, 'kind': 'nick'}], device: device,
              powChallenge: powChallenge, powNonce: powNonce);
        }
        Map<String, dynamic> res;
        try {
          res = await registerOnce(aliasVal);
        } on ApiError catch (e) {
          if (e.status == 409) {
            // ник занят — добавим короткий суффикс и повторим (со свежим PoW)
            final alt = '$aliasVal-${DateTime.now().millisecondsSinceEpoch.toRadixString(36).substring(6)}';
            res = await registerOnce(alt);
          } else {
            rethrow;
          }
        }
        Store.token = api.token!;                 // токен выставлен внутри register()
        recoveryCode = res['recovery_code'] as String?;
        Log.w('account', 'аккаунт создан: ${res['account_id']} (recovery выдан, устройство ${res['device_id']})');
      } else {
        // офлайн-аккаунт: локальный токен, всё работает локально
        Store.token = 'offline-${DateTime.now().millisecondsSinceEpoch}';
        api.token = Store.token;
      }
      Store.label = label;
      Store.onboarded = true;
      onboarded = true;
      lastError = null;
      await refreshFromBackend();
      return true;
    } catch (e) {
      lastError = '$e';
      return false;
    } finally {
      _setBusy(false);
    }
  }

  /// Вход по существующему токену (например, перенос аккаунта).
  Future<bool> loginWithToken(String token, {String? baseUrl}) async {
    _setBusy(true);
    try {
      if (baseUrl != null && baseUrl.trim().isNotEmpty) {
        Store.baseUrl = baseUrl.trim();
        api = ApiClient(baseUrl.trim(), trustedPubKeyB64: kTrustedPubKey.isEmpty ? null : kTrustedPubKey);
      }
      api.token = token.trim();
      Store.token = token.trim();
      backendOnline = await api.ping();
      Store.onboarded = true;
      onboarded = true;
      await refreshFromBackend();
      return true;
    } catch (e) {
      lastError = '$e';
      return false;
    } finally {
      _setBusy(false);
    }
  }

  /// Восстановление аккаунта по recovery-коду (переустановка/новое устройство).
  /// Код выдаётся один раз при регистрации; сервер по нему выдаёт свежий токен.
  Future<bool> loginWithRecovery(String recoveryCode, {String? baseUrl}) async {
    _setBusy(true);
    try {
      if (baseUrl != null && baseUrl.trim().isNotEmpty) {
        Store.baseUrl = baseUrl.trim();
        api = ApiClient(baseUrl.trim(), trustedPubKeyB64: kTrustedPubKey.isEmpty ? null : kTrustedPubKey);
      }
      final platform = kIsWeb ? 'web' : defaultTargetPlatform.name;
      final res = await api.recover(recoveryCode.trim(),
          device: {'name': label.isEmpty ? 'guest' : label, 'platform': platform});
      Store.token = api.token!;                 // токен выставлен внутри recover()
      Store.onboarded = true;
      onboarded = true;
      lastError = null;
      Log.w('account', 'вход по recovery: ${res['account_id']} (устройство ${res['device_id']})');
      await refreshFromBackend();
      return true;
    } catch (e) {
      lastError = '$e';
      return false;
    } finally {
      _setBusy(false);
    }
  }

  Future<void> logout() async {
    try { await engine.disconnect(); } catch (_) {}
    await Store.clear();
    onboarded = false;
    label = ''; plan = Plan.free; paidUntil = null;
    api.token = null;
    nodes = []; chat = [];
    notifyListeners();
  }

  // ── синхронизация с бэкендом (манифест + поддержка) ──
  // Стабильное «ведро» 0..99 по токену (FNV-1a 32-bit). Решает, попал ли клиент в
  // текущую волну canary: bucket < rollout% → да. Детерминировано на установку
  // (один и тот же токен всегда даёт то же ведро), поэтому при расширении волны
  // 1%→5%→25% набор уже-получивших клиентов только растёт, а не перетасовывается.
  int _canaryBucket(String token) {
    var h = 0x811c9dc5;
    for (final b in utf8.encode(token)) {
      h = (h ^ b) & 0xffffffff;
      h = (h * 0x01000193) & 0xffffffff;
    }
    return h % 100;
  }

  /// Проба «16 КБ занавеса» на текущем пути. При обнаружении (curtain/slow)
  /// АВТОМАТИЧЕСКИ уводим трафик через relay (ставим лучший relay в движок).
  /// Безопасно: relay нет → просто помечаем preferRelay (UI может подсказать).
  Future<void> detectCurtain() async {
    try {
      final v = await Probe.probeCurtain();
      curtainVerdict = v;
      preferRelay = curtainNeedsRelay(v);
      if (preferRelay && relays.isNotEmpty) {
        engine.setRelay(relays.first);
        Log.w('curtain', 'занавес ($v) → авто-detour через relay ${relays.first.id}');
      } else if (!preferRelay) {
        engine.setRelay(null); // путь чистый → прямой выход
      }
      notifyListeners();
    } catch (_) {/* проба не критична — молча */}
  }

  Future<void> refreshFromBackend() async {
    backendOnline = await api.ping();
    // 1) узлы и правила — ТОЛЬКО из подписанного манифеста. Нет бэкенда/узлов →
    // список пуст: UI покажет прямое подключение пользователя (без выдуманных узлов).
    if (backendOnline) {
      try {
        final m = await api.fetchManifest(since: manifestVersion);
        if (m != null) {
          final applied = Store.appliedManifestVersion;
          final bucket = _canaryBucket(Store.token ?? api.token ?? 'anon');
          manifestRollout = m.rollout;
          if (m.version > applied && bucket < m.rollout) {
            // версия НОВЕЕ применённой И клиент попал в волну canary → применяем.
            Store.appliedManifestVersion = m.version;
            nodes = _applyFavorites(m.nodes);
            relays = m.relays;
            manifestInWave = true;
          } else {
            // либо не новее (монотонность — откат на старый манифест отклоняем),
            // либо мы пока ВНЕ волны: остаёмся на текущем манифесте. since держим
            // равным applied, чтобы перепроверять, когда волна расширится.
            manifestInWave = m.version <= applied;
          }
          // since для следующего запроса = что РЕАЛЬНО применили (не версия-кандидат)
          manifestVersion = Store.appliedManifestVersion;
        }
      } catch (e) {
        lastError = 'manifest: $e';
      }
    }
    // 2) поддержка
    if (backendOnline) {
      try {
        final msgs = await api.supportThread();
        chat = msgs.map((j) => SupportMessage(from: j['from'], text: j['text'], at: DateTime.tryParse(j['at'] ?? '') ?? DateTime.now())).toList();
      } catch (_) {}
    }
    if (chat.isEmpty) {
      chat = [SupportMessage(from: 'support', text: tr('support.greeting'), at: DateTime.now())];
    }
    // восстановить ранее выбранный узел (или взять первый), чтобы «Подключиться» работало сразу
    if (nodes.isNotEmpty) {
      final saved = Store.lastNodeId;
      final idx = saved == null ? -1 : nodes.indexWhere((n) => n.id == saved);
      nodeIndex = idx >= 0 ? idx : 0;
      engine.setActiveNode(nodes[nodeIndex]);
    } else {
      nodeIndex = 0;
    }
    notifyListeners();
    // реальные замеры: прямое подключение + пинг узлов (у кого есть host)
    measureDirect();
    _measureNodePings();
  }

  List<NodeInfo> _applyFavorites(List<NodeInfo> src) {
    final fav = Store.favorites;
    return [for (final n in src) n.copyWith(favorite: fav.contains(n.id))];
  }

  // ── навигация / язык ──
  void setLang(String l) { lang = l; Store.lang = l; notifyListeners(); }
  void go(AppScreen s) { Log.w('nav', 'экран → ${s.name}'); overlay = null; screen = s; notifyListeners(); }
  void openOverlay(String o) { Log.w('nav', 'оверлей → $o'); overlay = o; notifyListeners(); }
  void closeOverlay() { Log.w('nav', 'оверлей закрыт'); overlay = null; notifyListeners(); }

  // ── защита ──
  NodeInfo get node => nodes.isNotEmpty
      ? nodes[nodeIndex.clamp(0, nodes.length - 1)]
      : const NodeInfo(id: '-', country: '—', code: 'NL', loadPct: 0);

  /// Перезапустить приложение от имени администратора (для прозрачного обхода).
  Future<void> requestAdmin() => engine.requestAdmin();

  DateTime _lastToggle = DateTime.fromMillisecondsSinceEpoch(0);
  bool _toggling = false;
  Future<void> toggleConnect() async {
    // защита от лавины: не чаще раза в 1.2с и без повторного входа
    final now = DateTime.now();
    if (_toggling) { Log.w('app', 'toggleConnect: уже выполняется — пропуск'); return; }
    if (now.difference(_lastToggle).inMilliseconds < 1200) { Log.w('app', 'toggleConnect: слишком часто — пропуск'); return; }
    _lastToggle = now; _toggling = true;
    Log.w('app', 'toggleConnect: текущая фаза=${status.phase.name}, узлов=${nodes.length}, движок=${nativeTunnel ? 'нативный' : 'mock'}');
    try {
      if (status.phase == ConnPhase.connecting) return;
      if (nodes.isEmpty && !nativeTunnel) { Log.w('app', 'toggleConnect: нет узлов и нет движка — нечего подключать'); return; }
      if (status.phase == ConnPhase.on) {
        Log.w('app', 'toggleConnect: отключение');
        await engine.disconnect();
      } else {
        final id = nodes.isNotEmpty ? node.id : 'local';
        engine.setActiveNode(nodes.isNotEmpty ? node : null); // узел с секретами transport (для VPN)
        Log.w('app', 'toggleConnect: подключение node=$id mode=${mode.name} hasTransport=${nodes.isNotEmpty && node.transport != null}');
        await engine.connect(nodeId: id, mode: mode == CoverageMode.off ? CoverageMode.smart : mode);
      }
    } finally {
      _toggling = false;
    }
  }

  Future<void> selectNode(int i) async {
    if (i < 0 || i >= nodes.length) return;
    nodeIndex = i;
    Store.lastNodeId = nodes[i].id;        // запоминаем выбор между запусками
    engine.setActiveNode(nodes[i]);         // узел с секретами готов к подключению
    Log.w('app', 'выбран узел ${nodes[i].id}');
    notifyListeners();
    if (status.phase == ConnPhase.on || status.phase == ConnPhase.connecting) {
      await engine.connect(nodeId: nodes[i].id, mode: mode);
    }
  }

  // Лог любого нажатия кнопки (ты просил видеть все кнопки в логах).
  void logTap(String what) => Log.w('ui', 'нажато: $what');

  Future<void> fastest() async {
    Log.w('ui', 'нажато: Самый быстрый');
    if (nodes.isEmpty) { Log.w('ui', 'Самый быстрый: узлов нет'); return; }
    // выбор по реальному измеренному пингу; узлы без замера — в конец
    final measured = [for (var i = 0; i < nodes.length; i++) (i, nodes[i].pingMs)]
        .where((e) => e.$2 != null).toList()
      ..sort((a, b) => a.$2!.compareTo(b.$2!));
    nodeIndex = measured.isNotEmpty ? measured.first.$1 : 0;
    Log.w('ui', 'Самый быстрый → ${nodes[nodeIndex].id} (${nodes[nodeIndex].pingMs ?? '—'} ms)');
    notifyListeners();
    engine.setActiveNode(nodes[nodeIndex]);
    await engine.connect(nodeId: nodes[nodeIndex].id, mode: mode);
  }

  // Случайный узел (с логом). При одном узле выбор очевиден — но это честно логируется.
  Future<void> pickRandom() async {
    Log.w('ui', 'нажато: Случайный');
    if (nodes.isEmpty) { Log.w('ui', 'Случайный: узлов нет'); return; }
    final i = DateTime.now().microsecondsSinceEpoch % nodes.length;
    Log.w('ui', 'Случайный → ${nodes[i].id}');
    await selectNode(i);
  }

  Future<void> setMode(CoverageMode m) async {
    mode = m; Store.mode = m.name;
    Log.w('ui', 'режим охвата: ${m.name}');
    await engine.setCoverage(m);
    notifyListeners();
  }

  void toggleFav(int i) {
    final n = nodes[i];
    final nv = !n.favorite;
    nodes[i] = n.copyWith(favorite: nv);
    final fav = Store.favorites;
    if (nv) { fav.add(n.id); } else { fav.remove(n.id); }
    Store.favorites = fav;
    notifyListeners();
  }

  // ── анализ ──
  Future<void> runScan() async {
    scanning = true; scanned = false; notifyListeners();
    scan = await engine.runAnalysis();
    scanning = false; scanned = true; notifyListeners();
  }

  void setActionFor(int i, RouteAction a) { scan[i].override = a; notifyListeners(); }

  Future<void> acceptScan() async {
    final rules = scan.map((s) => RoutingRule(kind: MatchKind.processId, match: s.id, action: s.override)).toList();
    await engine.applyRules(rules);
    Store.rules = rules.map((r) => r.toJson()).toList();
    notifyListeners();
  }

  // ── диагностика сети ──
  List<String> get blockedHosts => hostStatus.entries.where((e) => e.value != 'ok').map((e) => e.key).toList();
  bool get dpiDetected => hostStatus.values.any((v) => v == 'tls');

  // ── Pulse-мониторинг региона (п.62): сводный «пульс» доступности ключевых сервисов ──
  int get pulseReachable => hostStatus.values.where((v) => v == 'ok').length;
  int get pulseTotal => hostStatus.length;
  // 'good' (всё ок) | 'partial' (часть недоступна) | 'bad' (большинство недоступно) | 'unknown'
  String get pulseLevel {
    if (pulseTotal == 0) return 'unknown';
    final ratio = pulseReachable / pulseTotal;
    if (ratio >= 0.99) return 'good';
    if (ratio >= 0.5) return 'partial';
    return 'bad';
  }

  /// Реальный анализ: публичный IP/провайдер, пинг, скорость, и проверка блокировок
  /// по слоям (DNS/TCP/TLS) для списка популярных сайтов.
  Future<void> runDiagnostics() async {
    if (diagnosing) return;
    Log.w('diag', '═══ старт анализа сети ═══');
    diagnosing = true; diagnosed = false;
    netInfo = {}; netPing = null; netSpeed = null; hostStatus = {};
    tunedStrategy = null; tuneTried = false;
    notifyListeners();
    // 1) публичная инфа — с защитой от зависания
    try {
      netInfo = await Probe.publicInfo().timeout(const Duration(seconds: 9), onTimeout: () => <String, String>{});
    } catch (_) { netInfo = {}; }
    notifyListeners();
    // 2) пинг
    try {
      netPing = await Probe.directPing().timeout(const Duration(seconds: 7), onTimeout: () => null);
    } catch (_) { netPing = null; }
    notifyListeners();
    // 3) хосты — по одному, таблица заполняется вживую
    final hs = <String, String>{};
    for (final h in diagHosts) {
      String st;
      try {
        st = await Probe.checkHost(h).timeout(const Duration(seconds: 14), onTimeout: () => 'tcp');
      } catch (_) { st = 'tcp'; }
      hs[h] = st; hostStatus = Map.of(hs); notifyListeners();
    }
    diagnosing = false; diagnosed = true; notifyListeners(); // результат готов (скорость догрузится)
    Log.w('diag', 'IP=${netInfo['ip']} ISP=${netInfo['isp']} AS=${netInfo['as']} ping=$netPing статусы=$hostStatus');
    // 4) скорость — отдельно, не блокирует показ результата
    try {
      netSpeed = await Probe.downloadMbps().timeout(const Duration(seconds: 16), onTimeout: () => null);
    } catch (_) { netSpeed = null; }
    notifyListeners();
    // 5) проба «16 КБ занавеса» ТСПУ — если путь душится, авто-увод на relay.
    await detectCurtain();
  }

  // ── МОДУЛЬ: геймбустер (раздел 9 ТЗ) ──
  // Честно: на одном узле «ускорение» = выбор узла с наименьшим пингом и режим,
  // не нагружающий игровой трафик. Реальная разница «до/после» — с пулом узлов.
  bool gameBoosting = false;
  int? gamePingBefore;   // пинг до оптимизации (текущий)
  int? gamePingAfter;    // пинг лучшего узла
  String? gameBestNodeId;
  Map<String, int> gameNodePings = {}; // id узла → пинг

  Future<void> runGameBoost() async {
    if (gameBoosting) return;
    gameBoosting = true;
    gamePingBefore = directPingMs ?? monPingMs;
    gameNodePings = {};
    Log.w('boost', 'геймбустер: старт замера узлов (${nodes.length})');
    notifyListeners();
    try {
      for (final n in nodes) {
        if (n.host == null) continue;
        final p = await Probe.tcpPing(n.host!, n.port).timeout(const Duration(seconds: 4), onTimeout: () => null);
        if (p != null) { gameNodePings[n.id] = p; }
        notifyListeners(); // прогресс по мере замера
      }
      if (gameNodePings.isNotEmpty) {
        final best = gameNodePings.entries.reduce((a, b) => a.value <= b.value ? a : b);
        gameBestNodeId = best.key;
        gamePingAfter = best.value;
        Log.w('boost', 'лучший узел=$gameBestNodeId пинг=${best.value}мс (было ${gamePingBefore ?? "?"}мс)');
      } else {
        Log.w('boost', 'геймбустер: не удалось измерить узлы (нет host/пул пуст)');
      }
    } catch (e) {
      Log.w('boost', 'геймбустер ошибка (изолировано): $e');
    }
    gameBoosting = false;
    notifyListeners();
  }

  // применить лучший узел (переключиться на него)
  Future<void> applyGameBoost() async {
    if (gameBestNodeId == null) return;
    final idx = nodes.indexWhere((n) => n.id == gameBestNodeId);
    if (idx < 0) return;
    nodeIndex = idx;
    Store.lastNodeId = gameBestNodeId!;
    engine.setActiveNode(nodes[idx]);            // узел с секретами transport готов
    Log.w('boost', 'переключение на лучший узел: $gameBestNodeId');
    notifyListeners();
    // Переподключаемся на лучший узел напрямую (как selectNode/fastest). Двойной
    // toggleConnect тут не работал: второй вызов попадал в throttle 1.2с, и туннель
    // оставался выключенным.
    if (status.phase == ConnPhase.on || status.phase == ConnPhase.connecting) {
      await engine.connect(nodeId: gameBestNodeId!, mode: mode);
    }
  }

  // ── МОДУЛЬ: пользовательские правила маршрутизации (per-site/per-app) ──
  List<RoutingRule> userRules = [];

  // ── скан установленных приложений (для маршрутизации) ──
  List<InstalledApp> scannedApps = [];
  bool scanningApps = false;

  Future<void> scanApps() async {
    if (scanningApps) return;
    scanningApps = true; notifyListeners();
    Log.w('scan', 'старт скана приложений');
    try {
      scannedApps = await engine.scanApps().timeout(const Duration(seconds: 40), onTimeout: () { Log.w('scan', 'таймаут скана (40с)'); return const []; });
      Log.w('scan', 'скан завершён: найдено ${scannedApps.length} приложений');
    } catch (e, st) {
      scannedApps = const [];
      Log.e('scan', 'скан не удался (изолировано)', e, st);
    }
    scanningApps = false; notifyListeners();
  }

  void _loadRules() {
    try {
      userRules = Store.rules.map((j) => RoutingRule.fromJson(j)).toList();
      engine.applyRules(userRules); // передать движку, чтобы первое подключение учло правила
    } catch (e) { userRules = []; Log.w('rules', 'загрузка правил не удалась: $e'); }
  }

  Future<void> _saveAndApplyRules() async {
    Store.rules = userRules.map((r) => r.toJson()).toList();
    try { await engine.applyRules(userRules); } catch (e) { Log.w('rules', 'применение правил: $e'); }
    notifyListeners();
  }

  Future<void> addRule(MatchKind kind, String match, RouteAction action, {String? nodeId}) async {
    final m = match.trim();
    if (m.isEmpty) return;
    userRules.add(RoutingRule(kind: kind, match: m, action: action, nodeId: nodeId));
    Log.w('rules', 'добавлено правило: $m → ${action.name}');
    await _saveAndApplyRules();
  }

  Future<void> removeRule(int i) async {
    if (i < 0 || i >= userRules.length) return;
    final r = userRules.removeAt(i);
    Log.w('rules', 'удалено правило: ${r.match}');
    await _saveAndApplyRules();
  }

  /// Текущее действие для домена (или null, если правила нет) — для каталога.
  RouteAction? routeFor(String domain) {
    for (final r in userRules) {
      if (r.match == domain) return r.action;
    }
    return null;
  }

  /// Задать/обновить/снять маршрут для домена из каталога одной кнопкой.
  Future<void> setRouteFor(String domain, RouteAction? action, MatchKind kind) async {
    userRules.removeWhere((r) => r.match == domain);
    if (action != null) {
      userRules.add(RoutingRule(kind: kind, match: domain, action: action));
      Log.w('rules', 'маршрут $domain → ${action.name}');
    } else {
      Log.w('rules', 'маршрут $domain → авто (правило снято)');
    }
    await _saveAndApplyRules();
  }

  // ── МОДУЛЬ: выбор стиля оформления (A/B/C/D) ──
  String errorDisplay = 'both'; // both | card | toast | none
  void setErrorDisplay(String v) {
    errorDisplay = v; Store.errorDisplay = v;
    Log.w('ui', 'показ ошибок: $v');
    notifyListeners();
  }

  void setStyle(AppStyle s) {
    Style.set(s);
    Store.style = kStyles[s]!.id;
    Log.w('ui', 'стиль оформления: ${kStyles[s]!.id} (${kStyles[s]!.name})');
    notifyListeners();
  }

  // ── МОДУЛЬ: выбор протокола (авто / конкретный) ──
  // 'auto' — наш умный каскад; иначе — пользователь форсит конкретный протокол первым.
  String protoChoice = 'auto'; // 'auto' | 'reality' | 'hysteria2' | 'ss2022'
  void setProtoChoice(String c) {
    protoChoice = c; Store.protoChoice = c;
    Log.w('proto', 'выбор протокола: $c');
    engine.setProtoPreference(c);
    notifyListeners();
  }

  // ── МОДУЛЬ: карта трафика «что куда идёт» (тумблер) ──
  bool trafficEnabled = true;      // по умолчанию ВКЛ — пользователь сразу видит, что куда идёт
  bool trafficRunning = false;
  Timer? _trafficTimer;
  List<TrafficConn> traffic = const [];

  // ── МОДУЛЬ карты трафика: тумблер + опрос Clash API ──
  void setTrafficEnabled(bool on) {
    trafficEnabled = on;
    Log.w('traffic', 'тумблер: ${on ? "вкл" : "выкл"}');
    if (on) { startTraffic(); } else { stopTraffic(); }
    notifyListeners();
  }

  void startTraffic() {
    if (!trafficEnabled || trafficRunning) return;
    if (status.phase != ConnPhase.on) return; // только при активном туннеле
    trafficRunning = true;
    Log.w('traffic', 'старт опроса карты трафика');
    notifyListeners();
    _trafficTimer?.cancel();
    _trafficTimer = Timer.periodic(const Duration(seconds: 2), (_) => _trafficTick());
    _trafficTick();
  }

  void stopTraffic() {
    _trafficTimer?.cancel(); _trafficTimer = null;
    if (trafficRunning) Log.w('traffic', 'стоп опроса карты трафика');
    trafficRunning = false;
    traffic = const [];
    notifyListeners();
  }

  Future<void> _trafficTick() async {
    try {
      traffic = await engine.trafficConnections();
    } catch (e) {
      traffic = const [];
      Log.w('traffic', 'опрос не удался (изолировано): $e');
    }
    notifyListeners();
  }

  // ── МОДУЛЬ мониторинга: тумблер + цикл замеров ──
  void setMonitorEnabled(bool on) {
    monitorEnabled = on;
    Log.w('monitor', 'тумблер: ${on ? "вкл" : "выкл"}');
    if (on) { startMonitor(); } else { stopMonitor(); }
    notifyListeners();
  }

  void startMonitor() {
    if (!monitorEnabled || monitorRunning) return;
    monitorRunning = true;
    Log.w('monitor', 'старт цикла мониторинга');
    notifyListeners();
    _monitorTimer?.cancel();
    _monitorTimer = Timer.periodic(const Duration(seconds: 5), (_) => _monitorTick());
    _monitorTick(); // первый замер сразу
  }

  void stopMonitor() {
    _monitorTimer?.cancel(); _monitorTimer = null;
    if (monitorRunning) Log.w('monitor', 'стоп цикла мониторинга');
    monitorRunning = false;
    notifyListeners();
  }

  Future<void> _monitorTick() async {
    // изоляция падений: любая ошибка замера НЕ роняет приложение и не трогает ядро
    try {
      final p = await Probe.directPing().timeout(const Duration(seconds: 6), onTimeout: () => null);
      monPingMs = p;
      if (p != null) {
        pingHistory.add(p.toDouble());
        if (pingHistory.length > 40) pingHistory.removeAt(0);
      }
      final loss = await Probe.lossPct('1.1.1.1', 443, samples: 4).timeout(const Duration(seconds: 8), onTimeout: () => null);
      monLossPct = loss;
      // простая оценка здоровья сети
      if (p == null || (loss != null && loss >= 50)) {
        monHealth = 'bad';
      } else if (p <= 80 && (loss ?? 0) < 10) {
        monHealth = 'good';
      } else {
        monHealth = 'ok';
      }
    } catch (e) {
      monHealth = 'unknown';
      Log.w('monitor', 'замер не удался (изолировано): $e');
    }
    notifyListeners();
  }

  // ── МОДУЛЬ спидтеста: разовый замер по кнопке ──
  Future<void> runSpeedtest() async {
    if (speedtestRunning) return;
    speedtestRunning = true; speedtestMbps = null;
    Log.w('speedtest', 'старт');
    notifyListeners();
    try {
      speedtestMbps = await Probe.downloadMbps().timeout(const Duration(seconds: 16), onTimeout: () => null);
      Log.w('speedtest', 'результат: ${speedtestMbps?.toStringAsFixed(1) ?? "—"} Mbps');
    } catch (e) {
      speedtestMbps = null;
      Log.w('speedtest', 'ошибка (изолировано): $e');
    }
    speedtestRunning = false;
    notifyListeners();
  }

  /// Авто-подбор рабочей стратегии обхода (эффективен в режиме с админом).
  Future<void> autoTune() async {
    if (tuning) return;
    tuning = true; tuneTried = true; tuneProgress = '';
    notifyListeners();
    Log.w('app', 'autoTune старт, заблокированы: $blockedHosts');
    try {
      tunedStrategy = await engine.autoTuneBypass(blockedHosts, onProgress: (stage) {
        tuneProgress = stage;
        notifyListeners();
      }).timeout(const Duration(seconds: 180), onTimeout: () {
        Log.w('app', 'autoTune: таймаут 180с');
        return null;
      });
    } catch (e) {
      Log.w('app', 'autoTune ошибка: $e');
      tunedStrategy = null;
    }
    tuning = false; tuneProgress = '';
    Log.w('app', 'autoTune результат: ${tunedStrategy ?? "не найдено"}');
    notifyListeners();
    if (tunedStrategy != null) {
      final hs = <String, String>{};
      for (final h in diagHosts) {
        try {
          hs[h] = await Probe.checkHost(h).timeout(const Duration(seconds: 8), onTimeout: () => 'tcp');
        } catch (_) { hs[h] = 'tcp'; }
      }
      hostStatus = hs;
      notifyListeners();
    }
  }

  // ── настройки защиты ──
  Future<void> _pushProtection() async {
    Log.w('protection', 'killSwitch=${protection.killSwitch} ads=${protection.ads} trackers=${protection.trackers} phishing=${protection.phishing} dns=${protection.dns}');
    await engine.setProtection(protection);
    Store.protection = {
      'ads': protection.ads, 'trackers': protection.trackers, 'phishing': protection.phishing,
      'killSwitch': protection.killSwitch, 'dns': protection.dns,
    };
    notifyListeners();
  }
  void toggleAds() { protection = protection.copyWith(ads: !protection.ads); _pushProtection(); }
  void toggleTrackers() { protection = protection.copyWith(trackers: !protection.trackers); _pushProtection(); }
  void togglePhishing() { protection = protection.copyWith(phishing: !protection.phishing); _pushProtection(); }
  void toggleKill() { protection = protection.copyWith(killSwitch: !protection.killSwitch); _pushProtection(); }
  void setDns(String d) { protection = protection.copyWith(dns: d); _pushProtection(); }

  // ── аккаунт ──
  Future<void> setLabel(String v) async {
    final t = v.trim();
    if (t.isEmpty) return;
    label = t; Store.label = t;
    notifyListeners();
    if (backendOnline) { try { await api.setLabel(t); } catch (e) { lastError = '$e'; } }
  }

  /// Активация ключа. На бэкенде — реальная проверка подписи/реестра; офлайн —
  /// только проверка формата (без выдачи Pro, чтобы не врать).
  Future<String?> activateKey(String code) async {
    if (!ActivationKey(code).looksValid) return tr('activate.bad');
    if (!backendOnline) return tr('activate.offline');
    _setBusy(true);
    try {
      final res = await api.redeemKey(code);
      plan = planFromName(res['plan'] ?? 'pro');
      paidUntil = res['paidUntil'] != null ? DateTime.tryParse(res['paidUntil']) : null;
      Store.plan = plan.name;
      Store.paidUntil = paidUntil?.toIso8601String();
      notifyListeners();
      return null; // успех
    } on ApiError catch (e) {
      return _keyError(e.message);
    } catch (e) {
      return '$e';
    } finally {
      _setBusy(false);
    }
  }

  /// Проверка ключа БЕЗ активации (публичный чекер). Read-only, ничего не меняет,
  /// авторизация не нужна. Возвращает результат сервера {status, ok, message,
  /// grants, uses_total, ...} или null, если бэкенд недоступен (проверка онлайн).
  Future<Map<String, dynamic>?> checkKey(String code) async {
    if (!backendOnline) return null;
    try {
      return await api.checkKey(code.trim());
    } catch (e) {
      lastError = '$e';
      return null;
    }
  }

  String _keyError(String code) {
    switch (code) {
      case 'key_already_redeemed': return tr('key.already');
      case 'key_revoked': return tr('key.revoked');
      case 'key_expired': return tr('key.expired');
      default: return tr('activate.bad');
    }
  }

  // ── поддержка ──
  Future<void> sendMessage(String text) async {
    final t = text.trim();
    if (t.isEmpty) return;
    chat.add(SupportMessage(from: 'user', text: t, at: DateTime.now()));
    notifyListeners();
    if (backendOnline) {
      try {
        await api.supportSend(t);
        final msgs = await api.supportThread();
        chat = msgs.map((j) => SupportMessage(from: j['from'], text: j['text'], at: DateTime.tryParse(j['at'] ?? '') ?? DateTime.now())).toList();
        notifyListeners();
        return;
      } catch (_) {}
    }
    // офлайн-ответ
    await Future.delayed(const Duration(milliseconds: 700));
    chat.add(SupportMessage(from: 'support', text: tr('support.reply'), at: DateTime.now()));
    notifyListeners();
  }

  Future<void> fixInternet() async {
    chat.add(SupportMessage(from: 'system', text: tr('fix.run'), at: DateTime.now()));
    notifyListeners();
    // реальная проверка доступности популярного ресурса
    final report = await engine.diagnose('youtube.com');
    final msg = report == 'unreachable' ? tr('fix.result') : tr('fix.ok');
    chat.add(SupportMessage(from: 'system', text: msg, at: DateTime.now()));
    notifyListeners();
  }

  void reportServer() {
    chat.add(SupportMessage(from: 'system', text: tr('report.done'), at: DateTime.now()));
    notifyListeners();
  }

  void _setBusy(bool v) { busy = v; notifyListeners(); }
}
