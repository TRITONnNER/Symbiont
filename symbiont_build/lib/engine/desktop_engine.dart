// lib/engine/desktop_engine.dart
// Условный выбор реализации десктоп-движка: на native — процессный sing-box,
// на web — заглушка (возвращает null → MockEngine).
export 'desktop_engine_stub.dart' if (dart.library.io) 'desktop_engine_io.dart';
