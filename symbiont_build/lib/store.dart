// lib/store.dart
// Постоянное хранилище на диск (shared_preferences). Хранит то, что должно
// переживать перезапуск: токен аккаунта, метку, план, язык, режим охвата,
// избранные узлы, настройки защиты, факт прохождения онбординга.
//
// Личные данные НЕ храним: токен — непрозрачный идентификатор от бэкенда,
// метка — косметический ярлык. Никаких e-mail/телефонов.
import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

class Store {
  static late SharedPreferences _p;
  static Future<void> init() async { _p = await SharedPreferences.getInstance(); }

  // строки
  static String? get token => _p.getString('token');
  static set token(String? v) => v == null ? _p.remove('token') : _p.setString('token', v);

  static String get label => _p.getString('label') ?? '';
  static set label(String v) => _p.setString('label', v);

  static String get lang => _p.getString('lang') ?? 'ru';
  static set lang(String v) => _p.setString('lang', v);

  static String? get lastNodeId => _p.getString('lastNodeId');
  static set lastNodeId(String? v) => v == null ? _p.remove('lastNodeId') : _p.setString('lastNodeId', v);

  static String get protoChoice => _p.getString('protoChoice') ?? 'auto';
  static set protoChoice(String v) => _p.setString('protoChoice', v);

  /// Версия манифеста, которую клиент РЕАЛЬНО применил. Монотонна: применяем только
  /// строго бОльшую (защита от отката на старый подписанный манифест — blueprint).
  static int get appliedManifestVersion => _p.getInt('appliedManifestVersion') ?? 0;
  static set appliedManifestVersion(int v) => _p.setInt('appliedManifestVersion', v);

  static String get style => _p.getString('style') ?? 'A';
  static set style(String v) => _p.setString('style', v);

  // Как показывать ошибки: 'both' | 'card' | 'toast' | 'none'
  static String get errorDisplay => _p.getString('errorDisplay') ?? 'both';
  static set errorDisplay(String v) => _p.setString('errorDisplay', v);

  static String get plan => _p.getString('plan') ?? 'free';
  static set plan(String v) => _p.setString('plan', v);

  static String? get paidUntil => _p.getString('paidUntil');
  static set paidUntil(String? v) => v == null ? _p.remove('paidUntil') : _p.setString('paidUntil', v);

  static String get mode => _p.getString('mode') ?? 'smart';
  static set mode(String v) => _p.setString('mode', v);

  static String? get baseUrl => _p.getString('baseUrl');
  static set baseUrl(String? v) => v == null ? _p.remove('baseUrl') : _p.setString('baseUrl', v);

  // флаги
  static bool get onboarded => _p.getBool('onboarded') ?? false;
  static set onboarded(bool v) => _p.setBool('onboarded', v);

  // защита (json)
  static Map<String, dynamic> get protection {
    final s = _p.getString('protection');
    if (s == null) return {};
    try { return jsonDecode(s) as Map<String, dynamic>; } catch (_) { return {}; }
  }
  static set protection(Map<String, dynamic> v) => _p.setString('protection', jsonEncode(v));

  // избранные узлы (множество id)
  static Set<String> get favorites => (_p.getStringList('favorites') ?? const []).toSet();
  static set favorites(Set<String> v) => _p.setStringList('favorites', v.toList());

  // пользовательские правила (json-список)
  static List<Map<String, dynamic>> get rules {
    final s = _p.getString('rules');
    if (s == null) return [];
    try { return (jsonDecode(s) as List).cast<Map<String, dynamic>>(); } catch (_) { return []; }
  }
  static set rules(List<Map<String, dynamic>> v) => _p.setString('rules', jsonEncode(v));

  /// Полный сброс (выход из аккаунта).
  static Future<void> clear() async { await _p.clear(); }
}
