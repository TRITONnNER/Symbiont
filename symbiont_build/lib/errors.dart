// lib/errors.dart — человеко-понятные ошибки.
// Каждая техническая ошибка движка/sing-box превращается в короткое понятное
// сообщение + (по запросу) полный технический текст. Так у пользователя любого
// уровня есть и ясность, и возможность увидеть детали для поддержки.
import 'i18n/strings.dart';

class FriendlyError {
  final String title;   // короткий заголовок (человеческий)
  final String message; // что произошло и что делать, простыми словами
  final String raw;     // полный технический текст (для «Подробнее»)
  final bool canRetry;  // имеет смысл кнопка «Повторить»
  final bool needAdmin; // нужна кнопка «Перезапустить от админа»
  const FriendlyError({required this.title, required this.message, required this.raw, this.canRetry = true, this.needAdmin = false});
}

/// Переводит сырой код/текст ошибки в понятную структуру.
/// lang — язык интерфейса ('ru'/'en' и т.д.), для локализованных формулировок.
FriendlyError humanizeError(String raw, String lang) {
  final low = raw.toLowerCase();
  String t(String key) => T.of(lang, key);

  // спец-коды нашего движка
  if (raw == 'no_config') {
    return FriendlyError(title: t('err.title'), message: t('err.no_config'), raw: raw, canRetry: false);
  }
  if (raw == 'no_engine') {
    return FriendlyError(title: t('err.title'), message: t('err.no_engine'), raw: raw, canRetry: false);
  }
  if (raw == 'need_admin' || low.contains('админ') || low.contains('administrator')) {
    return FriendlyError(title: t('errh.admin.t'), message: t('errh.admin.m'), raw: raw, needAdmin: true);
  }

  // типичные ошибки sing-box (по ключевым словам в тексте)
  if (low.contains('only one usage') || low.contains('bind:') || (low.contains('listen') && low.contains('address'))) {
    // порт занят (обычно старый процесс)
    return FriendlyError(title: t('errh.port.t'), message: t('errh.port.m'), raw: raw);
  }
  if (low.contains('cache') && low.contains('timeout')) {
    return FriendlyError(title: t('errh.cache.t'), message: t('errh.cache.m'), raw: raw);
  }
  if (low.contains('rule-set') || low.contains('rule_set') || low.contains('raw.githubusercontent')) {
    return FriendlyError(title: t('errh.ruleset.t'), message: t('errh.ruleset.m'), raw: raw);
  }
  if (low.contains('context deadline exceeded') || low.contains('timeout') || low.contains('i/o timeout')) {
    return FriendlyError(title: t('errh.timeout.t'), message: t('errh.timeout.m'), raw: raw);
  }
  if (low.contains('connection refused') || low.contains('no route') || low.contains('unreachable') || low.contains('dial')) {
    return FriendlyError(title: t('errh.unreach.t'), message: t('errh.unreach.m'), raw: raw);
  }
  if (low.contains('decode config') || low.contains('parse') || low.contains('deprecated') || low.contains('initialize') || low.contains('fatal') || low.contains('detour')) {
    return FriendlyError(title: t('errh.config.t'), message: t('errh.config.m'), raw: raw);
  }
  if (low.contains('tls') || low.contains('handshake') || low.contains('certificate')) {
    return FriendlyError(title: t('errh.tls.t'), message: t('errh.tls.m'), raw: raw);
  }

  // неизвестная ошибка — честно говорим, что что-то пошло не так, и даём детали
  return FriendlyError(title: t('errh.unknown.t'), message: t('errh.unknown.m'), raw: raw);
}
