// lib/net/probe.dart
// Реальный сетевой замер (пинг/потери) через TCP-connect. Условный импорт:
// на native (Windows/Android/iOS/Linux/macOS) — настоящая реализация на dart:io;
// на web — заглушка (raw-сокетов в браузере нет), чтобы код компилировался.
export 'probe_web.dart' if (dart.library.io) 'probe_io.dart';
