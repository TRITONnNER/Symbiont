// sym_web_server_io.dart
//
// Loopback-HTTP-сервер для WebView: раздаёт вшитую оболочку (assets/webapp/) и
// проксирует /v1/* на бэкенд. За счёт прокси оболочка и API — на ОДНОМ origin,
// поэтому SYM_CONFIG.apiBase='' и CORS не участвует (как в web-portal).
//
// Рантайму Design-Components нужен именно HTTP (компоненты/шрифты грузятся
// через fetch), поэтому file:// не подходит — отсюда локальный сервер.

import 'dart:async';
import 'dart:io';
import 'package:flutter/services.dart' show rootBundle;

class SymWebServer {
  final String backendBase; // куда проксировать /v1 ('' → прокси выключен)
  HttpServer? _server;
  final HttpClient _client = HttpClient();

  SymWebServer(this.backendBase);

  int get port => _server?.port ?? 0;
  String get baseUrl => 'http://127.0.0.1:$port';

  static const _root = 'assets/webapp';

  static const _types = {
    'html': 'text/html; charset=utf-8',
    'js': 'application/javascript; charset=utf-8',
    'css': 'text/css; charset=utf-8',
    'json': 'application/json; charset=utf-8',
    'png': 'image/png',
    'svg': 'image/svg+xml',
    'woff2': 'font/woff2',
    'woff': 'font/woff',
    'ttf': 'font/ttf',
  };

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0, shared: true);
    _server!.listen(_handle);
  }

  Future<void> stop() async {
    _client.close(force: true);
    await _server?.close(force: true);
    _server = null;
  }

  Future<void> _handle(HttpRequest req) async {
    try {
      if (req.uri.path.startsWith('/v1/') && backendBase.isNotEmpty) {
        await _proxy(req);
      } else {
        await _static(req);
      }
    } catch (_) {
      req.response.statusCode = HttpStatus.internalServerError;
      await req.response.close();
    }
  }

  Future<void> _static(HttpRequest req) async {
    var path = Uri.decodeComponent(req.uri.path);
    if (path == '/' || path.isEmpty) path = '/Симбионт.dc.html';
    final asset = '$_root$path';
    try {
      final data = await rootBundle.load(asset);
      final ext = path.contains('.') ? path.split('.').last.toLowerCase() : '';
      req.response.headers.contentType =
          ContentType.parse(_types[ext] ?? 'application/octet-stream');
      // ВАЖНО: отдаём ТОЧНЫЙ срез ассета, а не весь backing-буфер. В release
      // rootBundle может вернуть ByteData как подвью общего буфера (offset>0 или
      // length<buffer): asUint8List() без границ вернул бы ЛИШНИЕ байты → контент
      // «с хвостом». Для файлов с SRI (react/react-dom) это ломает проверку
      // integrity → браузер отвергает скрипт, и оболочка не грузит React.
      req.response.add(data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes));
    } catch (_) {
      req.response.statusCode = HttpStatus.notFound;
    }
    await req.response.close();
  }

  Future<void> _proxy(HttpRequest req) async {
    final target = Uri.parse('$backendBase${req.uri.path}'
        '${req.uri.hasQuery ? '?${req.uri.query}' : ''}');
    final pr = await _client.openUrl(req.method, target);
    // проброс заголовков клиента (тип тела, авторизация)
    for (final h in const ['content-type', 'authorization']) {
      final v = req.headers.value(h);
      if (v != null) pr.headers.set(h, v);
    }
    if (req.method == 'POST' || req.method == 'PATCH' || req.method == 'PUT') {
      await pr.addStream(req);
    }
    final resp = await pr.close();
    req.response.statusCode = resp.statusCode;
    final ct = resp.headers.contentType;
    if (ct != null) req.response.headers.contentType = ct;
    await resp.pipe(req.response);
  }
}
