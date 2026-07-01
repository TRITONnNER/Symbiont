// lib/models/models.dart
//
// Продуктовые модели (тома V–VI): аккаунт без личных данных, подписка,
// ключи активации, диалог поддержки. Хранятся локально; на сервер уходит
// только непрозрачный токен (см. backend/API.md).

enum Plan { free, trial, pro }

/// Аккаунт «придумай что угодно»: label — косметический ярлык (ник/ID/что угодно),
/// token — внутренний непрозрачный идентификатор (уникальность держит он, не label).
class Account {
  final String token;   // напр. base32, выдаётся бэкендом; не e-mail/телефон
  String label;         // что ввёл пользователь; формат не проверяем
  Subscription subscription;
  Account({required this.token, required this.label, required this.subscription});

  factory Account.fromJson(Map<String, dynamic> j) => Account(
    token: j['token'], label: j['label'] ?? 'guest',
    subscription: Subscription.fromJson(j['subscription'] ?? const {}),
  );
  Map<String, dynamic> toJson() => {
    'token': token, 'label': label, 'subscription': subscription.toJson(),
  };
}

class Subscription {
  final Plan plan;
  final DateTime? paidUntil;
  /// «без лимита устройств»: ограничиваем одновременные сессии, не устройства.
  final int maxConcurrentSessions;
  const Subscription({this.plan = Plan.free, this.paidUntil, this.maxConcurrentSessions = 5});

  bool get active => plan != Plan.free &&
      (paidUntil == null || paidUntil!.isAfter(DateTime.now()));

  factory Subscription.fromJson(Map<String, dynamic> j) => Subscription(
    plan: Plan.values.byName(j['plan'] ?? 'free'),
    paidUntil: j['paidUntil'] != null ? DateTime.tryParse(j['paidUntil']) : null,
    maxConcurrentSessions: j['maxConcurrentSessions'] ?? 5,
  );
  Map<String, dynamic> toJson() => {
    'plan': plan.name,
    if (paidUntil != null) 'paidUntil': paidUntil!.toIso8601String(),
    'maxConcurrentSessions': maxConcurrentSessions,
  };
}

/// Ключ активации («предъявителя»). Подделка исключена (подпись+реестр),
/// кража только смягчается (bind-on-redeem + одноразовость + отзыв) — см. том VI §4.
class ActivationKey {
  final String code;     // напр. SYMB-XXXX-XXXX-XXXX (содержит подпись)
  const ActivationKey(this.code);

  /// Лёгкая клиентская проверка формата (валидность — на сервере).
  bool get looksValid =>
      code.replaceAll(RegExp(r'[^A-Za-z0-9]'), '').length >= 12;
}

class SupportMessage {
  final String from;     // "user" | "support" | "system"
  final String text;
  final DateTime at;
  const SupportMessage({required this.from, required this.text, required this.at});

  factory SupportMessage.fromJson(Map<String, dynamic> j) => SupportMessage(
    from: j['from'], text: j['text'], at: DateTime.parse(j['at']),
  );
}

/// Диалог поддержки привязан к токену аккаунта — личные данные не нужны (том VI §7).
class SupportThread {
  final String accountToken;
  final List<SupportMessage> messages;
  const SupportThread({required this.accountToken, required this.messages});
}
