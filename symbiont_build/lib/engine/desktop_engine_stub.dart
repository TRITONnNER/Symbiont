// lib/engine/desktop_engine_stub.dart — для web: процессов/dart:io нет,
// поэтому реального десктоп-движка нет. Тот же API, что в desktop_engine_io.dart.
import 'engine.dart';

SymbiontEngine? createDesktopEngine() => null;
