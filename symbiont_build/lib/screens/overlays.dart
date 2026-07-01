// lib/screens/overlays.dart — оверлеи Настройки, Словарь и Логи.
import 'package:flutter/material.dart';
import '../design/tokens.dart';
import 'package:flutter/services.dart';
import '../main.dart';
import '../theme.dart';
import '../style_engine.dart';
import '../engine/engine.dart';
import '../routing_catalog.dart';
import '../i18n/strings.dart';
import '../log.dart';

Widget _backbar(String title) => Padding(
  padding: const EdgeInsets.only(bottom: 16),
  child: Row(children: [
    _back(() => app.closeOverlay()),
    const SizedBox(width: 10),
    Text(title, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
  ]),
);

Widget _back(VoidCallback onTap) => Tooltip(message: app.tr('common.back'),
  child: Semantics(button: true, label: app.tr('common.back'), child: InkWell(
    borderRadius: BorderRadius.circular(11), onTap: onTap,
    child: SizedBox(width: 44, height: 44, child: Center(child: Container(width: 36, height: 36,
      decoration: BoxDecoration(color: const Color(0x0AFFFFFF), borderRadius: BorderRadius.circular(11), border: Border.all(color: K.line)),
      child: const Icon(Icons.chevron_left, size: 20, color: K.txt2)))),
  )),
);

// ── Настройки ─────────────────────────────────────────────────────────────────
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});
  @override
  Widget build(BuildContext context) {
    final p = app.protection;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      _backbar(app.tr('settings.title')),
      cardBox(padding: const EdgeInsets.symmetric(horizontal: 17), child: Column(children: [
        _toggle('ads', p.ads, app.toggleAds, last: false),
        _toggle('trackers', p.trackers, app.toggleTrackers, last: false),
        _toggle('phishing', p.phishing, app.togglePhishing, last: false),
        _toggle('killswitch', p.killSwitch, app.toggleKill, last: false),
        _dnsRow(p.dns),
      ])),
      const SizedBox(height: 10),
      // выбор, как показывать ошибки
      Padding(padding: const EdgeInsets.only(left: 4, bottom: 6),
        child: Text(app.tr('errd.title'), style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: K.txt))),
      Padding(padding: const EdgeInsets.only(left: 4, bottom: 8),
        child: Text(app.tr('errd.sub'), style: const TextStyle(fontSize: 11.5, color: K.muted))),
      cardBox(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6), child: Column(children: [
        for (final opt in const ['both', 'card', 'toast', 'none'])
          _errOpt(opt, last: opt == 'none'),
      ])),
      const SizedBox(height: 6),
      gradButton(app.tr('settings.gloss'), () => app.openOverlay('glossary'), ghost: true, icon: Icons.menu_book_outlined),
      const SizedBox(height: 8),
      gradButton(app.tr('faq.open'), () => app.openOverlay('faq'), ghost: true, icon: Icons.help_outline),
      const SizedBox(height: 8),
      gradButton(app.tr('logs.title'), () => app.openOverlay('logs'), ghost: true, icon: Icons.receipt_long_outlined),
    ]);
  }

  Widget _errOpt(String opt, {bool last = false}) {
    final active = app.errorDisplay == opt;
    return Semantics(inMutuallyExclusiveGroup: true, selected: active, button: true, child: InkWell(
      onTap: () => app.setErrorDisplay(opt),
      child: Container(
        decoration: BoxDecoration(border: last ? null : const Border(bottom: BorderSide(color: K.line))),
        padding: const EdgeInsets.symmetric(vertical: 13),
        child: Row(children: [
          Icon(active ? Icons.radio_button_checked : Icons.radio_button_unchecked,
            size: 19, color: active ? K.mint : K.muted),
          const SizedBox(width: 12),
          Expanded(child: Text(app.tr('errd.$opt'), style: TextStyle(fontSize: 13.5,
            fontWeight: active ? FontWeight.w600 : FontWeight.w400, color: active ? K.txt : K.txt2))),
        ]),
      ),
    ));
  }

  Widget _toggle(String key, bool on, VoidCallback onTap, {bool last = false}) => Container(
    decoration: BoxDecoration(border: last ? null : const Border(bottom: BorderSide(color: K.line))),
    padding: const EdgeInsets.symmetric(vertical: 15),
    child: Row(children: [
      Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(app.tr('set.$key.t'), style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
        const SizedBox(height: 2),
        Text(app.tr('set.$key.d'), style: const TextStyle(fontSize: 11.5, color: K.muted)),
      ])),
      _switch(on, onTap),
    ]),
  );

  Widget _switch(bool on, VoidCallback onTap) => Semantics(button: true, toggled: on, child: InkWell(
    borderRadius: BorderRadius.circular(99), onTap: onTap,
    child: AnimatedContainer(
      duration: Dur.short,
      width: 46, height: 27, padding: const EdgeInsets.all(2.5),
      decoration: BoxDecoration(gradient: on ? K.grad : null, color: on ? null : const Color(0x1FFFFFFF),
        borderRadius: BorderRadius.circular(99), border: Border.all(color: on ? Colors.transparent : K.line2)),
      child: Align(alignment: on ? Alignment.centerRight : Alignment.centerLeft,
        child: Container(width: 20, height: 20, decoration: BoxDecoration(color: on ? const Color(0xFF04201A) : const Color(0xFFCFD8E3), shape: BoxShape.circle))),
    ),
  ));

  Widget _dnsRow(String dns) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 15),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(app.tr('set.dns.t'), style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
      const SizedBox(height: 2),
      Text(app.tr('set.dns.d'), style: const TextStyle(fontSize: 11.5, color: K.muted)),
      const SizedBox(height: 10),
      Wrap(spacing: 8, runSpacing: 8, children: ['DoH', 'DoT', 'DoQ', 'ODoH'].map((o) {
        final on = dns == o;
        return Semantics(button: true, selected: on, inMutuallyExclusiveGroup: true, label: o, child: InkWell(
          borderRadius: BorderRadius.circular(8), onTap: () => app.setDns(o),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 8),
            decoration: BoxDecoration(gradient: on ? K.grad : null, borderRadius: BorderRadius.circular(8), border: Border.all(color: on ? Colors.transparent : K.line2)),
            child: Text(o, style: mono(size: 11.5, color: on ? const Color(0xFF04201A) : K.txt2)),
          ),
        ));
      }).toList()),
    ]),
  );
}

// ── Словарь ───────────────────────────────────────────────────────────────────
class GlossaryScreen extends StatefulWidget {
  const GlossaryScreen({super.key});
  @override
  State<GlossaryScreen> createState() => _GlossaryScreenState();
}

class _GlossaryScreenState extends State<GlossaryScreen> {
  String q = '';
  final Set<int> open = {};
  @override
  Widget build(BuildContext context) {
    final terms = T.gloss(app.lang);
    final filtered = <MapEntry<int, List<String>>>[];
    for (var i = 0; i < terms.length; i++) {
      final g = terms[i];
      if (q.isEmpty || g[0].toLowerCase().contains(q.toLowerCase()) || g[2].toLowerCase().contains(q.toLowerCase())) {
        filtered.add(MapEntry(i, g));
      }
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      _backbar(app.tr('glossary.title')),
      Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
        decoration: BoxDecoration(color: K.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: K.line2)),
        child: Row(children: [
          const Icon(Icons.search, size: 17, color: K.muted),
          const SizedBox(width: 9),
          Expanded(child: TextField(onChanged: (v) => setState(() => q = v),
            style: const TextStyle(color: K.txt, fontSize: 14),
            decoration: InputDecoration(border: InputBorder.none, hintText: app.tr('glossary.search'), hintStyle: const TextStyle(color: K.muted)))),
        ]),
      ),
      const SizedBox(height: 12),
      ...filtered.map((e) => _term(e.key, e.value)),
    ]);
  }

  Widget _term(int i, List<String> g) {
    final isOpen = open.contains(i);
    return Padding(padding: const EdgeInsets.only(bottom: 9), child: Semantics(button: true, expanded: isOpen, label: g[0], child: InkWell(
      borderRadius: BorderRadius.circular(14),
      onTap: () => setState(() => isOpen ? open.remove(i) : open.add(i)),
      child: Container(
        padding: const EdgeInsets.all(15),
        decoration: BoxDecoration(color: K.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: K.line)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Wrap(crossAxisAlignment: WrapCrossAlignment.center, spacing: 9, children: [
            Text(g[0], style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5)),
            Container(padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
              decoration: BoxDecoration(borderRadius: BorderRadius.circular(6), border: Border.all(color: K.line2)),
              child: Text('${app.tr('aka')} ${g[1]}', style: mono(size: 10.5, color: K.muted))),
          ]),
          if (isOpen) Padding(padding: const EdgeInsets.only(top: 8), child: Text(g[2], style: const TextStyle(fontSize: 12.5, color: K.txt2, height: 1.5))),
        ]),
      ),
    )));
  }
}

// ── FAQ / Частые вопросы ────────────────────────────────
// Частые вопросы с поиском и раскрытием. Данные — T.faq(lang): [вопрос, ответ];
// порядок задаёт английский «мастер», непереведённые языки берут английский.
class FaqScreen extends StatefulWidget {
  const FaqScreen({super.key});
  @override
  State<FaqScreen> createState() => _FaqScreenState();
}

class _FaqScreenState extends State<FaqScreen> {
  String q = '';
  final Set<int> open = {};
  @override
  Widget build(BuildContext context) {
    final items = T.faq(app.lang);
    final filtered = <MapEntry<int, List<String>>>[];
    for (var i = 0; i < items.length; i++) {
      final g = items[i];
      if (q.isEmpty || g[0].toLowerCase().contains(q.toLowerCase()) || g[1].toLowerCase().contains(q.toLowerCase())) {
        filtered.add(MapEntry(i, g));
      }
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      _backbar(app.tr('faq.title')),
      Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
        decoration: BoxDecoration(color: K.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: K.line2)),
        child: Row(children: [
          const Icon(Icons.search, size: 17, color: K.muted),
          const SizedBox(width: 9),
          Expanded(child: TextField(onChanged: (v) => setState(() => q = v),
            style: const TextStyle(color: K.txt, fontSize: 14),
            decoration: InputDecoration(border: InputBorder.none, hintText: app.tr('faq.search'), hintStyle: const TextStyle(color: K.muted)))),
        ]),
      ),
      const SizedBox(height: 12),
      ...filtered.map((e) => _qa(e.key, e.value)),
    ]);
  }

  Widget _qa(int i, List<String> g) {
    final isOpen = open.contains(i);
    return Padding(padding: const EdgeInsets.only(bottom: 9), child: Semantics(button: true, expanded: isOpen, label: g[0], child: InkWell(
      borderRadius: BorderRadius.circular(14),
      onTap: () => setState(() => isOpen ? open.remove(i) : open.add(i)),
      child: Container(
        padding: const EdgeInsets.all(15),
        decoration: BoxDecoration(color: K.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: K.line)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(child: Text(g[0], style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5, height: 1.35))),
            const SizedBox(width: 8),
            Icon(isOpen ? Icons.expand_less : Icons.expand_more, size: 19, color: K.muted),
          ]),
          if (isOpen) Padding(padding: const EdgeInsets.only(top: 9), child: Text(g[1], style: const TextStyle(fontSize: 12.5, color: K.txt2, height: 1.55))),
        ]),
      ),
    )));
  }
}

// ── Логи ──────────────────────────────────────────────────────────────────────
// Журнал событий движка. Пользователь жмёт «Скопировать», вставляет в чат — и по
// этим строкам видно, какой путь обхода выбран, поднялся ли winws, какие сайты
// открылись при подборе. Это наш главный инструмент диагностики «без ошибок».
class LogsScreen extends StatefulWidget {
  const LogsScreen({super.key});
  @override
  State<LogsScreen> createState() => _LogsScreenState();
}

class _LogsScreenState extends State<LogsScreen> {
  @override
  Widget build(BuildContext context) {
    final lines = Log.lines(last: 400);
    final text = lines.isEmpty ? app.tr('logs.empty') : lines.join('\n');
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      _backbar(app.tr('logs.title')),
      Text(app.tr('logs.sub'), style: const TextStyle(fontSize: 12, color: K.muted, height: 1.45)),
      const SizedBox(height: 8),
      cardBox(padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10), child: Row(children: [
        const Icon(Icons.folder_outlined, size: 16, color: K.muted),
        const SizedBox(width: 8),
        Expanded(child: SelectableText(Log.path, style: mono(size: 10.5, color: K.txt2))),
      ])),
      const SizedBox(height: 10),
      Row(children: [
        Expanded(child: gradButton(app.tr('logs.copy'), () async {
          await Clipboard.setData(ClipboardData(text: Log.dump()));
          if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(app.tr('logs.copied')), duration: const Duration(seconds: 2)));
        }, icon: Icons.copy_all)),
        const SizedBox(width: 10),
        Expanded(child: gradButton(app.tr('logs.refresh'), () => setState(() {}), icon: Icons.refresh, ghost: true)),
      ]),
      const SizedBox(height: 10),
      gradButton(app.tr('logs.clear'), () { Log.clear(); setState(() {}); }, icon: Icons.delete_outline, ghost: true),
      const SizedBox(height: 12),
      Container(
        constraints: const BoxConstraints(maxHeight: 360),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: const Color(0xFF0A0E14), borderRadius: BorderRadius.circular(12), border: Border.all(color: K.line)),
        child: SingleChildScrollView(
          reverse: true,
          child: SelectableText(text, style: mono(size: 10.5, color: K.txt2)),
        ),
      ),
    ]);
  }
}

/// Экран правил маршрутизации (per-site / per-app): для конкретного домена —
/// свой маршрут (через VPN / напрямую / блокировать). Всё во всех стилях.
class RulesScreen extends StatefulWidget {
  const RulesScreen({super.key});
  @override
  State<RulesScreen> createState() => _RulesScreenState();
}

class _RulesScreenState extends State<RulesScreen> {
  String _query = ''; // поиск по каталогу и приложениям
  String _cat = 'all';
  final Set<String> _expanded = {}; // какие сервисы раскрыты (по домену)

  String _actLabel(RouteAction a) => app.tr('route.${a.name}');
  Color _actColor(RouteAction a) {
    switch (a) {
      case RouteAction.tunnel: return K.blue;   // Через VPN
      case RouteAction.direct: return K.mint;   // Напрямую (работает и так)
      case RouteAction.bypass: return K.amber;  // Обход DPI
      case RouteAction.boost:  return const Color(0xFFB388FF); // Через ускорение (фиолетовый)
      case RouteAction.block:  return K.rose;   // Блокировать
    }
  }

  MatchKind _kindFor(String m) {
    if (m.startsWith('.')) return MatchKind.tld;
    if (m.startsWith('*.')) return MatchKind.domainSuffix;
    if (!m.contains('.')) return MatchKind.keyword;
    return MatchKind.domainExact;
  }

  // рекомендованное действие (ненавязчивый совет; решает пользователь).
  // По домену, а не грубо по категории: российские сервисы работают и без VPN,
  // заблокированные — нужен VPN. RuTube/VK/Яндекс НЕ надо «через VPN».
  RouteAction? _recommended(String domain, String category) {
    final d = domain.toLowerCase();
    // явно российские/локальные — лучше напрямую (VPN им не нужен и может мешать)
    const directDomains = {
      'rutube.ru', 'vk.com', 'vk.ru', 'vkontakte.ru', 'ok.ru', 'yandex.ru', 'ya.ru',
      'mail.ru', 'sberbank.ru', 'gosuslugi.ru', 'avito.ru', 'wildberries.ru', 'ozon.ru',
      'kinopoisk.ru', 'dzen.ru', 'tinkoff.ru', 'vtb.ru', 'alfabank.ru', 'mos.ru',
    };
    if (directDomains.contains(d) || category == 'bank') return RouteAction.direct;
    // типично заблокированные в РФ — нужен VPN
    const tunnelDomains = {
      'instagram.com', 'facebook.com', 'x.com', 'twitter.com', 'youtube.com',
      'telegram.org', 'discord.com', 'tiktok.com', 'netflix.com', 'spotify.com',
      'twitch.tv', 'soundcloud.com', 'deezer.com',
    };
    if (tunnelDomains.contains(d)) return RouteAction.tunnel;
    return null; // для остального — без навязчивого совета
  }

  @override
  Widget build(BuildContext context) {
    final q = _query.trim().toLowerCase();
    // каталог: фильтр по категории И по поиску
    final catalog = kRoutingCatalog.where((it) {
      final byCat = _cat == 'all' || it.category == _cat;
      final byQ = q.isEmpty || it.name.toLowerCase().contains(q) || it.domain.toLowerCase().contains(q);
      return byCat && byQ;
    }).toList();
    // приложения: фильтр по поиску
    final apps = app.scannedApps.where((a) =>
      q.isEmpty || a.name.toLowerCase().contains(q) || a.exe.toLowerCase().contains(q)).toList();
    // пользовательские правила вне каталога
    final catalogDomains = kRoutingCatalog.map((e) => e.domain).toSet();
    final custom = app.userRules.where((r) => !catalogDomains.contains(r.match) &&
      (q.isEmpty || r.match.toLowerCase().contains(q))).toList();

    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      // заголовок + кнопка "+"
      Padding(padding: const EdgeInsets.only(top: 4, bottom: 8),
        child: Row(children: [
          Expanded(child: Text(app.tr('rules.title'), style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: K.txt))),
          Semantics(button: true, label: app.tr('common.add'), child: _PressFX(onTap: _openAddSheet, child: Container(
            constraints: const BoxConstraints(minHeight: 44),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
            alignment: Alignment.center,
            decoration: BoxDecoration(gradient: K.grad, borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 12)),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              const Icon(Icons.add, size: 18, color: Color(0xFF04201A)),
              const SizedBox(width: 4),
              Text(app.tr('common.add'), style: const TextStyle(color: Color(0xFF04201A), fontWeight: FontWeight.w700, fontSize: 13)),
            ]),
          ))),
        ])),
      // поиск
      _searchField(),
      const SizedBox(height: 10),

      // ── СКАН ПРИЛОЖЕНИЙ — НАВЕРХУ ──
      Row(children: [
        Expanded(child: Text(app.tr('apps.title'), style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, color: K.txt))),
        if (app.scanningApps)
          const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 1.8, color: K.mint)),
      ]),
      Padding(padding: const EdgeInsets.only(top: 2, bottom: 8),
        child: Text(app.tr('apps.sub'), style: const TextStyle(fontSize: 11.5, color: K.muted, height: 1.4))),
      if (app.scannedApps.isEmpty && !app.scanningApps)
        _PressFX(onTap: () => app.scanApps(), child: gradButton(app.tr('apps.scan'), () => app.scanApps(), icon: Icons.search, ghost: true))
      else if (app.scanningApps)
        Padding(padding: const EdgeInsets.symmetric(vertical: 8),
          child: Text(app.tr('apps.scanning'), style: const TextStyle(fontSize: 12, color: K.muted)))
      else ...[
        ...apps.take(60).map((a) => _serviceRow('🖥️', a.name, a.exe, MatchKind.processId, category: 'app')),
        if (apps.length > 60)
          Padding(padding: const EdgeInsets.only(top: 2, bottom: 4),
            child: Text('+${apps.length - 60} ${app.tr('rules.more')}', style: const TextStyle(fontSize: 11.5, color: K.muted))),
        const SizedBox(height: 8),
        _PressFX(onTap: () => app.scanApps(), child: gradButton(app.tr('apps.rescan'), () => app.scanApps(), icon: Icons.refresh, ghost: true)),
      ],

      const SizedBox(height: 16),
      // ── КАТЕГОРИИ + КАТАЛОГ СЕРВИСОВ ──
      Text(app.tr('rules.services'), style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, color: K.txt)),
      const SizedBox(height: 8),
      SizedBox(height: 36, child: ListView(scrollDirection: Axis.horizontal, children: [
        for (final c in kRouteCategories) Padding(padding: const EdgeInsets.only(right: 8), child: _catChip(c)),
      ])),
      const SizedBox(height: 10),
      ...catalog.map((it) => _serviceRow(it.icon, it.name, it.domain, _kindFor(it.domain), category: it.category)),
      if (catalog.isEmpty && q.isNotEmpty)
        Padding(padding: const EdgeInsets.symmetric(vertical: 12),
          child: Text(app.tr('rules.noresults'), style: const TextStyle(fontSize: 12.5, color: K.muted))),

      // пользовательские правила
      if (custom.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text(app.tr('rules.custom'), style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: K.txt2)),
        const SizedBox(height: 8),
        ...custom.map((r) => _serviceRow('🌐', r.match, r.match, r.kind, category: 'custom')),
      ],

      // легенда цветов
      const SizedBox(height: 16),
      _colorLegend(),
      const SizedBox(height: 20),
    ]);
  }

  Widget _searchField() => TextField(
    onChanged: (v) => setState(() => _query = v),
    style: const TextStyle(fontSize: 13.5, color: K.txt),
    decoration: InputDecoration(
      hintText: app.tr('rules.search'),
      hintStyle: const TextStyle(color: K.muted, fontSize: 13),
      prefixIcon: const Icon(Icons.search, size: 18, color: K.muted),
      filled: true, fillColor: const Color(0x0AFFFFFF),
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 12), borderSide: const BorderSide(color: K.line)),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 12), borderSide: const BorderSide(color: K.line)),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 12), borderSide: const BorderSide(color: K.mint)),
    ),
  );

  Widget _catChip(String c) {
    final on = _cat == c;
    return _PressFX(onTap: () => setState(() => _cat = c), child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 7),
      decoration: BoxDecoration(
        gradient: on ? K.gradSoft : null, color: on ? null : const Color(0x0AFFFFFF),
        borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 20),
        border: Border.all(color: on ? K.mint.withOpacity(0.5) : K.line, width: Style.spec.borderWidth),
      ),
      child: Text(app.tr('cat.$c'), style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: on ? K.mint : K.txt2)),
    ));
  }

  // строка сервиса/приложения: иконка + имя + домен + рекомендация + выпадающая кнопка
  Widget _serviceRow(String icon, String name, String domain, MatchKind kind, {required String category}) {
    final cur = app.routeFor(domain);
    final rec = _recommended(domain, category);
    final label = cur == null ? app.tr('route.auto') : _actLabel(cur);
    final c = cur == null ? K.muted : _actColor(cur);
    final radius = BorderRadius.circular(Style.spec.radius == 0 ? 0 : 12);
    final isOpen = _expanded.contains(domain);
    return Padding(padding: const EdgeInsets.only(bottom: 6), child: _PressFX(
      onTap: () => setState(() { isOpen ? _expanded.remove(domain) : _expanded.add(domain); }),
      child: Container(
        decoration: BoxDecoration(
          color: Style.spec.surface,
          borderRadius: radius,
          // спокойная рамка; подсветка только у раскрытой строки (фокус)
          border: Border.all(color: isOpen ? c.withOpacity(0.45) : K.line, width: Style.spec.borderWidth),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        _ServiceIcon(emoji: icon, domain: domain, kind: kind, name: name),
        const SizedBox(width: 12),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Flexible(child: Text(name, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: K.txt))),
            if (rec != null && cur == null) ...[
              const SizedBox(width: 6),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(color: _actColor(rec).withOpacity(0.14), borderRadius: BorderRadius.circular(20)),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Icon(Icons.star, size: 9, color: _actColor(rec)), const SizedBox(width: 2),
                  Text(_actLabel(rec), style: TextStyle(fontSize: 9.5, color: _actColor(rec), fontWeight: FontWeight.w600)),
                ]),
              ),
            ],
          ]),
          const SizedBox(height: 1),
          Text(domain, overflow: TextOverflow.ellipsis, style: mono(size: 11, color: K.muted)),
        ])),
        PopupMenuButton<String>(
          color: Style.spec.surface,
          tooltip: app.tr('route.menu'),
          // ВАЖНО: value не может быть null — Flutter трактует null-выбор как отмену меню,
          // поэтому «Авто» представлено строкой 'auto', а действия — именами enum.
          onSelected: (v) {
            final a = v == 'auto' ? null : RouteAction.values.firstWhere((x) => x.name == v);
            app.setRouteFor(domain, a, kind); setState(() {});
          },
          itemBuilder: (_) => [
            PopupMenuItem<String>(value: 'auto', child: Row(children: [
              Container(width: 8, height: 8, decoration: const BoxDecoration(color: K.muted, shape: BoxShape.circle)),
              const SizedBox(width: 8), Text(app.tr('route.auto')),
            ])),
            ...RouteAction.values.map((a) => PopupMenuItem<String>(value: a.name,
              child: Row(children: [
                Container(width: 8, height: 8, decoration: BoxDecoration(color: _actColor(a), shape: BoxShape.circle)),
                const SizedBox(width: 8), Text(_actLabel(a)),
                if (a == rec) ...[const SizedBox(width: 6), const Icon(Icons.star, size: 11, color: K.amber)],
              ]))),
          ],
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
            decoration: BoxDecoration(
              color: c.withOpacity(0.14),
              borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 20),
              border: Border.all(color: c.withOpacity(0.5), width: Style.spec.borderWidth),
            ),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Text(label, style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: c)),
              const SizedBox(width: 4),
              Icon(isOpen ? Icons.expand_less : Icons.arrow_drop_down, size: 16, color: c),
            ]),
          ),
        ),
      ]),
      // ── раскрытые детали сервиса ──
      if (isOpen) _serviceDetails(name, domain, kind, category, cur, rec),
      ]),
    )));
  }

  // детальная карточка сервиса (раскрывается по тапу)
  Widget _serviceDetails(String name, String domain, MatchKind kind, String category, RouteAction? cur, RouteAction? rec) {
    final cleanDom = domain.replaceFirst('*.', '').replaceFirst(RegExp(r'^\.'), '');
    final isApp = kind == MatchKind.processId;
    return Padding(padding: const EdgeInsets.only(top: 12), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Divider(color: K.line, height: 1),
      const SizedBox(height: 12),
      // выбор действия чипами (быстрее, чем меню)
      Text(app.tr('det.action'), style: const TextStyle(fontSize: 11.5, color: K.muted)),
      const SizedBox(height: 6),
      Wrap(spacing: 6, runSpacing: 6, children: [
        _detActionChip(domain, kind, null, cur, rec),
        ...RouteAction.values.map((a) => _detActionChip(domain, kind, a, cur, rec)),
      ]),
      const SizedBox(height: 12),
      // охват (поддомены) — только для доменов, не для приложений
      if (!isApp) ...[
        Text(app.tr('det.coverage'), style: const TextStyle(fontSize: 11.5, color: K.muted)),
        const SizedBox(height: 4),
        Container(
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(color: const Color(0x0AFFFFFF), borderRadius: BorderRadius.circular(8)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            _covLine(cleanDom, app.tr('det.cov.main')),
            const SizedBox(height: 4),
            _covLine('*.$cleanDom', app.tr('det.cov.sub')),
          ]),
        ),
        const SizedBox(height: 6),
        Text(app.tr('det.cov.note'), style: const TextStyle(fontSize: 10.5, color: K.muted, height: 1.4, fontStyle: FontStyle.italic)),
        const SizedBox(height: 12),
      ],
      // удаление правила (если оно есть)
      if (cur != null)
        _PressFX(onTap: () { app.setRouteFor(domain, null, kind); setState(() {}); }, child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
          decoration: BoxDecoration(
            color: K.rose.withOpacity(0.10), borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 10),
            border: Border.all(color: K.rose.withOpacity(0.4)),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.delete_outline, size: 16, color: K.rose), const SizedBox(width: 6),
            Text(app.tr('det.remove'), style: const TextStyle(fontSize: 12.5, color: K.rose, fontWeight: FontWeight.w600)),
          ]),
        )),
    ]));
  }

  Widget _covLine(String dom, String label) => Row(children: [
    const Icon(Icons.check_circle_outline, size: 13, color: K.mint), const SizedBox(width: 6),
    Text(dom, style: mono(size: 11.5, color: K.txt)),
    const SizedBox(width: 6),
    Expanded(child: Text(label, style: const TextStyle(fontSize: 10.5, color: K.muted))),
  ]);

  Widget _detActionChip(String domain, MatchKind kind, RouteAction? a, RouteAction? cur, RouteAction? rec) {
    final on = cur == a;
    final col = a == null ? K.muted : _actColor(a);
    final lbl = a == null ? app.tr('route.auto') : _actLabel(a);
    return _PressFX(
      onTap: () { app.setRouteFor(domain, a, kind); setState(() {}); },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: on ? col.withOpacity(0.18) : const Color(0x0AFFFFFF),
          borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 20),
          border: Border.all(color: on ? col : K.line, width: on ? 2 : 1),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (a != null) Container(width: 7, height: 7, decoration: BoxDecoration(color: col, shape: BoxShape.circle)),
          if (a != null) const SizedBox(width: 5),
          Text(lbl, style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: on ? col : K.txt2)),
          if (a == rec) ...[const SizedBox(width: 4), const Icon(Icons.star, size: 10, color: K.amber)],
        ]),
      ),
    );
  }

  // легенда цветов действий
  Widget _colorLegend() => cardBox(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
    Text(app.tr('rules.legend'), style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: K.txt2)),
    const SizedBox(height: 8),
    Wrap(spacing: 12, runSpacing: 8, children: RouteAction.values.map((a) => Row(mainAxisSize: MainAxisSize.min, children: [
      Container(width: 9, height: 9, decoration: BoxDecoration(color: _actColor(a), shape: BoxShape.circle)),
      const SizedBox(width: 6),
      Text(_actLabel(a), style: const TextStyle(fontSize: 11.5, color: K.txt2)),
    ])).toList()),
  ]));

  // слой «+» : выбор Сайт / Обзор приложения
  void _openAddSheet() {
    showModalBottomSheet(context: context, backgroundColor: Colors.transparent, isScrollControlled: true,
      builder: (_) => _AddSheet(onAddSite: _addSite));
  }

  void _addSite(String domain, bool allSub, RouteAction action) {
    final m = normalizeDomain(domain);
    if (m.isEmpty) return;
    app.addRule(allSub ? MatchKind.domainSuffix : MatchKind.domainExact, m, action);
    setState(() {});
  }
}


/// Иконка сервиса: пытается показать настоящий фавикон сайта (через сеть),
/// при неудаче/загрузке — эмодзи-заглушка. Для приложений (processId) и
/// не-доменов сразу показывает эмодзи. Flutter кэширует картинку в памяти.
class _ServiceIcon extends StatelessWidget {
  final String emoji; final String domain; final MatchKind kind; final String name;
  const _ServiceIcon({required this.emoji, required this.domain, required this.kind, this.name = ''});

  bool get _isDomain =>
      kind != MatchKind.processId && domain.contains('.') && !domain.toLowerCase().endsWith('.exe');

  String get _cleanDomain => domain.replaceFirst('*.', '').replaceFirst(RegExp(r'^\.'), '');

  // Надёжный фолбэк: буквенный аватар (первая буква имени в цветном квадрате).
  // Эмодзи на Windows часто не рендерится (пустой квадрат), поэтому не полагаемся на него.
  Widget get _avatar {
    final base = (name.isNotEmpty ? name : domain).trim();
    final ch = base.isNotEmpty ? base.substring(0, 1).toUpperCase() : '•';
    const palette = [K.mint, K.aqua, K.blue, K.amber, K.rose];
    final c = palette[base.hashCode.abs() % palette.length];
    return Container(
      width: 28, height: 28,
      decoration: BoxDecoration(color: c.withOpacity(0.16), borderRadius: BorderRadius.circular(8),
        border: Border.all(color: c.withOpacity(0.35))),
      alignment: Alignment.center,
      child: Text(ch, style: TextStyle(fontSize: 14, fontWeight: FontWeight.w800, color: c)),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (!_isDomain) return _avatar;
    // фавикон для доменов; ошибка/загрузка → буквенный аватар
    final url = 'https://www.google.com/s2/favicons?sz=64&domain=$_cleanDomain';
    return ClipRRect(
      borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 8),
      child: Image.network(
        url, width: 28, height: 28, fit: BoxFit.cover,
        errorBuilder: (_, __, ___) => _avatar,
        loadingBuilder: (ctx, child, prog) => prog == null ? child : _avatar,
      ),
    );
  }
}

/// Комбо-эффект нажатия: лёгкое сжатие + кратковременная подсветка.
/// Единый отклик для всех кнопок/строк — сразу видно, что нажалось.
class _PressFX extends StatefulWidget {
  final Widget child; final VoidCallback onTap;
  const _PressFX({required this.child, required this.onTap});
  @override
  State<_PressFX> createState() => _PressFXState();
}

class _PressFXState extends State<_PressFX> {
  double _scale = 1.0; bool _glow = false;
  void _down() => setState(() { _scale = 0.96; _glow = true; });
  void _up() { setState(() { _scale = 1.0; }); Future.delayed(Dur.short, () { if (mounted) setState(() => _glow = false); }); }
  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapDown: (_) => _down(),
      onTapUp: (_) { _up(); widget.onTap(); },
      onTapCancel: _up,
      child: AnimatedScale(
        scale: _scale, duration: Dur.micro, curve: Ease.standard,
        child: AnimatedContainer(
          duration: Dur.short,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 14),
            boxShadow: _glow ? [BoxShadow(color: K.mint.withOpacity(0.25), blurRadius: 16, spreadRadius: 1)] : const [],
          ),
          child: widget.child,
        ),
      ),
    );
  }
}

/// Слой «+»: выбор «Добавить сайт» или «Обзор приложения».
class _AddSheet extends StatefulWidget {
  final void Function(String domain, bool allSub, RouteAction action) onAddSite;
  const _AddSheet({required this.onAddSite});
  @override
  State<_AddSheet> createState() => _AddSheetState();
}

class _AddSheetState extends State<_AddSheet> {
  final _ctrl = TextEditingController();
  bool _allSub = true;
  RouteAction _action = RouteAction.tunnel;
  bool _siteMode = false; // false = выбор типа, true = форма сайта

  @override
  void dispose() { _ctrl.dispose(); super.dispose(); }

  Color _ac(RouteAction a) {
    switch (a) {
      case RouteAction.tunnel: return K.blue;
      case RouteAction.direct: return K.mint;
      case RouteAction.bypass: return K.amber;
      case RouteAction.boost: return const Color(0xFFB388FF);
      case RouteAction.block: return K.rose;
    }
  }

  @override
  Widget build(BuildContext context) {
    final sp = Style.spec;
    return Container(
      padding: EdgeInsets.fromLTRB(18, 16, 18, MediaQuery.of(context).viewInsets.bottom + 24),
      decoration: BoxDecoration(
        color: sp.surface,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
        border: Border.all(color: K.line, width: sp.borderWidth),
      ),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Center(child: Container(width: 40, height: 4, decoration: BoxDecoration(color: K.line2, borderRadius: BorderRadius.circular(2)))),
        const SizedBox(height: 16),
        if (!_siteMode) ...[
          Text(app.tr('add.title'), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: K.txt)),
          const SizedBox(height: 4),
          Text(app.tr('add.sub'), style: const TextStyle(fontSize: 12, color: K.muted, height: 1.4)),
          const SizedBox(height: 16),
          _PressFX(onTap: () => setState(() => _siteMode = true), child: _choice(Icons.public, app.tr('add.site'), app.tr('add.site.sub'))),
          const SizedBox(height: 10),
          _PressFX(onTap: () {
            Navigator.pop(context);
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(app.tr('add.browse.soon')), duration: const Duration(seconds: 3)));
          }, child: _choice(Icons.folder_open, app.tr('add.browse'), app.tr('add.browse.sub'))),
        ] else ...[
          Row(children: [
            _PressFX(onTap: () => setState(() => _siteMode = false), child: const Padding(padding: EdgeInsets.all(4), child: Icon(Icons.arrow_back, size: 20, color: K.txt2))),
            const SizedBox(width: 8),
            Text(app.tr('add.site'), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: K.txt)),
          ]),
          const SizedBox(height: 14),
          TextField(
            controller: _ctrl, autofocus: true,
            onChanged: (_) => setState(() {}),
            style: const TextStyle(fontSize: 14, color: K.txt),
            decoration: InputDecoration(
              hintText: app.tr('add.site.hint'),
              hintStyle: const TextStyle(color: K.muted, fontSize: 13),
              filled: true, fillColor: const Color(0x0AFFFFFF),
              contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(sp.radius == 0 ? 0 : 10), borderSide: const BorderSide(color: K.line)),
              enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(sp.radius == 0 ? 0 : 10), borderSide: const BorderSide(color: K.line)),
              focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(sp.radius == 0 ? 0 : 10), borderSide: const BorderSide(color: K.mint)),
            ),
          ),
          // умные подсказки домена (из каталога + типичные)
          if (_ctrl.text.trim().isNotEmpty) ...[
            const SizedBox(height: 8),
            ...suggestDomains(_ctrl.text).map((s) => _PressFX(
              onTap: () => setState(() { _ctrl.text = s.domain; _ctrl.selection = TextSelection.collapsed(offset: s.domain.length); }),
              child: Container(
                margin: const EdgeInsets.only(bottom: 6),
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
                decoration: BoxDecoration(
                  color: const Color(0x0AFFFFFF),
                  borderRadius: BorderRadius.circular(sp.radius == 0 ? 0 : 10),
                  border: Border.all(color: K.line, width: sp.borderWidth),
                ),
                child: Row(children: [
                  Text(s.icon, style: const TextStyle(fontSize: 16)),
                  const SizedBox(width: 10),
                  Expanded(child: Text(s.name == s.domain ? s.domain : '${s.name}  ·  ${s.domain}',
                    style: const TextStyle(fontSize: 13, color: K.txt2))),
                  const Icon(Icons.north_west, size: 14, color: K.muted),
                ]),
              ),
            )),
          ],
          const SizedBox(height: 12),
          _PressFX(onTap: () => setState(() => _allSub = !_allSub), child: Row(children: [
            Icon(_allSub ? Icons.check_box : Icons.check_box_outline_blank, size: 20, color: _allSub ? K.mint : K.muted),
            const SizedBox(width: 10),
            Expanded(child: Text(app.tr('add.allsub'), style: const TextStyle(fontSize: 13, color: K.txt2, height: 1.3))),
          ])),
          const SizedBox(height: 14),
          Text(app.tr('add.action'), style: const TextStyle(fontSize: 12, color: K.muted)),
          const SizedBox(height: 6),
          Wrap(spacing: 8, runSpacing: 8, children: RouteAction.values.map((a) {
            final on = _action == a; final c = _ac(a);
            return _PressFX(onTap: () => setState(() => _action = a), child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
              decoration: BoxDecoration(
                color: on ? c.withOpacity(0.16) : const Color(0x0AFFFFFF),
                borderRadius: BorderRadius.circular(sp.radius == 0 ? 0 : 20),
                border: Border.all(color: on ? c : K.line, width: on ? 2 : 1),
              ),
              child: Text(app.tr('route.${a.name}'), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: on ? c : K.txt2)),
            ));
          }).toList()),
          const SizedBox(height: 16),
          _PressFX(onTap: () {
            widget.onAddSite(_ctrl.text, _allSub, _action);
            Navigator.pop(context);
          }, child: gradButton(app.tr('add.confirm'), () {
            widget.onAddSite(_ctrl.text, _allSub, _action);
            Navigator.pop(context);
          }, icon: Icons.check)),
        ],
      ]),
    );
  }

  Widget _choice(IconData icon, String title, String sub) => Container(
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: const Color(0x0AFFFFFF),
      borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 14),
      border: Border.all(color: K.line, width: Style.spec.borderWidth),
    ),
    child: Row(children: [
      Icon(icon, size: 24, color: K.mint),
      const SizedBox(width: 14),
      Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: K.txt)),
        const SizedBox(height: 2),
        Text(sub, style: const TextStyle(fontSize: 11.5, color: K.muted, height: 1.3)),
      ])),
      const Icon(Icons.chevron_right, size: 20, color: K.muted),
    ]),
  );
}
