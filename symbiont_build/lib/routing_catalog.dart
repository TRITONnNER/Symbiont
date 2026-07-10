// lib/routing_catalog.dart — готовый каталог популярных сервисов/приложений
// для экрана маршрутизации. Иконка (эмодзи), домен и категория. Пользователь
// видит понятный список (как узлы) и задаёт маршрут выпадающей кнопкой.
// Реальный скан установленных приложений добавляется отдельным модулем.

class CatalogItem {
  final String name;     // отображаемое имя
  final String icon;     // эмодзи-иконка (как флаги у узлов)
  final String domain;   // домен для правила (match)
  final String category; // ключ категории
  const CatalogItem(this.name, this.icon, this.domain, this.category);
}

// категории (ключи; названия — в i18n cat.*)
const List<String> kRouteCategories = ['all', 'social', 'video', 'messenger', 'game', 'bank', 'other'];

const List<CatalogItem> kRoutingCatalog = [
  // соцсети
  CatalogItem('Instagram', '📷', 'instagram.com', 'social'),
  CatalogItem('Facebook', '📘', 'facebook.com', 'social'),
  CatalogItem('X (Twitter)', '🐦', 'x.com', 'social'),
  CatalogItem('TikTok', '🎵', 'tiktok.com', 'social'),
  CatalogItem('Reddit', '👽', 'reddit.com', 'social'),
  CatalogItem('LinkedIn', '💼', 'linkedin.com', 'social'),
  // видео
  CatalogItem('YouTube', '▶️', 'youtube.com', 'video'),
  CatalogItem('Twitch', '🟣', 'twitch.tv', 'video'),
  CatalogItem('Netflix', '🎬', 'netflix.com', 'video'),
  CatalogItem('Rutube', '📺', 'rutube.ru', 'video'),
  CatalogItem('Vimeo', '🎞️', 'vimeo.com', 'video'),
  // мессенджеры
  CatalogItem('Telegram', '✈️', 'telegram.org', 'messenger'),
  CatalogItem('WhatsApp', '💬', 'whatsapp.com', 'messenger'),
  CatalogItem('Discord', '🎮', 'discord.com', 'messenger'),
  CatalogItem('Signal', '🔒', 'signal.org', 'messenger'),
  CatalogItem('Viber', '🟪', 'viber.com', 'messenger'),
  // игры/игровые сервисы
  CatalogItem('Steam', '🎯', 'steampowered.com', 'game'),
  CatalogItem('Epic Games', '🛡️', 'epicgames.com', 'game'),
  CatalogItem('Riot / LoL', '⚔️', 'riotgames.com', 'game'),
  CatalogItem('Roblox', '🧱', 'roblox.com', 'game'),
  CatalogItem('Battle.net', '🔥', 'battle.net', 'game'),
  CatalogItem('PlayStation', '🎮', 'playstation.com', 'game'),
  // банки / госуслуги (обычно — напрямую)
  CatalogItem('Сбербанк', '🟢', 'sberbank.ru', 'bank'),
  CatalogItem('Тинькофф', '🟡', 'tbank.ru', 'bank'),
  CatalogItem('Альфа-Банк', '🔴', 'alfabank.ru', 'bank'),
  CatalogItem('ВТБ', '🔵', 'vtb.ru', 'bank'),
  CatalogItem('Госуслуги', '🏛️', 'gosuslugi.ru', 'bank'),
  CatalogItem('Мир / НСПК', '🗺️', 'nspk.ru', 'bank'),
  // прочее популярное
  CatalogItem('Google', '🔎', 'google.com', 'other'),
  CatalogItem('GitHub', '🐙', 'github.com', 'other'),
  CatalogItem('Spotify', '🎧', 'spotify.com', 'other'),
  CatalogItem('Wikipedia', '📚', 'wikipedia.org', 'other'),
  CatalogItem('ChatGPT', '🤖', 'openai.com', 'other'),
];

/// Извлекает чистый домен из любой пользовательской строки:
/// "https://www.youtube.com/watch?v=x" → "youtube.com"
/// "youtube,ru" → "youtube.ru" ; "  ВК.РУ/feed " → "вк.ру"
String normalizeDomain(String input) {
  var s = input.trim().toLowerCase();
  if (s.isEmpty) return '';
  s = s.replaceAll(',', '.');                 // частая опечатка: запятая вместо точки
  s = s.replaceFirst(RegExp(r'^[a-z]+://'), ''); // убрать схему http:// https:// и пр.
  s = s.replaceFirst(RegExp(r'^www\.'), '');  // убрать www.
  s = s.split('/').first;                      // отрезать путь
  s = s.split('?').first;                      // отрезать query
  s = s.split('#').first;                      // отрезать якорь
  s = s.split(':').first;                      // отрезать порт
  s = s.replaceAll(RegExp(r'\s+'), '');        // убрать пробелы
  s = s.replaceFirst(RegExp(r'^\*\.'), '');    // убрать ведущий *.
  s = s.replaceFirst(RegExp(r'^\.+'), '');     // убрать ведущие точки
  s = s.replaceFirst(RegExp(r'\.+$'), '');     // убрать хвостовые точки
  return s;
}

/// Подсказки домена по тексту: ищет в каталоге (по имени и домену) + типичные.
List<CatalogItem> suggestDomains(String query) {
  final q = query.trim().toLowerCase();
  if (q.isEmpty) return const [];
  final out = <CatalogItem>[];
  // 1) совпадения по каталогу (имя или домен)
  for (final it in kRoutingCatalog) {
    if (it.name.toLowerCase().contains(q) || it.domain.toLowerCase().contains(q)) out.add(it);
  }
  // 2) типичные русские названия → домены
  const aliases = <String, String>{
    'ютуб': 'youtube.com', 'ютьюб': 'youtube.com', 'твич': 'twitch.tv',
    'инст': 'instagram.com', 'инста': 'instagram.com', 'тг': 'telegram.org',
    'телега': 'telegram.org', 'телеграм': 'telegram.org', 'вк': 'vk.com',
    'вконтакте': 'vk.com', 'фейсбук': 'facebook.com', 'дискорд': 'discord.com',
    'сбер': 'sberbank.ru', 'госуслуги': 'gosuslugi.ru', 'твиттер': 'x.com',
    'икс': 'x.com', 'тикток': 'tiktok.com', 'нетфликс': 'netflix.com',
    'спотифай': 'spotify.com', 'гугл': 'google.com', 'гитхаб': 'github.com',
  };
  aliases.forEach((alias, dom) {
    if (alias.contains(q) || q.contains(alias)) {
      if (!out.any((e) => e.domain == dom)) out.add(CatalogItem(dom, '🌐', dom, 'other'));
    }
  });
  return out.take(6).toList();
}
