// lib/engine/desktop_engine_io.dart
// РЕАЛЬНЫЙ десктоп-движок (Windows/Linux/macOS). Своей логики обхода нет — всё
// делают готовые инструменты. Три пути, выбираются автоматически:
//   • VPN            — есть config.json с НАСТОЯЩИМ сервером → sing-box (туннель,
//                      маршрутизация, ad-block — всё из конфига);
//   • ОБХОД (админ)  — GoodbyeDPI: прозрачный обход DPI на прямом соединении
//                      (WinDivert, нужны права администратора);
//   • ОБХОД (без админа) — ByeDPI: локальный прокси с десинхронизацией DPI;
//                      приложение само прописывает системный прокси (ветка
//                      пользователя, без админа) и откатывает при отключении.
//
// Honest-оговорки: бинарники ставит setup_windows.ps1; стратегии обхода сетезависимы
// (редактируются в файлах *_args.txt); реальный VPN/boost требуют сервера.
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'engine.dart';
import 'singbox_config.dart';
import '../net/probe.dart';
import '../log.dart';

SymbiontEngine? createDesktopEngine() {
  if (!(Platform.isWindows || Platform.isLinux || Platform.isMacOS)) return null;
  final sb = DesktopEngine.locate(Platform.isWindows ? 'sing-box.exe' : 'sing-box', sub: 'bin');
  final gd = DesktopEngine.locate(Platform.isWindows ? 'goodbyedpi.exe' : 'goodbyedpi', sub: 'goodbyedpi');
  final bd = DesktopEngine.locate(Platform.isWindows ? 'ciadpi.exe' : 'ciadpi', sub: 'byedpi');
  final zp = DesktopEngine.zapretDir(); // папка zapret со стратегией .bat (winws внутри bin/)
  if (sb == null && gd == null && bd == null && zp == null) return null;
  return DesktopEngine(singbox: sb, goodbyedpi: gd, byedpi: bd, zapret: zp);
}

class DesktopEngine implements SymbiontEngine {
  final String? singbox;     // VPN (нужен сервер в config.json)
  final String? goodbyedpi;  // обход с админом (прозрачно, WinDivert)
  final String? byedpi;      // обход без админа (локальный прокси)
  final String? zapret;      // winws.exe — самый эффективный обход (РФ), с админом
  final _ctrl = StreamController<ConnStatus>.broadcast();
  ConnStatus _cur = ConnStatus.off;
  Process? _proc;
  CoverageMode _mode = CoverageMode.smart;
  bool _proxyApplied = false;
  bool _zapretActive = false;

  static const int byedpiPort = 1080;

  DesktopEngine({this.singbox, this.goodbyedpi, this.byedpi, this.zapret}) {
    _emit(ConnStatus.off);
    Log.w('engine', 'desktop engine создан: singbox=${singbox != null} goodbyedpi=${goodbyedpi != null} '
        'byedpi=${byedpi != null} zapret=${zapret != null}');
    if (zapret != null) Log.w('engine', 'zapret dir: $zapret');
    // восстановление системного прокси, если прошлый сеанс рухнул, не откатив его
    _WinProxy.recover();
  }

  void _emit(ConnStatus s) { _cur = s; if (!_ctrl.isClosed) _ctrl.add(s); }
  @override
  Stream<ConnStatus> get status => _ctrl.stream;

  // ── поиск бинарников ──
  static String dataDir() {
    final sep = Platform.pathSeparator;
    final base = Platform.isWindows
        ? (Platform.environment['APPDATA'] ?? Directory.systemTemp.path)
        : (Platform.environment['HOME'] ?? Directory.systemTemp.path);
    final d = Directory('$base${sep}Symbiont');
    if (!d.existsSync()) { try { d.createSync(recursive: true); } catch (_) {} }
    return d.path;
  }

  static String? locate(String exe, {required String sub}) {
    final sep = Platform.pathSeparator;
    final c = <String>['${dataDir()}$sep$sub$sep$exe'];
    try { c.add('${File(Platform.resolvedExecutable).parent.path}$sep$sub$sep$exe'); } catch (_) {}
    for (final p in (Platform.environment['PATH'] ?? '').split(Platform.isWindows ? ';' : ':')) {
      if (p.trim().isNotEmpty) c.add('${p.trim()}$sep$exe');
    }
    for (final x in c) { try { if (File(x).existsSync()) return x; } catch (_) {} }
    return null;
  }

  String? configPath() {
    final p = '${dataDir()}${Platform.pathSeparator}config.json';
    return File(p).existsSync() ? p : null;
  }

  /// Папка zapret считается «установленной», если в ней есть хотя бы один .bat
  /// (стратегия Flowseal: general.bat + winws.exe в подпапке bin/).
  static String? zapretDir() {
    final sep = Platform.pathSeparator;
    final dir = '${dataDir()}${sep}zapret';
    try {
      final d = Directory(dir);
      if (d.existsSync() && d.listSync().any((f) => f.path.toLowerCase().endsWith('.bat'))) return dir;
    } catch (_) {}
    return null;
  }

  List<String> _args(String file, List<String> def) {
    try {
      final f = File('${dataDir()}${Platform.pathSeparator}$file');
      if (f.existsSync()) {
        final s = f.readAsStringSync().trim();
        if (s.isNotEmpty) return s.split(RegExp(r'\s+'));
      }
    } catch (_) {}
    return def;
  }

  static Future<bool> _isAdmin() async {
    if (!Platform.isWindows) return false;
    try {
      final r = await Process.run('net', ['session']); // код 0 только у администратора
      return r.exitCode == 0;
    } catch (_) { return false; }
  }

  // Активный узел (с секретами transport из манифеста) — ставит AppState перед connect.
  NodeInfo? activeNode;
  Protection _protection = const Protection();

  @override
  void setActiveNode(NodeInfo? node) { activeNode = node; }

  @override
  bool _connecting = false; // защита от лавины повторных connect
  bool _intentionalStop = false; // true, когда мы сами останавливаем процесс (не крах)
  String _protoPref = 'auto'; // выбор протокола: auto|reality|hysteria2|ss2022
  List<RoutingRule> _rules = const []; // текущие правила маршрутизации (per-site/app)
  @override
  void setProtoPreference(String choice) { _protoPref = choice; }

  // relay-узел («белый» IP) для detour: ставит AppState при детекте занавеса.
  NodeInfo? activeRelay;
  @override
  void setRelay(NodeInfo? relay) { activeRelay = relay; }

  // Скан установленных программ через реестр Windows (как лаунчеры/NVIDIA).
  // Полностью изолирован: любая ошибка → пустой список, приложение не падает.
  @override
  Future<List<InstalledApp>> scanApps() async {
    if (!Platform.isWindows) return const [];
    final found = <String, InstalledApp>{}; // дедуп по exe
    final keys = [
      r'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
      r'HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
      r'HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
    ];
    try {
      for (final root in keys) {
        // ОДИН рекурсивный запрос на корень — быстро. reg разворачивает HKLM→HKEY_LOCAL_MACHINE,
        // поэтому новый ключ ловим по префиксу "HKEY_", а не по исходному root.
        // Прямой вызов reg (надёжно находит программы). Кодировка — системная;
        // редкие кириллические имена могут чуть «поплыть», но скан работает стабильно.
        final res = await Process.run('reg', ['query', root, '/s'])
            .timeout(const Duration(seconds: 20), onTimeout: () => ProcessResult(0, 1, '', ''));
        if (res.exitCode != 0) continue;
        final lines = (res.stdout as String).split('\n');
        String? curName, curIcon, curLoc;
        void flush() {
          final name = curName;
          if (name != null && name.isNotEmpty) {
            final low = name.toLowerCase();
            final junk = low.contains('update') || low.contains('redistributable') || low.contains('runtime') ||
                low.contains('driver') || low.contains('microsoft visual c++') || low.contains('directx') ||
                low.contains('sdk') || low.contains('.net ') || low.endsWith('.net');
            if (!junk) {
              String? exePath;
              if (curIcon != null && curIcon!.toLowerCase().contains('.exe')) {
                exePath = curIcon!.split(',').first.replaceAll('"', '').trim();
              }
              exePath ??= (curLoc != null && curLoc!.isNotEmpty) ? curLoc : null;
              String exeName;
              if (exePath != null && exePath.toLowerCase().endsWith('.exe')) {
                exeName = exePath.split('\\').last;
              } else {
                final safe = name.replaceAll(RegExp(r'[^A-Za-z0-9]'), '');
                exeName = '$safe.exe';
              }
              found.putIfAbsent(exeName.toLowerCase(), () => InstalledApp(name: name, exe: exeName, path: exePath));
            }
          }
          curName = null; curIcon = null; curLoc = null;
        }
        for (final raw in lines) {
          final t = raw.trim();
          if (t.startsWith('HKEY_')) { flush(); continue; }
          if (t.startsWith('DisplayName')) { curName = _afterRegSz(t); }
          else if (t.startsWith('DisplayIcon')) { curIcon = _afterRegSz(t); }
          else if (t.startsWith('InstallLocation')) { curLoc = _afterRegSz(t); }
        }
        flush();
      }
    } catch (e) {
      Log.w('scan', 'скан приложений не удался (изолировано): $e');
      return const [];
    }
    final apps = found.values.toList()..sort((a, b) => a.name.toLowerCase().compareTo(b.name.toLowerCase()));
    Log.w('scan', 'найдено приложений: ${apps.length}');
    // имена (первые 50) — чтобы видеть, что именно нашлось
    if (apps.isNotEmpty) {
      final names = apps.take(50).map((a) => a.name).join(', ');
      Log.w('scan', 'список: $names${apps.length > 50 ? ' …и ещё ${apps.length - 50}' : ''}');
    }
    return apps;
  }

  // из строки "Имя    REG_SZ    Значение" достать Значение (формат `reg query`)
  String? _afterRegSz(String line) {
    final idx = line.indexOf('REG_SZ');
    if (idx < 0) return null;
    final v = line.substring(idx + 'REG_SZ'.length).trim();
    return v.isEmpty ? null : v;
  }

  // Карта трафика: читаем активные соединения из Clash API (127.0.0.1:9090).
  // Любая ошибка изолирована — возвращаем пустой список, приложение не падает.
  @override
  Future<List<TrafficConn>> trafficConnections() async {
    HttpClient? c;
    try {
      c = HttpClient()..connectionTimeout = const Duration(seconds: 2);
      final req = await c.getUrl(Uri.parse('http://127.0.0.1:9090/connections'));
      final resp = await req.close().timeout(const Duration(seconds: 3));
      if (resp.statusCode != 200) return const [];
      final body = await resp.transform(utf8.decoder).join();
      final data = jsonDecode(body) as Map<String, dynamic>;
      final conns = (data['connections'] as List?) ?? const [];
      final out = <TrafficConn>[];
      for (final raw in conns) {
        final m = raw as Map<String, dynamic>;
        final meta = (m['metadata'] as Map<String, dynamic>?) ?? const {};
        final hostName = (meta['host'] as String?)?.isNotEmpty == true
            ? meta['host'] as String
            : (meta['destinationIP'] as String? ?? '—');
        final port = meta['destinationPort']?.toString() ?? '';
        final chains = (m['chains'] as List?)?.cast<String>() ?? const [];
        // последний элемент chains — финальный outbound (proxy/direct/reject)
        final rule = chains.isNotEmpty ? chains.last.toLowerCase() : 'proxy';
        out.add(TrafficConn(
          host: port.isEmpty ? hostName : '$hostName:$port',
          rule: rule.contains('direct') ? 'direct' : (rule.contains('reject') || rule.contains('block') ? 'reject' : 'proxy'),
          network: (meta['network'] as String? ?? 'tcp'),
          up: (m['upload'] as num?)?.toInt() ?? 0,
          down: (m['download'] as num?)?.toInt() ?? 0,
        ));
      }
      // самые «тяжёлые» сверху
      out.sort((a, b) => (b.up + b.down).compareTo(a.up + a.down));
      return out.take(40).toList();
    } catch (e) {
      Log.w('traffic', 'чтение Clash API не удалось (изолировано): $e');
      return const [];
    } finally {
      c?.close(force: true);
    }
  }

  Future<void> connect({String? nodeId, CoverageMode? mode}) async {
    _mode = mode ?? _mode;
    if (_connecting) { Log.w('connect', 'уже идёт подключение — повторный вызов пропущен'); return; }
    _connecting = true;
    try {
      await _connectInner(nodeId: nodeId);
    } finally {
      _connecting = false;
    }
  }

  Future<void> _connectInner({String? nodeId}) async {
    _restartCount = 0; _restartWindow = DateTime.now(); // новое подключение — watchdog снова активен
    // 1) ПРИОРИТЕТ: реальный VPN-узел из манифеста (есть transport + sing-box).
    final node = activeNode;
    if (node?.transport != null && singbox != null) {
      Log.w('connect', 'путь: VPN из манифеста, узел=${node!.id}, протоколы=${(node.transport!).keys.toList()}');
      await _connectFromNode(node);
      return;
    }
    // 2) Иначе — заранее положенный config.json (ручной режим/эксперт).
    final cfg = configPath();
    Log.w('connect', 'старт: node=$nodeId mode=${_mode.name} manifestNode=${node != null} config=${cfg != null}');
    if (cfg != null && singbox != null) {
      Log.w('connect', 'путь: VPN (sing-box) из готового config.json=$cfg');
      await _runSingbox(cfg);
      return;
    }
    // обход без сервера: с админом — GoodbyeDPI (прозрачно), иначе — ByeDPI (прокси)
    final admin = await _isAdmin();
    Log.w('connect', 'админ-права: $admin');
    // дефолт GoodbyeDPI — КЛАССИЧЕСКИЙ набор (= режим -1), он есть во всех версиях
    // (современные режимы -5..-9 в старых релизах отсутствуют → «unknown option»).
    const gdDefault = ['-p', '-r', '-s', '-f', '2', '-k', '2', '-n', '-e', '2'];
    if (admin && zapret != null && _zapretBat() != null) {
      Log.w('connect', 'путь: zapret (winws), bat=${_zapretBat()}');
      await _runZapret(); // самый эффективный путь для РФ
    } else if (admin && goodbyedpi != null) {
      Log.w('connect', 'путь: GoodbyeDPI (прозрачно)');
      await _runProcess(goodbyedpi!, _args('bypass_args.txt', gdDefault),
          workDir: File(goodbyedpi!).parent.path, proto: 'bypass', adminHint: true);
    } else if (byedpi != null) {
      Log.w('connect', 'путь: ByeDPI (прокси, без админа)');
      await _runByeDpi();
    } else if (goodbyedpi != null) {
      Log.w('connect', 'путь: GoodbyeDPI без админа (вероятно упадёт)');
      // только GoodbyeDPI, но нет прав — запустим (упадёт) и честно подскажем про админа/ByeDPI
      await _runProcess(goodbyedpi!, _args('bypass_args.txt', gdDefault),
          workDir: File(goodbyedpi!).parent.path, proto: 'bypass', adminHint: true);
    } else if (zapret != null) {
      Log.w('connect', 'zapret есть, но нет прав администратора → need_admin');
      _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: 'need_admin')); // winws без админа не запустить
    } else if (singbox != null) {
      Log.w('connect', 'только sing-box, но нет config.json → no_config');
      _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: 'no_config'));
    } else {
      Log.w('connect', 'движков нет → no_engine');
      _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: 'no_engine'));
    }
  }

  // ── VPN: sing-box ──
  Future<void> _runSingbox(String cfg) async {
    _emit(ConnStatus(phase: ConnPhase.connecting, mode: _mode));
    try {
      await _stop();
      final check = await Process.run(singbox!, ['check', '-c', cfg], workingDirectory: dataDir());
      if (check.exitCode != 0) {
        final why = (check.stderr?.toString() ?? '').trim();
        _emit(ConnStatus(phase: ConnPhase.error, mode: _mode,
          error: 'config: ${why.isEmpty ? 'invalid (code ${check.exitCode})' : why}'));
        return;
      }
      final proc = await Process.start(singbox!, ['run', '-c', cfg, '-D', dataDir()]);
      _attach(proc, proto: 'vpn');
    } catch (e) {
      _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: '$e'));
    }
  }

  // ── VPN из узла манифеста: строим клиентский конфиг из transport и поднимаем sing-box ──
  Future<void> _connectFromNode(NodeInfo node) async {
    _emit(ConnStatus(phase: ConnPhase.connecting, mode: _mode));
    try {
      await _stop();
      final t = node.transport!;
      // Порядок каскада подбираем сами — пользователю об этом думать не нужно.
      // На localhost/петле Reality физически не работает (нет реального SNI-таргета),
      // поэтому надёжный SS2022 ставим первым. На реальном сервере Reality — лучший
      // для обхода DPI, он и идёт первым.
      final host = (node.host ?? '').toLowerCase();
      final isLoopback = host == '127.0.0.1' || host == 'localhost' || host == '::1' || host.startsWith('127.');
      var order = isLoopback
          ? ['ss2022', 'hysteria2', 'reality']
          : ['reality', 'hysteria2', 'ss2022'];
      // выбор протокола пользователем: если не 'auto' — ставим выбранный первым,
      // остальные оставляем как fallback (надёжность не теряем).
      if (_protoPref != 'auto' && order.contains(_protoPref)) {
        order = [_protoPref, ...order.where((p) => p != _protoPref)];
        Log.w('vpn', 'выбор протокола пользователя: $_protoPref → первым');
      }
      Log.w('vpn', 'узел=${node.id} host=$host loopback=$isLoopback → порядок каскада: $order');
      final cascade = <NodeEndpoint>[];
      for (final proto in order) {
        final p = t[proto];
        if (p is Map) {
          final m = p.cast<String, dynamic>();
          cascade.add(NodeEndpoint(
            id: '${node.id}-$proto',
            server: (m['server'] ?? node.host ?? '') as String,
            port: (m['port'] ?? 443) as int,
            protocol: proto,
            xrayCore: false, // наш сервер — sing-box (не Xray); reality идёт http-фолбэком
            params: m,
          ));
        }
      }
      if (cascade.isEmpty) {
        Log.w('vpn', 'у узла ${node.id} нет валидного transport — нечем подключаться');
        _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: 'no_config'));
        return;
      }
      Log.w('vpn', 'каскад: ${cascade.map((e) => e.protocol).toList()}');
      // полный VPN (tun) при админе, иначе локальный прокси (mixed) без админа
      final admin = await _isAdmin();
      // На loopback (ПК как сервер) ВСЕГДА mixed: полный VPN(tun) на петле создаёт
      // закольцовку и конфликтует с другим VPN/интернетом. mixed поднимает локальный
      // прокси — туннель проверяется, но систему не перехватывает (интернет жив).
      final inbound = (isLoopback || !admin) ? 'mixed' : 'tun';
      Log.w('vpn', 'inbound=$inbound (admin=$admin, loopback=$isLoopback)');
      // relay (detour через «белый» IP) — если AppState его выставил (детект занавеса).
      NodeEndpoint? relayEp;
      final rt = activeRelay?.transport?['reality'];
      if (rt is Map) {
        final rm = rt.cast<String, dynamic>();
        relayEp = NodeEndpoint(
          id: '${activeRelay!.id}-relay',
          server: (rm['server'] ?? activeRelay!.host ?? '') as String,
          port: (rm['port'] ?? 443) as int,
          protocol: 'reality', xrayCore: false, params: rm,
        );
        Log.w('vpn', 'relay включён: detour через ${activeRelay!.id} (обход занавеса/whitelist)');
      }
      final cfg = SingboxConfig.build(
        endpoint: cascade.first,
        cascade: cascade,
        relay: relayEp,
        rules: _rules,
        protection: _protection,
        inbound: inbound,
        clashApi: true,
        sessionSeed: '${node.id}-${DateTime.now().millisecondsSinceEpoch}',
      );
      // пишем конфиг рядом с данными
      final path = '${dataDir()}${Platform.pathSeparator}generated_config.json';
      File(path).writeAsStringSync(const JsonEncoder.withIndent('  ').convert(cfg));
      Log.w('vpn', 'конфиг записан: $path');
      // проверяем конфиг ДО запуска — частая причина «тихого» падения
      final check = await Process.run(singbox!, ['check', '-c', path], workingDirectory: dataDir());
      if (check.exitCode != 0) {
        final why = (check.stderr?.toString() ?? '').trim();
        Log.w('vpn', 'sing-box check ПРОВАЛ (code ${check.exitCode}): $why');
        _emit(ConnStatus(phase: ConnPhase.error, mode: _mode,
          error: 'config: ${why.isEmpty ? 'invalid (code ${check.exitCode})' : why}'));
        return;
      }
      Log.w('vpn', 'sing-box check OK — запускаю');
      final proc = await Process.start(singbox!, ['run', '-c', path, '-D', dataDir()]);
      _attach(proc, proto: 'vpn');
    } catch (e) {
      Log.w('vpn', 'ОШИБКА _connectFromNode: $e');
      _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: '$e'));
    }
  }


  Future<void> _runByeDpi() async {
    _emit(ConnStatus(phase: ConnPhase.connecting, mode: _mode));
    try {
      await _stop();
      final args = _args('byedpi_args.txt', ['-p', '$byedpiPort', '--disorder', '1', '--auto=torst', '--tlsrec', '1+s']);
      final proc = await Process.start(byedpi!, args, workingDirectory: File(byedpi!).parent.path);
      _proc = proc;
      proc.stdout.drain<void>().catchError((_) {});
      var lastErr = '';
      proc.stderr.transform(utf8.decoder).listen((s) { final t = s.trim(); if (t.isNotEmpty) lastErr = t; }, onError: (_) {});
      var exited = false;
      proc.exitCode.then((code) async {
        exited = true;
        await _clearProxy();
        if (_cur.phase == ConnPhase.connecting || _cur.phase == ConnPhase.on) {
          _emit(ConnStatus(phase: ConnPhase.error, mode: _mode,
            error: lastErr.isNotEmpty ? lastErr : 'byedpi exited (code $code) — проверьте стратегию в byedpi_args.txt'));
        }
      });
      await Future.delayed(const Duration(milliseconds: 1500));
      if (exited) return;
      // ByeDPI поднялся → включаем системный прокси (без админа), с откатом
      await _WinProxy.set('socks=127.0.0.1:$byedpiPort');
      _proxyApplied = true;
      _emit(ConnStatus(phase: ConnPhase.on, mode: _mode, protocol: 'bypass-proxy'));
    } catch (e) {
      await _clearProxy();
      _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: '$e'));
    }
  }

  // ── общий запуск процесса (GoodbyeDPI/VPN-после-проверки) ──
  Future<void> _runProcess(String bin, List<String> args, {required String workDir, required String proto, bool adminHint = false}) async {
    _emit(ConnStatus(phase: ConnPhase.connecting, mode: _mode));
    try {
      await _stop();
      final proc = await Process.start(bin, args, workingDirectory: workDir);
      _attach(proc, proto: proto, adminHint: adminHint);
    } catch (e) {
      _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: '$e'));
    }
  }

  void _attach(Process proc, {required String proto, bool adminHint = false}) {
    _proc = proc;
    _lastProto = proto; // для watchdog
    _intentionalStop = false; // новый запуск — снимаем флаг намеренной остановки
    proc.stdout.drain<void>().catchError((_) {});
    var lastErr = '';
    proc.stderr.transform(utf8.decoder).listen((s) { final t = s.trim(); if (t.isNotEmpty) lastErr = t; }, onError: (_) {});
    var exited = false;
    proc.exitCode.then((code) {
      exited = true;
      // намеренное отключение пользователем — это НЕ крах: молчим, без ошибки и перезапуска
      if (_intentionalStop) {
        Log.w('vpn', 'процесс остановлен (намеренно)');
        return;
      }
      if (_cur.phase == ConnPhase.connecting || _cur.phase == ConnPhase.on) {
        var err = lastErr.isNotEmpty ? lastErr : 'процесс завершился (code $code)';
        if (adminHint && lastErr.isEmpty) err += ' — нужны права администратора (или используйте ByeDPI без админа)';
        Log.w('watchdog', 'процесс ($proto) упал: $err');
        _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: err));
        // НЕ перезапускаем при ошибке конфигурации — перезапуск даст ту же ошибку
        // (бессмысленная лавина). Рестарт только при реальном крахе уже поднятого туннеля.
        final lower = err.toLowerCase();
        final isConfigError = lower.contains('fatal') || lower.contains('decode config') ||
            lower.contains('initialize') || lower.contains('rule-set') ||
            lower.contains('parse') || lower.contains('deprecated');
        if (isConfigError) {
          Log.w('watchdog', 'это ошибка конфигурации — авто-перезапуск НЕ выполняется');
        } else {
          _maybeAutoRestart(proto);
        }
      }
    });
    Future.delayed(const Duration(milliseconds: 1500), () {
      if (!exited && (_cur.phase == ConnPhase.connecting)) {
        // для VPN кладём узел в статус — UI покажет страну/IP сервера (как у коммерческих VPN)
        _emit(ConnStatus(phase: ConnPhase.on, mode: _mode, protocol: proto,
          node: proto == 'vpn' ? activeNode : null));
      }
    });
  }

  // ── watchdog: авто-перезапуск упавшего канала (ограниченное число попыток) ──
  String? _lastProto;
  int _restartCount = 0;
  DateTime _restartWindow = DateTime.now();

  Future<void> _maybeAutoRestart(String proto) async {
    // не более 3 перезапусков за 60 секунд, чтобы не уйти в цикл
    final now = DateTime.now();
    if (now.difference(_restartWindow) > const Duration(seconds: 60)) {
      _restartWindow = now; _restartCount = 0;
    }
    if (_restartCount >= 3) {
      Log.w('watchdog', 'лимит авто-перезапусков ($proto) — останавливаюсь, нужен ручной повтор');
      return;
    }
    _restartCount++;
    Log.w('watchdog', 'авто-перезапуск ($proto), попытка $_restartCount/3 через 2с…');
    await Future.delayed(const Duration(seconds: 2));
    // если пользователь сам не отключился за это время — пробуем поднять заново
    if (_cur.phase == ConnPhase.error) {
      if (proto == 'vpn' && activeNode != null) {
        await _connectFromNode(activeNode!);
      } else if (proto == 'bypass' && zapret != null) {
        await _runZapret();
      } else {
        await connect(); // общий путь выберет лучший доступный
      }
    }
  }


  Future<void> _clearProxy() async {
    if (_proxyApplied) { await _WinProxy.restore(); _proxyApplied = false; }
  }

  Future<void> _stop() async {
    _intentionalStop = true; // гасим watchdog: завершение процесса ниже — наше, не крах
    final p = _proc; _proc = null;
    if (p != null) { try { p.kill(); } catch (_) {} }
    // Подстраховка: убиваем осиротевшие sing-box от прошлых запусков/падений,
    // иначе они держат порт 2080 и новый туннель не стартует
    // («Only one usage of each socket address»).
    await _killOrphanSingbox();
  }

  // Убить чужие/зависшие процессы sing-box (не трогает другие приложения).
  Future<void> _killOrphanSingbox() async {
    try {
      if (Platform.isWindows) {
        await Process.run('taskkill', ['/F', '/IM', 'sing-box.exe', '/T']);
      } else {
        await Process.run('pkill', ['-f', 'sing-box']);
      }
      // даём ОС освободить порт перед новым bind
      await Future.delayed(const Duration(milliseconds: 350));
    } catch (_) {/* нет процесса — это норма */}
  }

  // ── обход через zapret/winws (самый эффективный для РФ, нужен админ) ──
  String? _zapretBat() {
    final sep = Platform.pathSeparator;
    final dir = '${dataDir()}${sep}zapret';
    var name = 'general.bat'; // можно переопределить файлом zapret_bat.txt
    try {
      final ov = File('${dataDir()}${sep}zapret_bat.txt');
      if (ov.existsSync()) { final s = ov.readAsStringSync().trim(); if (s.isNotEmpty) name = s; }
    } catch (_) {}
    final p = '$dir$sep$name';
    if (File(p).existsSync()) return p;
    try {
      final bats = Directory(dir).listSync()
          .where((f) => f.path.toLowerCase().endsWith('.bat'))
          .where((f) => !f.path.toLowerCase().contains('service'))
          .toList();
      // предпочесть general*.bat, иначе первый подходящий
      final gen = bats.where((f) => f.uri.pathSegments.last.toLowerCase().startsWith('general'));
      if (gen.isNotEmpty) return gen.first.path;
      if (bats.isNotEmpty) return bats.first.path;
    } catch (_) {}
    return null;
  }

  Future<bool> _winwsRunning() async {
    try {
      final r = await Process.run('tasklist', ['/FI', 'IMAGENAME eq winws.exe', '/NH']);
      return r.stdout.toString().toLowerCase().contains('winws.exe');
    } catch (_) { return false; }
  }

  Future<void> _killWinws() async {
    try { await Process.run('taskkill', ['/F', '/IM', 'winws.exe']); } catch (_) {}
  }

  Future<void> _runZapret() async {
    _emit(ConnStatus(phase: ConnPhase.connecting, mode: _mode));
    try {
      await _stop();
      await _killWinws(); // на случай зависшего прошлого процесса
      final bat = _zapretBat();
      Log.w('zapret', 'стратегия: ${bat ?? "НЕ НАЙДЕНА"}');
      if (bat == null) {
        _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: 'zapret: стратегия (.bat) не найдена в папке zapret'));
        return;
      }
      final ok = await _startZapretHidden(bat);
      Log.w('zapret', 'winws поднялся: $ok');
      if (ok) {
        _zapretActive = true;
        _emit(ConnStatus(phase: ConnPhase.on, mode: _mode, protocol: 'bypass'));
      } else {
        _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: 'zapret не запустился (нужны права администратора?)'));
      }
    } catch (e) {
      Log.w('zapret', 'ОШИБКА: $e');
      _emit(ConnStatus(phase: ConnPhase.error, mode: _mode, error: '$e'));
    }
  }

  /// Запускает стратегию zapret СКРЫТО — без отдельного окна winws и без логов на экране.
  /// Делает временную копию .bat, в которой `start "..." /min "winws.exe"` заменён на
  /// прямой вызов (winws тогда наследует скрытое окно родителя), а вывод service.bat гасится.
  /// Возвращает true, если процесс winws поднялся.
  Future<bool> _startZapretHidden(String batPath) async {
    final sep = Platform.pathSeparator;
    final dir = File(batPath).parent.path;
    try {
      // если предыдущий winws не умер (часто держит WinDivert64.sys) — фиксируем
      if (await _winwsRunning()) {
        Log.w('zapret', 'ВНИМАНИЕ: winws всё ещё запущен перед стартом — пробую убить ещё раз');
        await _killWinws();
        await Future.delayed(const Duration(milliseconds: 800));
        if (await _winwsRunning()) {
          Log.w('zapret', 'winws НЕ убивается (возможно завис драйвер) — нужен перезапуск/закрытие старого экземпляра');
        }
      }
      var content = File(batPath).readAsStringSync();
      content = content.replaceAll(RegExp(r'start\s+"[^"]*"\s+/min\s+', caseSensitive: false), '');
      content = content.replaceAll(RegExp(r'start\s+/min\s+', caseSensitive: false), '');
      final hidden = '$dir${sep}_symbiont_hidden.bat';
      final winwsLog = '$dir${sep}winws_out.log';
      // тело без двойной шапки; в конец winws-команды добавим перенаправление вывода в лог
      var body = content
          .replaceFirst(RegExp(r'^@echo off\s*', caseSensitive: false), '')
          .replaceFirst(RegExp(r'chcp\s+65001\s*>\s*nul\s*', caseSensitive: false), '');
      final wrapped = '@echo off\r\nchcp 65001 > nul\r\ncd /d "$dir"\r\n$body';
      File(hidden).writeAsStringSync(wrapped);
      Log.w('zapret', 'скрытый .bat создан: $hidden');
      try { File(winwsLog).writeAsStringSync(''); } catch (_) {}
      // запуск скрыто (без окна), вывод winws уходит в файл winws_out.log внутри cmd
      await Process.start('powershell', [
        '-WindowStyle', 'Hidden', '-NonInteractive', '-Command',
        "Start-Process -WindowStyle Hidden -FilePath cmd.exe -ArgumentList '/c','\"\"$hidden\" > \"$winwsLog\" 2>&1\"'"
      ], mode: ProcessStartMode.detached);
      Log.w('zapret', 'PowerShell скрытый запуск отправлен; жду подъёма winws…');
      for (var i = 0; i < 8; i++) {
        await Future.delayed(const Duration(seconds: 2));
        if (await _winwsRunning()) { Log.w('zapret', 'winws обнаружен через ${(i + 1) * 2}s'); return true; }
      }
      // не поднялся — приложим хвост его лога для диагностики
      try {
        final lf = File(winwsLog);
        if (lf.existsSync()) {
          final t = lf.readAsStringSync();
          final tail = t.length > 600 ? t.substring(t.length - 600) : t;
          Log.w('zapret', 'winws не поднялся за 16s. Хвост winws_out.log:\n$tail');
        } else {
          Log.w('zapret', 'winws не поднялся за 16s, winws_out.log пуст/отсутствует');
        }
      } catch (_) {}
      return await _winwsRunning();
    } catch (e) {
      Log.w('zapret', 'скрытый запуск ОШИБКА: $e');
      return false;
    }
  }

  @override
  Future<void> requestAdmin() async {
    if (!Platform.isWindows) return;
    final exe = Platform.resolvedExecutable;
    try {
      await Process.run('powershell', ['-Command', "Start-Process -FilePath '$exe' -Verb RunAs"]);
    } catch (_) {}
    try { await disconnect(); } catch (_) {} // снять прокси перед выходом
    exit(0); // закрыть текущий (не-привилегированный) экземпляр; запустится привилегированный
  }

  @override
  Future<void> disconnect() async {
    _restartCount = 99; // запрет авто-перезапуска: это сознательное отключение
    _lastProto = null;
    await _stop();
    if (_zapretActive) { await _killWinws(); _zapretActive = false; }
    await _clearProxy();
    Log.w('connect', 'отключено пользователем');
    _emit(ConnStatus(phase: ConnPhase.off, mode: _mode));
  }

  @override
  Future<List<NodeInfo>> listNodes() async => const [];
  @override
  Future<NodeInfo> fastestNode() async => throw StateError('not used');
  @override
  Future<void> setCoverage(CoverageMode mode) async { _mode = mode; if (mode == CoverageMode.off) await disconnect(); }
  @override
  @override
  Future<void> applyRules(List<RoutingRule> rules) async {
    _rules = List.of(rules);
    Log.w('rules', 'правила обновлены: ${_rules.length} шт.');
    // если туннель активен — пересобрать конфиг и переподнять, чтобы правила вступили в силу
    if (_cur.phase == ConnPhase.on && activeNode != null) {
      Log.w('rules', 'туннель активен — применяю правила (переподключение)');
      await _connectFromNode(activeNode!);
    }
  }
  @override
  Future<List<ScanItem>> runAnalysis() async => const [];
  @override
  Future<void> setProtection(Protection p) async { _protection = p; }
  @override
  Future<String> diagnose(String target) async {
    final p = await Probe.tcpPing(target, 443, timeout: const Duration(seconds: 3));
    return p == null ? 'unreachable' : 'reachable';
  }

  @override
  Future<String?> autoTuneBypass(List<String> testHosts, {void Function(String stage)? onProgress}) async {
    if (!Platform.isWindows) return null;
    final admin = await _isAdmin();
    Log.w('autotune', 'старт: admin=$admin zapret=${zapret != null} goodbyedpi=${goodbyedpi != null} hosts=$testHosts');
    if (!admin) { Log.w('autotune', 'нет прав администратора → подбор недоступен'); return null; }
    final hosts = testHosts.isEmpty ? const ['youtube.com', 'discord.com', 'x.com'] : testHosts;

    // ПРИОРИТЕТ: если установлен zapret — подбираем его стратегии (.bat).
    if (zapret != null) {
      return await _autoTuneZapret(hosts, onProgress);
    }
    if (goodbyedpi == null) { Log.w('autotune', 'нет движка обхода'); return null; }

    final candidates = <List<String>>[
      ['-p', '-r', '-s', '-f', '2', '-k', '2', '-n', '-e', '2'],
      ['-p', '-r', '-s', '-e', '40'],
      ['-f', '2', '-e', '2', '--reverse-frag', '--max-payload'],
      ['-f', '2', '-e', '2', '--wrong-seq', '--reverse-frag', '--max-payload'],
      ['-f', '2', '-e', '2', '--wrong-chksum', '--reverse-frag', '--max-payload'],
      ['-5'], ['-6'], ['-9'],
    ];
    final deadline = DateTime.now().add(const Duration(seconds: 70));
    for (var i = 0; i < candidates.length; i++) {
      final args = candidates[i];
      if (DateTime.now().isAfter(deadline)) { Log.w('autotune', 'лимит времени'); break; }
      onProgress?.call('GoodbyeDPI ${i + 1}/${candidates.length}: ${args.join(' ')}');
      Log.w('autotune', 'GoodbyeDPI вариант ${i + 1}/${candidates.length}: ${args.join(' ')}');
      await _stop();
      try {
        final proc = await Process.start(goodbyedpi!, args, workingDirectory: File(goodbyedpi!).parent.path);
        _proc = proc;
        proc.stdout.drain<void>().catchError((_) {});
        proc.stderr.drain<void>().catchError((_) {});
        await Future.delayed(const Duration(milliseconds: 2500));
        var ok = 0;
        for (final h in hosts) {
          final st = await Probe.checkHost(h).timeout(const Duration(seconds: 6), onTimeout: () => 'tcp');
          Log.w('autotune', '  $h → $st');
          if (st == 'ok') ok++;
        }
        Log.w('autotune', '  итог: $ok/${hosts.length} открылось');
        if (hosts.isNotEmpty && ok >= (hosts.length / 2).ceil()) {
          try { File('${dataDir()}${Platform.pathSeparator}bypass_args.txt').writeAsStringSync(args.join(' ')); } catch (_) {}
          Log.w('autotune', 'РАБОЧАЯ стратегия GoodbyeDPI: ${args.join(' ')}');
          _emit(ConnStatus(phase: ConnPhase.on, mode: _mode, protocol: 'bypass'));
          return args.join(' ');
        }
      } catch (e) { Log.w('autotune', '  ошибка варианта: $e'); }
    }
    await _stop();
    Log.w('autotune', 'GoodbyeDPI: рабочая стратегия не найдена');
    _emit(ConnStatus(phase: ConnPhase.off, mode: _mode));
    return null;
  }

  /// Перебор готовых стратегий zapret (.bat) с проверкой, открылись ли сайты.
  Future<String?> _autoTuneZapret(List<String> hosts, void Function(String stage)? onProgress) async {
    final sep = Platform.pathSeparator;
    final dir = '${dataDir()}${sep}zapret';
    List<String> bats;
    try {
      bats = Directory(dir).listSync()
          .map((f) => f.uri.pathSegments.last)
          .where((n) => n.toLowerCase().endsWith('.bat') && !n.toLowerCase().contains('service') && !n.toLowerCase().contains('_symbiont'))
          .toList();
    } catch (e) { Log.w('autotune', 'не читается папка zapret: $e'); return null; }
    if (bats.isEmpty) { Log.w('autotune', 'в папке zapret нет .bat стратегий'); return null; }
    int rank(String n) {
      final l = n.toLowerCase();
      if (l == 'general.bat') return 0;
      if (l.contains('fake tls auto')) return 1;
      if (l.contains('alt') && !l.contains('alt1')) return 2;
      return 3;
    }
    bats.sort((a, b) => rank(a).compareTo(rank(b)));
    Log.w('autotune', 'zapret стратегий к перебору: ${bats.length} → ${bats.take(6).join(", ")}…');

    final deadline = DateTime.now().add(const Duration(seconds: 120));
    var winwsFailStreak = 0; // сколько стратегий подряд winws не поднялся
    for (var i = 0; i < bats.length; i++) {
      final bat = bats[i];
      if (DateTime.now().isAfter(deadline)) { Log.w('autotune', 'лимит времени подбора zapret'); break; }
      onProgress?.call('zapret ${i + 1}/${bats.length}: $bat');
      Log.w('autotune', 'zapret стратегия ${i + 1}/${bats.length}: $bat');
      try {
        await _killWinws();
        final up = await _startZapretHidden('$dir$sep$bat');
        if (!up) {
          winwsFailStreak++;
          Log.w('autotune', '  winws не поднялся на этой стратегии — пропуск');
          // если winws не стартует 3 раза подряд — он, скорее всего, заблокирован
          // антивирусом/Defender, и дальнейший перебор бессмыслен. Прекращаем рано,
          // чтобы не молотить 5 минут впустую и не мешать туннелю.
          if (winwsFailStreak >= 3) {
            Log.w('autotune', 'winws не запускается ($winwsFailStreak раза подряд) — обход через zapret недоступен (вероятно, блокирует антивирус). Прекращаю перебор.');
            onProgress?.call('winws заблокирован антивирусом — обход недоступен');
            break;
          }
          continue;
        }
        winwsFailStreak = 0; // успешный старт — сбрасываем счётчик
        await Future.delayed(const Duration(seconds: 2));
        var ok = 0;
        for (final h in hosts) {
          final st = await Probe.checkHost(h).timeout(const Duration(seconds: 7), onTimeout: () => 'tcp');
          Log.w('autotune', '  $h → $st');
          if (st == 'ok') ok++;
        }
        Log.w('autotune', '  итог: $ok/${hosts.length} открылось');
        if (hosts.isNotEmpty && ok >= (hosts.length / 2).ceil()) {
          try { File('$dir${sep}..${sep}zapret_bat.txt').writeAsStringSync(bat); } catch (_) {}
          Log.w('autotune', 'РАБОЧАЯ стратегия zapret: $bat (сохранена в zapret_bat.txt)');
          _zapretActive = true;
          _emit(ConnStatus(phase: ConnPhase.on, mode: _mode, protocol: 'bypass'));
          return bat;
        }
      } catch (e) { Log.w('autotune', '  ошибка стратегии: $e'); }
    }
    await _killWinws();
    _zapretActive = false;
    Log.w('autotune', 'zapret: рабочая стратегия не найдена среди перебранных');
    _emit(ConnStatus(phase: ConnPhase.off, mode: _mode));
    return null;
  }
}

/// Управление системным прокси Windows (ветка ТЕКУЩЕГО пользователя — без админа).
/// Сохраняет прежние значения в файл-бэкап, чтобы откатить даже после сбоя.
class _WinProxy {
  static const _key = r'HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings';
  static String get _backup => '${DesktopEngine.dataDir()}${Platform.pathSeparator}proxy_backup.json';

  static Future<String> _query(String name) async {
    try {
      final r = await Process.run('reg', ['query', _key, '/v', name]);
      if (r.exitCode != 0) return '';
      final out = r.stdout.toString();
      final m = RegExp('$name\\s+REG_\\w+\\s+(.+)').firstMatch(out);
      return m?.group(1)?.trim() ?? '';
    } catch (_) { return ''; }
  }

  static Future<void> set(String proxyServer) async {
    if (!Platform.isWindows) return;
    try {
      // сохранить прежнее состояние (для отката)
      final prevEnable = await _query('ProxyEnable');
      final prevServer = await _query('ProxyServer');
      File(_backup).writeAsStringSync(jsonEncode({'enable': prevEnable, 'server': prevServer}));
      await Process.run('reg', ['add', _key, '/v', 'ProxyServer', '/t', 'REG_SZ', '/d', proxyServer, '/f']);
      await Process.run('reg', ['add', _key, '/v', 'ProxyEnable', '/t', 'REG_DWORD', '/d', '1', '/f']);
    } catch (_) {}
  }

  static Future<void> restore() async {
    if (!Platform.isWindows) return;
    try {
      final f = File(_backup);
      if (f.existsSync()) {
        final j = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
        final server = (j['server'] ?? '').toString();
        final enable = (j['enable'] ?? '0x0').toString();
        if (server.isEmpty) {
          await Process.run('reg', ['delete', _key, '/v', 'ProxyServer', '/f']);
        } else {
          await Process.run('reg', ['add', _key, '/v', 'ProxyServer', '/t', 'REG_SZ', '/d', server, '/f']);
        }
        final on = enable.toLowerCase().contains('1');
        await Process.run('reg', ['add', _key, '/v', 'ProxyEnable', '/t', 'REG_DWORD', '/d', on ? '1' : '0', '/f']);
        f.deleteSync();
      } else {
        // нет бэкапа — просто выключаем прокси
        await Process.run('reg', ['add', _key, '/v', 'ProxyEnable', '/t', 'REG_DWORD', '/d', '0', '/f']);
      }
    } catch (_) {}
  }

  /// Вызывается при старте: если остался бэкап (прошлый сеанс не откатил прокси) — откатываем.
  static Future<void> recover() async {
    if (!Platform.isWindows) return;
    try { if (File(_backup).existsSync()) await restore(); } catch (_) {}
  }
}
