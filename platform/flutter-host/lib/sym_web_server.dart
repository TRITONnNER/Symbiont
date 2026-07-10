// sym_web_server.dart
//
// Крошечный loopback-HTTP-сервер, раздающий вшитую оболочку (assets/webapp/).
// Рантайм Design-Components грузит компоненты/шрифты/флаги через fetch —
// поэтому нужен именно HTTP (а не file://), как и serve.py в webapp/.

import 'dart:async';
import 'dart:io';
import 'dart:typed_data';
import 'package:flutter/services.dart' show rootBundle;

class SymWebServer {
  HttpServer? _server;
  int get port => _server?.port ?? 0;
  String get baseUrl => 'http://127.0.0.1:$port';

  static const _root = 'assets/webapp'; // каталог вшитой оболочки в pubspec assets

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
    await _server?.close(force: true);
    _server = null;
  }

  Future<void> _handle(HttpRequest req) async {
    var path = Uri.decodeComponent(req.uri.path);
    if (path == '/' || path.isEmpty) path = '/Симбионт.dc.html';
    final asset = '$_root$path';
    try {
      final ByteData data = await rootBundle.load(asset);
      final ext = path.contains('.') ? path.split('.').last.toLowerCase() : '';
      req.response.headers.contentType =
          ContentType.parse(_types[ext] ?? 'application/octet-stream');
      // Оболочка полностью локальна; заголовки — как у статики.
      req.response.add(data.buffer.asUint8List());
    } catch (_) {
      req.response.statusCode = HttpStatus.notFound;
    }
    await req.response.close();
  }
}
