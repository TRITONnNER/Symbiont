// lib/app_shell.dart — адаптивная оболочка приложения.
// Раскладка зависит от Responsive (пересчитывается при ресайзе/фуллскрине):
//   • expanded (≥960px): боковой rail + topbar + центрированный контент;
//   • compact/medium (<960px): topbar + контент + нижние вкладки.
// Контент всегда центрирован и ограничен по ширине (читаемость на 4K).
import 'package:flutter/material.dart';
import 'main.dart';
import 'theme.dart';
import 'style_engine.dart';
import 'store.dart';
import 'responsive.dart';
import 'log.dart';
import 'design/tokens.dart';
import 'i18n/languages.dart';
import 'engine/engine.dart';
import 'errors.dart';
import 'screens/home_screen.dart';
import 'screens/nodes_screen.dart';
import 'screens/scan_screen.dart';
import 'screens/account_screen.dart';
import 'screens/support_screen.dart';
import 'screens/overlays.dart';
import 'screens/tariffs_screen.dart';
import 'screens/devices_screen.dart';
import 'screens/referral_screen.dart';
import 'screens/wheel_screen.dart';

final _tabs = <AppScreen>[AppScreen.home, AppScreen.nodes, AppScreen.routes, AppScreen.scan, AppScreen.account, AppScreen.support];

IconData _icon(AppScreen k) => switch (k) {
  AppScreen.home => Icons.shield_outlined,
  AppScreen.nodes => Icons.hub_outlined,
  AppScreen.routes => Icons.alt_route,
  AppScreen.scan => Icons.search,
  AppScreen.account => Icons.person_outline,
  AppScreen.support => Icons.support_agent_outlined,
};
String _navKey(AppScreen k) => 'nav.${k.name}';

class AppShell extends StatefulWidget {
  const AppShell({super.key});
  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  String? _lastErrShown; // чтобы тост не повторялся на каждый ребилд

  Widget _screen() {
    // ВАЖНО: экраны НЕ const — иначе AnimatedBuilder не перерисует их при
    // notifyListeners(), и нажатия будут «срабатывать без эффекта» (выглядит
    // как неработающие кнопки). Новые экземпляры заставляют Flutter ребилдить
    // экран; State у StatefulWidget сохраняется (совпадает тип/позиция).
    Log.w('render', 'строю экран: ${app.overlay ?? app.screen.name}');
    if (app.overlay == 'settings') return SettingsScreen();
    if (app.overlay == 'tariffs') return const TariffsScreen();
    if (app.overlay == 'devices') return const DevicesScreen();
    if (app.overlay == 'referral') return const ReferralScreen();
    if (app.overlay == 'wheel') return const WheelScreen();
    if (app.overlay == 'glossary') return GlossaryScreen();
    if (app.overlay == 'faq') return const FaqScreen();
    if (app.overlay == 'logs') return const LogsScreen();
    if (app.overlay == 'rules') return const RulesScreen();
    switch (app.screen) {
      case AppScreen.nodes: return NodesScreen();
      case AppScreen.routes: return const RulesScreen();
      case AppScreen.scan: return ScanScreen();
      case AppScreen.account: return AccountScreen();
      case AppScreen.support: return SupportScreen();
      case AppScreen.home: return HomeScreen();
    }
  }

  // показать всплывашку при появлении НОВОЙ ошибки (если режим это разрешает)
  void _maybeToast(BuildContext context) {
    final err = app.status.phase == ConnPhase.error ? app.status.error : null;
    final mode = app.errorDisplay; // both|card|toast|none
    if (err == null) { _lastErrShown = null; return; }
    if (mode != 'both' && mode != 'toast') return;
    if (err == _lastErrShown) return;
    _lastErrShown = err;
    final e = humanizeError(err, app.lang);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        backgroundColor: Style.spec.surface,
        behavior: SnackBarBehavior.floating,
        duration: const Duration(seconds: 4),
        content: Row(children: [
          const Icon(Icons.error_outline, color: K.rose, size: 18),
          const SizedBox(width: 10),
          Expanded(child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(e.title, style: const TextStyle(color: K.rose, fontWeight: FontWeight.w700, fontSize: 13)),
            Text(e.message, style: const TextStyle(color: K.txt, fontSize: 12), maxLines: 2, overflow: TextOverflow.ellipsis),
          ])),
        ]),
      ));
    });
  }

  @override
  Widget build(BuildContext context) {
    final sp = Style.spec;
    return Scaffold(
      backgroundColor: sp.bg,
      body: AnimatedBuilder(
        animation: app,
        builder: (context, _) {
          _maybeToast(context);
          final r = Responsive.of(context);
          return DecoratedBox(
            decoration: BoxDecoration(gradient: RadialGradient(
              center: const Alignment(0.8, -1.0), radius: 1.2,
              colors: [sp.accent2.withOpacity(0.10), sp.bg], stops: const [0, 0.6],
            )),
            child: SafeArea(
              child: Column(children: [
                _TopBar(r: r),
                Expanded(child: Row(children: [
                  if (r.useRail) _Rail(r: r),
                  Expanded(child: _Content(r: r, child: _screen())),
                ])),
                if (r.useBottomTabs) _TabBar(r: r),
              ]),
            ),
          );
        },
      ),
    );
  }
}

class _Content extends StatelessWidget {
  final Widget child; final Responsive r;
  const _Content({required this.child, required this.r});
  @override
  Widget build(BuildContext context) {
    // По-экранная политика: списочные/плотные экраны шире; герой «Защита» центрируем;
    // читаемые формы — узкие. На мобильном всегда по центру на всю ширину.
    final ov = app.overlay;
    final scr = app.screen;
    final isWide = ov == 'rules' || ov == 'logs' ||
        (ov == null && (scr == AppScreen.routes || scr == AppScreen.nodes || scr == AppScreen.scan));
    final maxW = isWide ? r.contentMaxWidthWide : r.contentMaxWidth;
    // колонка контейнерная → центрируем (симметричная пустота = осознанный макет).
    final align = Alignment.topCenter;
    return Align(
      alignment: align,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxW),
        child: SingleChildScrollView(
          padding: EdgeInsets.fromLTRB(r.pad, r.isCompact ? 18 : 26, r.pad, 30),
          // плавная смена экранов: затухание + лёгкий подъём снизу
          child: AnimatedSwitcher(
            duration: Dur.medium,
            switchInCurve: Ease.standard,
            switchOutCurve: Ease.accelerate,
            transitionBuilder: (w, anim) => FadeTransition(
              opacity: anim,
              child: SlideTransition(
                position: Tween(begin: const Offset(0, 0.03), end: Offset.zero).animate(anim),
                child: w,
              ),
            ),
            // ключ по экрану+оверлею, чтобы свитчер понимал смену
            child: KeyedSubtree(
              key: ValueKey('${app.screen}-${app.overlay}'),
              child: child,
            ),
          ),
        ),
      ),
    );
  }
}

class _TopBar extends StatelessWidget {
  final Responsive r;
  const _TopBar({required this.r});
  @override
  Widget build(BuildContext context) {
    final on = app.status.phase == ConnPhase.on;
    final connecting = app.status.phase == ConnPhase.connecting;
    final statusText = connecting ? app.tr('status.conn') : on ? app.tr('status.on') : app.tr('status.off');
    final showName = !r.isCompact;
    final showHostInPill = on && app.status.node != null && r.w >= 520;
    final pillHost = showHostInPill ? (app.status.node?.host ?? app.status.node?.id) : null;
    return Container(
      height: r.topBarHeight, padding: EdgeInsets.symmetric(horizontal: r.isCompact ? 10 : 16),
      decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: K.line))),
      child: Row(children: [
        const _Logo(),
        if (showName) const SizedBox(width: 8),
        if (showName) const Text('Симбионт', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 18, letterSpacing: 0.5)),
        SizedBox(width: r.isCompact ? 8 : 14),
        Flexible(child: _statusPill(statusText, on, pillHost)),
        const Spacer(),
        if (r.w >= 440) _Langs(compact: r.isCompact) else _LangButton(),
        const SizedBox(width: 4),
        _StyleButton(),
        const SizedBox(width: 4),
        _iconBtn(Icons.menu_book_outlined, () => app.openOverlay('glossary'), app.tr('glossary.title')),
        const SizedBox(width: 4),
        _iconBtn(Icons.help_outline, () => app.openOverlay('faq'), app.tr('faq.title')),
        const SizedBox(width: 4),
        _iconBtn(Icons.settings_outlined, () => app.openOverlay('settings'), app.tr('settings.title')),
      ]),
    );
  }

  Widget _statusPill(String text, bool on, String? host) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
    decoration: BoxDecoration(color: const Color(0x08FFFFFF), borderRadius: BorderRadius.circular(12), border: Border.all(color: K.line)),
    child: Row(mainAxisSize: MainAxisSize.min, children: [
      Container(width: 8, height: 8, decoration: BoxDecoration(color: on ? K.mint : K.muted, shape: BoxShape.circle)),
      Flexible(child: Padding(
        padding: const EdgeInsets.only(left: 8),
        child: Text(text, overflow: TextOverflow.ellipsis, maxLines: 1, softWrap: false,
          style: TextStyle(fontSize: 13, color: on ? K.txt : K.txt2, fontWeight: on ? FontWeight.w700 : FontWeight.w400)))),
      if (host != null) Flexible(child: Padding(
        padding: const EdgeInsets.only(left: 6),
        child: Text(host, overflow: TextOverflow.ellipsis, maxLines: 1, softWrap: false, style: mono(size: 11, color: K.muted)))),
    ]),
  );
}

Widget _iconBtn(IconData i, VoidCallback onTap, String label) => Tooltip(
  message: label,
  child: Semantics(button: true, label: label, child: InkWell(
    borderRadius: BorderRadius.circular(11), onTap: onTap,
    child: SizedBox(width: 40, height: 40, child: Center(child: Container(
      width: 36, height: 36,
      decoration: BoxDecoration(color: const Color(0x0AFFFFFF), borderRadius: BorderRadius.circular(11), border: Border.all(color: K.line)),
      child: Icon(i, size: 18, color: K.txt2))))),
  ),
);

/// Кнопка выбора стиля в углу шапки (как переключатель день/ночь, но 4 стиля).
class _StyleButton extends StatelessWidget {
  void _pick(BuildContext context) {
    showModalBottomSheet(
      context: context, backgroundColor: Style.spec.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(Style.spec.radius == 0 ? 0 : 18))),
      builder: (_) => SafeArea(child: SingleChildScrollView(child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(padding: const EdgeInsets.only(left: 4, bottom: 4),
            child: Text(app.tr('style.title'), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: K.txt))),
          Padding(padding: const EdgeInsets.only(left: 4, bottom: 12),
            child: Text(app.tr('style.sub'), style: const TextStyle(fontSize: 12.5, color: K.muted))),
          ...AppStyle.values.map((st) {
            final spec = kStyles[st]!;
            final active = Style.current.value == st;
            return Padding(padding: const EdgeInsets.only(bottom: 8), child: InkWell(
              borderRadius: BorderRadius.circular(spec.radius == 0 ? 0 : 12),
              onTap: () { app.setStyle(st); Navigator.pop(context); },
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                decoration: BoxDecoration(
                  color: active ? spec.accent.withOpacity(0.12) : const Color(0x0AFFFFFF),
                  borderRadius: BorderRadius.circular(spec.radius == 0 ? 0 : 12),
                  border: Border.all(color: active ? spec.accent : K.line, width: active ? 2 : 1),
                ),
                child: Row(children: [
                  // мини-превью: квадрат в цвете акцента с формой стиля
                  Container(width: 34, height: 34, decoration: BoxDecoration(
                    color: spec.surface, border: Border.all(color: spec.accent, width: spec.borderWidth),
                    borderRadius: BorderRadius.circular(spec.radius == 0 ? 0 : 8),
                    boxShadow: spec.shadowOffset != Offset.zero ? [BoxShadow(color: spec.shadowColor, offset: const Offset(2, 2))] : (spec.glow ? [BoxShadow(color: spec.accent.withOpacity(0.5), blurRadius: 8)] : null),
                  ), child: Center(child: Text(spec.id, style: TextStyle(color: spec.accent, fontWeight: FontWeight.w800,
                    fontFamily: spec.monoEverywhere ? 'Consolas' : null)))),
                  const SizedBox(width: 12),
                  Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(spec.name, style: TextStyle(fontSize: 14.5, fontWeight: FontWeight.w700, color: active ? spec.accent : K.txt)),
                    const SizedBox(height: 2),
                    Text(spec.hint, style: const TextStyle(fontSize: 12, color: K.muted)),
                  ])),
                  if (active) Icon(Icons.check_circle, size: 20, color: spec.accent),
                ]),
              ),
            ));
          }),
        ]),
      ))),
    );
  }

  @override
  Widget build(BuildContext context) => InkWell(
    borderRadius: BorderRadius.circular(11), onTap: () => _pick(context),
    child: Container(width: 36, height: 36,
      decoration: BoxDecoration(color: const Color(0x0AFFFFFF), borderRadius: BorderRadius.circular(11), border: Border.all(color: K.line)),
      child: const Icon(Icons.palette_outlined, size: 17, color: K.txt2)),
  );
}

class _Logo extends StatelessWidget {
  const _Logo();
  @override
  Widget build(BuildContext context) => Container(
    width: 34, height: 34,
    decoration: BoxDecoration(gradient: K.grad, borderRadius: BorderRadius.circular(10)),
    child: const Icon(Icons.all_inclusive, size: 20, color: Color(0xFF04201A)),
  );
}

// Языки берём из реестра (lib/i18n/languages.dart). Меню со всеми языками —
// самоназвания; готовые отмечены, остальные идут на английском фоллбэке.
PopupMenuItem<String> _langItem(String code) {
  final l = langByCode(code);
  final on = app.lang == code;
  return PopupMenuItem<String>(value: code, height: 40, child: Row(children: [
    Expanded(child: Text(l.native, style: TextStyle(
      color: on ? K.mint : K.txt, fontWeight: on ? FontWeight.w700 : FontWeight.w500))),
    if (!l.complete) const Padding(padding: EdgeInsets.only(left: 8),
      child: Text('β', style: TextStyle(fontSize: 11, color: K.muted))),
    if (on) const Padding(padding: EdgeInsets.only(left: 8), child: Icon(Icons.check, size: 16, color: K.mint)),
  ]));
}

class _Langs extends StatelessWidget {
  final bool compact;
  const _Langs({this.compact = false});
  @override
  Widget build(BuildContext context) {
    final cur = langByCode(app.lang);
    return PopupMenuButton<String>(
      tooltip: 'Language',
      color: K.surface2,
      constraints: const BoxConstraints(maxHeight: 360, minWidth: 200),
      onSelected: (l) => app.setLang(l),
      itemBuilder: (_) => kLanguages.map((l) => _langItem(l.code)).toList(),
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: compact ? 8 : 10, vertical: 7),
        decoration: BoxDecoration(color: const Color(0x0AFFFFFF), borderRadius: BorderRadius.circular(10), border: Border.all(color: K.line)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.language, size: 15, color: K.txt2),
          const SizedBox(width: 6),
          Text(cur.native, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: K.txt2)),
          const Icon(Icons.arrow_drop_down, size: 18, color: K.muted),
        ]),
      ),
    );
  }
}

/// На очень узких экранах — компактный выбор языка через меню.
class _LangButton extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<String>(
      tooltip: 'Language',
      color: K.surface2,
      constraints: const BoxConstraints(maxHeight: 360, minWidth: 200),
      onSelected: (l) => app.setLang(l),
      itemBuilder: (_) => kLanguages.map((l) => _langItem(l.code)).toList(),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        decoration: BoxDecoration(color: const Color(0x0AFFFFFF), borderRadius: BorderRadius.circular(10), border: Border.all(color: K.line)),
        child: Text(app.lang.toUpperCase(), style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: K.txt2)),
      ),
    );
  }
}

class _Rail extends StatelessWidget {
  final Responsive r;
  const _Rail({required this.r});
  @override
  Widget build(BuildContext context) => Container(
    width: r.railWidth,
    decoration: const BoxDecoration(border: Border(right: BorderSide(color: K.line))),
    padding: const EdgeInsets.fromLTRB(12, 16, 12, 12),
    child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Padding(padding: const EdgeInsets.fromLTRB(12, 10, 12, 6),
        child: Text(app.tr('rail.group').toUpperCase(), style: const TextStyle(fontSize: 10.5, letterSpacing: 1.6, color: K.muted))),
      ..._tabs.map((k) {
        final on = app.screen == k && app.overlay == null;
        return Padding(
          padding: const EdgeInsets.only(bottom: 4),
          child: Semantics(button: true, selected: on, label: app.tr(_navKey(k)), child: InkWell(
            borderRadius: BorderRadius.circular(13), onTap: () => app.go(k),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 11),
              decoration: BoxDecoration(gradient: on ? K.gradSoft : null, borderRadius: BorderRadius.circular(13)),
              child: Row(children: [
                Icon(_icon(k), size: 20, color: on ? K.mint : K.txt2),
                const SizedBox(width: 12),
                Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(app.tr(_navKey(k)), style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: on ? K.mint : K.txt2)),
                  Text(app.tr('navd.${k.name}'), style: const TextStyle(fontSize: 10.5, color: K.muted)),
                ])),
              ]),
            ),
          )),
        );
      }),
      const Spacer(),
      _ConnBadge(),
      const SizedBox(height: 10),
      Container(
        padding: const EdgeInsets.all(12),
        decoration: const BoxDecoration(border: Border(top: BorderSide(color: K.line))),
        child: Text(app.tr('rail.foot'), style: const TextStyle(fontSize: 11, color: K.muted, height: 1.5)),
      ),
    ]),
  );
}

/// Бейдж онлайн/офлайн бэкенда.
class _ConnBadge extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final online = app.backendOnline;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: (online ? K.mint : K.muted).withOpacity(0.10),
        borderRadius: BorderRadius.circular(11),
        border: Border.all(color: (online ? K.mint : K.muted).withOpacity(0.3))),
      child: Row(children: [
        Icon(online ? Icons.cloud_done_outlined : Icons.cloud_off_outlined, size: 15, color: online ? K.mint : K.muted),
        const SizedBox(width: 8),
        Text(app.tr(online ? 'online.badge' : 'offline.badge'),
          style: TextStyle(fontSize: 11.5, color: online ? K.mint : K.muted, fontWeight: FontWeight.w600)),
      ]),
    );
  }
}

class _TabBar extends StatelessWidget {
  final Responsive r;
  const _TabBar({required this.r});
  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.fromLTRB(6, 8, 6, 8),
    decoration: const BoxDecoration(color: Color(0xE60A0C12), border: Border(top: BorderSide(color: K.line))),
    child: Row(children: _tabs.map((k) {
      final on = app.screen == k && app.overlay == null;
      return Expanded(child: Semantics(button: true, selected: on, label: app.tr(_navKey(k)), child: InkWell(
        borderRadius: BorderRadius.circular(12), onTap: () => app.go(k),
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: TT.min),
          child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(_icon(k), size: 21, color: on ? K.mint : K.muted),
            const SizedBox(height: 4),
            Text(app.tr(_navKey(k)), maxLines: 1, overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 10, fontWeight: FontWeight.w600, color: on ? K.mint : K.muted)),
            if (on) Text(app.tr('navd.${k.name}'), maxLines: 1, overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 8.5, color: K.muted)),
          ]),
        ),
        ),
      )));
    }).toList()),
  );
}
