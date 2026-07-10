// sym_web_server.dart — условный выбор реализации.
// На десктопе/мобиле — реальный loopback-сервер (dart:io). На web — заглушка,
// чтобы `flutter build web` компилировался (там оболочка и так открывается
// напрямую, WebView не нужен).
export 'sym_web_server_stub.dart'
    if (dart.library.io) 'sym_web_server_io.dart';
