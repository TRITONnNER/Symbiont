// Заглушка для web-таргета: WebShellPage там не используется.
class SymWebServer {
  SymWebServer(String backendBase);
  int get port => 0;
  String get baseUrl => '';
  Future<void> start() async {}
  Future<void> stop() async {}
}
