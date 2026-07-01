// lib/net/probe_web.dart — заглушка для web (нет raw-сокетов/процессов).
import 'curtain.dart';

class Probe {
  static Future<int?> tcpPing(String host, int port,
      {Duration timeout = const Duration(seconds: 2)}) async => null;
  static Future<int?> median(String host, int port, {int samples = 3}) async => null;
  static Future<int?> directPing() async => null;
  static Future<double?> lossPct(String host, int port, {int samples = 6}) async => null;
  static Future<Map<String, String>> publicInfo() async => const {};
  static Future<String> checkHost(String host) async => 'ok';
  static Future<double?> downloadMbps() async => null;
  static Future<CurtainVerdict> probeCurtain({String? url, int target = 64 * 1024, int stallSecs = 4}) async => CurtainVerdict.clear;
}
