// lib/net/probe_io.dart — реальная реализация замеров/диагностики на dart:io (native).
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'curtain.dart';

class Probe {
  // ── базовый TCP-пинг ──
  static Future<int?> tcpPing(String host, int port,
      {Duration timeout = const Duration(seconds: 2)}) async {
    final sw = Stopwatch()..start();
    Socket? s;
    try {
      s = await Socket.connect(host, port, timeout: timeout);
      sw.stop();
      final us = sw.elapsedMicroseconds;
      return us < 1000 ? 1 : (us / 1000).round(); // не показываем 0 для реального соединения
    } catch (_) {
      return null;
    } finally {
      try { s?.destroy(); } catch (_) {}
    }
  }

  static Future<int?> median(String host, int port, {int samples = 3}) async {
    final r = <int>[];
    for (var i = 0; i < samples; i++) {
      final p = await tcpPing(host, port);
      if (p != null) r.add(p);
    }
    if (r.isEmpty) return null;
    r.sort();
    return r[r.length ~/ 2];
  }

  static Future<int?> directPing() async {
    const anchors = [['1.1.1.1', 443], ['8.8.8.8', 443], ['77.88.55.88', 443]];
    for (final a in anchors) {
      final p = await median(a[0] as String, a[1] as int, samples: 3);
      if (p != null) return p;
    }
    return null;
  }

  static Future<double?> lossPct(String host, int port, {int samples = 6}) async {
    var fail = 0;
    for (var i = 0; i < samples; i++) {
      if (await tcpPing(host, port) == null) fail++;
    }
    return fail * 100.0 / samples;
  }

  // ── публичная информация о подключении (как 2ip) ──
  static Future<Map<String, String>> publicInfo() async {
    const urls = ['http://ip-api.com/json/?fields=query,country,isp,as'];
    for (final u in urls) {
      HttpClient? c;
      try {
        c = HttpClient()..connectionTimeout = const Duration(seconds: 5);
        final req = await c.getUrl(Uri.parse(u));
        final resp = await req.close().timeout(const Duration(seconds: 6));
        if (resp.statusCode == 200) {
          final body = await resp.transform(utf8.decoder).join().timeout(const Duration(seconds: 5));
          final j = jsonDecode(body) as Map<String, dynamic>;
          return {
            'ip': (j['query'] ?? '').toString(),
            'country': (j['country'] ?? '').toString(),
            'isp': (j['isp'] ?? '').toString(),
            'as': (j['as'] ?? '').toString(),
          };
        }
      } catch (_) {
      } finally {
        try { c?.close(); } catch (_) {}
      }
    }
    return {};
  }

  // ── классификация доступности хоста: ok | dns | tcp | tls ──
  // tls = TCP проходит, но TLS-рукопожатие рвётся — классическая сигнатура DPI.
  static Future<String> checkHost(String host) async {
    try {
      final addrs = await InternetAddress.lookup(host).timeout(const Duration(seconds: 4));
      if (addrs.isEmpty) return 'dns';
    } catch (_) {
      return 'dns';
    }
    Socket? sock;
    try {
      sock = await Socket.connect(host, 443, timeout: const Duration(seconds: 4));
    } catch (_) {
      return 'tcp';
    }
    try {
      final secure = await SecureSocket.secure(sock, host: host).timeout(const Duration(seconds: 5));
      secure.destroy();
      return 'ok';
    } catch (_) {
      try { sock.destroy(); } catch (_) {}
      return 'tls';
    }
  }

  // ── грубый замер скорости загрузки (Мбит/с) ──
  static Future<double?> downloadMbps() async {
    const url = 'https://speed.cloudflare.com/__down?bytes=3000000'; // ~3 МБ
    HttpClient? c;
    try {
      c = HttpClient()..connectionTimeout = const Duration(seconds: 6);
      final sw = Stopwatch()..start();
      final req = await c.getUrl(Uri.parse(url));
      final resp = await req.close().timeout(const Duration(seconds: 8));
      var bytes = 0;
      // Сторож ТИШИНЫ + общий дедлайн (как в probeCurtain). `await for` виснул, если
      // тело замирало между чанками: проверка дедлайна срабатывала только с приходом
      // чанка, а внешний .timeout лишь бросал future, оставляя HttpClient/сокет
      // открытыми (утечка на каждом флаки-замере).
      final done = Completer<void>();
      Timer? watchdog;
      void arm() {
        watchdog?.cancel();
        watchdog = Timer(const Duration(seconds: 4), () {
          if (!done.isCompleted) done.complete();
        });
      }
      arm();
      final sub = resp.listen((chunk) {
        bytes += chunk.length;
        arm();                       // данные пришли — перевзводим сторож тишины
      }, onDone: () { if (!done.isCompleted) done.complete(); },
         onError: (_) { if (!done.isCompleted) done.complete(); });
      await done.future.timeout(const Duration(seconds: 12), onTimeout: () {});
      watchdog?.cancel();
      await sub.cancel();
      sw.stop();
      if (sw.elapsedMilliseconds <= 0 || bytes <= 0) return null;
      return (bytes * 8) / (sw.elapsedMilliseconds * 1000); // Мбит/с
    } catch (_) {
      return null;
    } finally {
      try { c?.close(force: true); } catch (_) {}
    }
  }

  /// Проба «16 КБ занавеса»: тянем >target байт; если поток замирает (нет данных
  /// дольше stallSecs) — фиксируем сколько успели и классифицируем. По умолчанию
  /// бьём в большой иностранный ассет (cloudflare) — тот же путь, что душит ТСПУ.
  static Future<CurtainVerdict> probeCurtain(
      {String? url, int target = 64 * 1024, int stallSecs = 4}) async {
    final u = url ?? 'https://speed.cloudflare.com/__down?bytes=${target * 2}';
    HttpClient? c;
    try {
      c = HttpClient()..connectionTimeout = const Duration(seconds: 6);
      final req = await c.getUrl(Uri.parse(u));
      final resp = await req.close().timeout(const Duration(seconds: 8));
      var bytes = 0;
      var stalled = false;
      final done = Completer<void>();
      Timer? watchdog;
      void arm() {
        watchdog?.cancel();
        watchdog = Timer(Duration(seconds: stallSecs), () {
          stalled = true;
          if (!done.isCompleted) done.complete();
        });
      }
      arm();
      final sub = resp.listen((chunk) {
        bytes += chunk.length;
        if (bytes >= target) {
          if (!done.isCompleted) done.complete();
        } else {
          arm(); // данные пришли — перевзводим сторож тишины
        }
      }, onDone: () {
        if (!done.isCompleted) done.complete();
      }, onError: (_) {
        if (!done.isCompleted) done.complete();
      });
      await done.future.timeout(const Duration(seconds: 25), onTimeout: () { stalled = true; });
      watchdog?.cancel();
      await sub.cancel();
      return classifyCurtain(receivedBytes: bytes, targetBytes: target, stalled: stalled);
    } catch (_) {
      return CurtainVerdict.blocked; // не смогли даже начать — считаем блоком пути
    } finally {
      try { c?.close(force: true); } catch (_) {}
    }
  }
}
