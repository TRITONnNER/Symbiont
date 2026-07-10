// lib/log_io.dart — журнал событий на native. Пишет построчно в файл и в память.
// Файл: <APPDATA|HOME>/Symbiont/symbiont.log — его пользователь присылает для разбора.
import 'dart:io';

class Log {
  static final List<String> _buf = [];
  static File? _file;
  static bool _init = false;

  static String get path {
    final sep = Platform.pathSeparator;
    final base = Platform.isWindows
        ? (Platform.environment['APPDATA'] ?? Directory.systemTemp.path)
        : (Platform.environment['HOME'] ?? Directory.systemTemp.path);
    return '$base${sep}Symbiont${sep}symbiont.log';
  }

  static void _ensure() {
    if (_init) return;
    _init = true;
    try {
      final f = File(path);
      f.parent.createSync(recursive: true);
      // ротация: если файл больше ~512 КБ — начинаем заново
      if (f.existsSync() && f.lengthSync() > 512 * 1024) f.writeAsStringSync('');
      _file = f;
      final head = '\n===== Симбионт запущен ${DateTime.now().toIso8601String()} '
          '(${Platform.operatingSystem} ${Platform.operatingSystemVersion}) =====\n';
      f.writeAsStringSync(head, mode: FileMode.append, flush: true);
    } catch (_) { _file = null; }
    // баннер в консоль flutter run
    _console('═══════════════════════════════════════════════════════════════');
    _console('SYMB  Симбионт — журнал в консоли (${Platform.operatingSystem})');
    _console('SYMB  файл лога: $path');
    _console('═══════════════════════════════════════════════════════════════');
  }

  // Печать строки в stdout (консоль `flutter run` в PowerShell) — наши «глаза».
  static void _console(String s) {
    try { stdout.writeln(s); } catch (_) {
      // ignore: avoid_print
      try { print(s); } catch (_) {}
    }
  }

  /// Записать строку журнала: время + тег + сообщение. Дублируется в консоль.
  static void w(String tag, String msg) {
    _ensure();
    final ts = DateTime.now().toIso8601String().substring(11, 23); // только время HH:MM:SS.mmm
    final line = '${DateTime.now().toIso8601String()}  [$tag] $msg';
    _buf.add(line);
    if (_buf.length > 2000) _buf.removeAt(0);
    _console('SYMB $ts [$tag] $msg'); // в PowerShell
    try { _file?.writeAsStringSync('$line\n', mode: FileMode.append, flush: true); } catch (_) {}
  }

  /// Ошибка с (опционально) объектом и стеком — печатается заметно.
  static void e(String tag, String msg, [Object? err, StackTrace? st]) {
    w('ERROR:$tag', err != null ? '$msg | $err' : msg);
    if (st != null) {
      for (final l in st.toString().split('\n').take(12)) {
        if (l.trim().isNotEmpty) _console('SYMB     $l');
      }
    }
  }

  /// Полный снимок окружения для отладки (ОС, локаль, железо, сеть, IP-интерфейсы).
  /// Зовём при старте — чтобы в логе сразу было «на чём и в каких условиях запущено».
  static Future<void> env() async {
    _ensure();
    try {
      w('env', 'ОС: ${Platform.operatingSystem} ${Platform.operatingSystemVersion}');
      w('env', 'локаль=${Platform.localeName}  CPU=${Platform.numberOfProcessors}  '
          'dart=${Platform.version.split(' ').first}');
      w('env', 'хост=${Platform.localHostname}');
      w('env', 'exe=${Platform.resolvedExecutable}');
      w('env', 'путь лога=$path');
    } catch (e) { w('env', 'снимок ОС частично: $e'); }
    try {
      final ifs = await NetworkInterface.list(includeLoopback: false, type: InternetAddressType.any);
      for (final i in ifs) {
        for (final a in i.addresses) {
          w('net', 'интерфейс ${i.name}: ${a.address} (${a.type.name})');
        }
      }
      if (ifs.isEmpty) w('net', 'сетевых интерфейсов не найдено');
    } catch (e) { w('net', 'список интерфейсов не получен: $e'); }
  }

  static List<String> lines({int last = 400}) =>
      _buf.length <= last ? List.of(_buf) : _buf.sublist(_buf.length - last);

  static String dump() {
    try {
      final f = File(path);
      if (f.existsSync()) return f.readAsStringSync();
    } catch (_) {}
    return lines().join('\n');
  }

  static void clear() {
    _buf.clear();
    try { File(path).writeAsStringSync(''); } catch (_) {}
  }
}
