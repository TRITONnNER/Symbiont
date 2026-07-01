// lib/log_web.dart — журнал для web: память + печать в консоль браузера.
class Log {
  static final List<String> _buf = [];
  static String get path => '';
  static void w(String tag, String msg) {
    final ts = DateTime.now().toIso8601String().substring(11, 23);
    final line = '${DateTime.now().toIso8601String()}  [$tag] $msg';
    _buf.add(line);
    if (_buf.length > 1000) _buf.removeAt(0);
    // ignore: avoid_print
    print('SYMB $ts [$tag] $msg');
  }
  static void e(String tag, String msg, [Object? err, StackTrace? st]) {
    w('ERROR:$tag', err != null ? '$msg | $err' : msg);
    // ignore: avoid_print
    if (st != null) print('SYMB     $st');
  }
  static Future<void> env() async { w('env', 'платформа: web/браузер'); }
  static List<String> lines({int last = 400}) =>
      _buf.length <= last ? List.of(_buf) : _buf.sublist(_buf.length - last);
  static String dump() => lines().join('\n');
  static void clear() => _buf.clear();
}
