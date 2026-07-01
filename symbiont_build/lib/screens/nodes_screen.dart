// lib/screens/nodes_screen.dart — экран «Узлы». Адаптивная сетка (1/2/3 колонки).
import 'package:flutter/material.dart';
import 'dart:math';
import '../main.dart';
import '../theme.dart';
import '../responsive.dart';
import '../design/tokens.dart';
import '../engine/engine.dart';

class NodesScreen extends StatefulWidget {
  const NodesScreen({super.key});
  @override
  State<NodesScreen> createState() => _NodesScreenState();
}

class _NodesScreenState extends State<NodesScreen> {
  String q = '';
  bool favOnly = false;
  @override
  Widget build(BuildContext context) {
    final r = Responsive.of(context);
    final list = <MapEntry<int, NodeInfo>>[];
    for (var i = 0; i < app.nodes.length; i++) {
      final n = app.nodes[i];
      final okQ = q.isEmpty || _country(n).toLowerCase().contains(q.toLowerCase());
      final okF = !favOnly || n.favorite;
      if (okQ && okF) list.add(MapEntry(i, n));
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      titleText(app.tr('nav.nodes')),
      const SizedBox(height: 6),
      _search(),
      const SizedBox(height: 12),
      Row(children: [
        _quick('🚀', app.tr('fastest.t'), app.tr('fastest.sub'), () => app.fastest()),
        const SizedBox(width: 11),
        _quick('🎲', app.tr('random.t'), app.tr('random.sub'), () => app.pickRandom()),
      ]),
      const SizedBox(height: 12),
      Row(children: [
        _filterChip(app.tr('nodes.all'), !favOnly, () => setState(() => favOnly = false)),
        const SizedBox(width: 8),
        _filterChip('★ ${app.tr('nodes.fav')}', favOnly, () => setState(() => favOnly = true)),
      ]),
      const SizedBox(height: 14),
      if (list.isEmpty) Padding(padding: const EdgeInsets.symmetric(vertical: 36),
        child: Center(child: Column(children: [
          Container(width: 64, height: 64, decoration: BoxDecoration(
            gradient: K.gradSoft, shape: BoxShape.circle, border: Border.all(color: K.mint.withOpacity(0.4))),
            child: Icon(app.nodes.isEmpty ? Icons.dns_outlined : Icons.search_off, size: 28, color: K.mint)),
          const SizedBox(height: 14),
          Text(app.tr(app.nodes.isEmpty ? 'nodes.none' : 'nodes.empty'),
            textAlign: TextAlign.center, style: const TextStyle(color: K.txt, fontWeight: FontWeight.w700, fontSize: 15)),
          if (app.nodes.isEmpty) Padding(padding: const EdgeInsets.only(top: 8),
            child: Text(app.tr('nodes.none.sub'), textAlign: TextAlign.center,
              style: const TextStyle(color: K.muted, fontSize: 12.5, height: 1.5))),
        ]))),
      _grid(r, list),
    ]);
  }

  Widget _grid(Responsive r, List<MapEntry<int, NodeInfo>> list) {
    return LayoutBuilder(builder: (context, c) {
      // колонки считаем от РЕАЛЬНОЙ ширины контента (а не окна) — корректно при ресайзе
      final cols = c.maxWidth >= 1320 ? 3 : (c.maxWidth >= 680 ? 2 : 1);
      if (cols == 1) {
        return Column(children: [for (final e in list) Padding(padding: const EdgeInsets.only(bottom: 10), child: _node(e.key, e.value))]);
      }
      const gap = 12.0;
      final tileW = ((c.maxWidth - gap * (cols - 1)) / cols).floorToDouble();
      return Wrap(spacing: gap, runSpacing: gap, children: [
        for (final e in list) SizedBox(width: tileW, child: _node(e.key, e.value)),
      ]);
    });
  }

  Widget _search() => Container(
    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
    decoration: BoxDecoration(color: K.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: K.line2)),
    child: Row(children: [
      const Icon(Icons.search, size: 17, color: K.muted),
      const SizedBox(width: 9),
      Expanded(child: TextField(
        onChanged: (v) => setState(() => q = v),
        style: const TextStyle(color: K.txt, fontSize: 14),
        decoration: InputDecoration(border: InputBorder.none, hintText: app.tr('nodes.search'), hintStyle: const TextStyle(color: K.muted)),
      )),
    ]),
  );

  Widget _filterChip(String t, bool on, VoidCallback onTap) => InkWell(
    borderRadius: BorderRadius.circular(10), onTap: onTap,
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 8),
      decoration: BoxDecoration(gradient: on ? K.gradSoft : null, color: on ? null : K.surface,
        borderRadius: BorderRadius.circular(10), border: Border.all(color: on ? K.mint.withOpacity(0.5) : K.line)),
      child: Text(t, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: on ? K.mint : K.txt2)),
    ),
  );

  Widget _quick(String ic, String t, String d, VoidCallback onTap) => Expanded(child: InkWell(
    borderRadius: BorderRadius.circular(20), onTap: onTap,
    child: Container(
      padding: const EdgeInsets.fromLTRB(15, 16, 15, 16),
      decoration: BoxDecoration(color: K.surface, borderRadius: BorderRadius.circular(20), border: Border.all(color: K.line)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(ic, style: const TextStyle(fontSize: 23)),
        const SizedBox(height: 9),
        Text(t, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5)),
        const SizedBox(height: 2),
        Text(d, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 11, color: K.muted)),
      ]),
    ),
  ));

  Widget _node(int i, NodeInfo n) {
    final sel = app.nodeIndex == i;
    final live = sel && app.status.phase == ConnPhase.on;
    final connecting = sel && app.status.phase == ConnPhase.connecting;
    return _Appear(
      delay: Duration(milliseconds: 40 * (i % 8)),
      child: InkWell(
        borderRadius: BorderRadius.circular(16), onTap: () => app.selectNode(i),
        child: AnimatedContainer(
          duration: Dur.medium,
          padding: const EdgeInsets.all(13),
          decoration: BoxDecoration(
            gradient: sel ? K.gradSoft : null, color: sel ? null : K.surface,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: live ? K.mint : (sel ? K.mint.withOpacity(0.5) : K.line), width: live ? 1.5 : 1),
            boxShadow: live ? [BoxShadow(color: K.mint.withOpacity(0.25), blurRadius: 24, spreadRadius: -6)] : null,
          ),
          child: Row(children: [
            // флаг в скруглённой плашке (как у Bebra)
            Container(width: 46, height: 46,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: K.ink, borderRadius: BorderRadius.circular(13),
                border: Border.all(color: sel ? K.mint.withOpacity(0.4) : K.line2)),
              child: Text(_flag(n.code), style: const TextStyle(fontSize: 26))),
            const SizedBox(width: 13),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Flexible(child: Text(_country(n), maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5))),
                if (live || connecting) ...[
                  const SizedBox(width: 7),
                  _StatusDot(on: live),
                ],
              ]),
              const SizedBox(height: 6),
              Row(children: [
                Icon(Icons.speed_rounded, size: 13, color: K.muted),
                const SizedBox(width: 4),
                Text(n.pingMs != null ? '${n.pingMs} ms' : '— ms', style: mono(size: 11.5, color: live ? K.mint : K.txt2)),
                const SizedBox(width: 12),
                // анимированная полоска нагрузки
                Expanded(child: ClipRRect(borderRadius: BorderRadius.circular(3),
                  child: Stack(children: [
                    Container(height: 5, color: const Color(0x14FFFFFF)),
                    TweenAnimationBuilder<double>(
                      tween: Tween(begin: 0, end: (n.loadPct / 100).clamp(0.0, 1.0)),
                      duration: Dur.long, curve: Ease.decelerate,
                      builder: (c, v, _) => FractionallySizedBox(widthFactor: v, alignment: Alignment.centerLeft,
                        child: Container(height: 5, decoration: BoxDecoration(
                          gradient: LinearGradient(colors: [loadColor(n.loadPct).withOpacity(0.6), loadColor(n.loadPct)])))),
                    ),
                  ]))),
                const SizedBox(width: 8),
                Text('${n.loadPct}%', style: const TextStyle(fontSize: 10.5, color: K.muted)),
              ]),
            ])),
            const SizedBox(width: 8),
            // звезда избранного
            InkWell(onTap: () => app.toggleFav(i), borderRadius: BorderRadius.circular(20),
              child: Padding(padding: const EdgeInsets.all(5),
                child: Icon(n.favorite ? Icons.star_rounded : Icons.star_outline_rounded,
                  size: 20, color: n.favorite ? K.amber : K.muted))),
          ]),
        ),
      ),
    );
  }
}

// Плавное появление карточки: затухание + подъём, с задержкой (стаггер по списку).
class _Appear extends StatefulWidget {
  final Widget child; final Duration delay;
  const _Appear({required this.child, this.delay = Duration.zero});
  @override
  State<_Appear> createState() => _AppearState();
}

class _AppearState extends State<_Appear> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: Dur.long);
  @override
  void initState() {
    super.initState();
    Future.delayed(widget.delay, () { if (mounted) _c.forward(); });
  }
  @override
  void dispose() { _c.dispose(); super.dispose(); }
  @override
  Widget build(BuildContext context) {
    final curved = CurvedAnimation(parent: _c, curve: Ease.decelerate);
    return FadeTransition(opacity: curved,
      child: SlideTransition(
        position: Tween(begin: const Offset(0, 0.08), end: Offset.zero).animate(curved),
        child: widget.child));
  }
}

// Пульсирующая точка статуса (зелёная = подключено, мятная мигает = подключение).
class _StatusDot extends StatefulWidget {
  final bool on;
  const _StatusDot({required this.on});
  @override
  State<_StatusDot> createState() => _StatusDotState();
}

class _StatusDotState extends State<_StatusDot> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))..repeat(reverse: true);
  @override
  void dispose() { _c.dispose(); super.dispose(); }
  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: _c,
    builder: (c, _) {
      final g = 0.4 + 0.6 * _c.value;
      return Container(width: 9, height: 9, decoration: BoxDecoration(
        color: K.mint, shape: BoxShape.circle,
        boxShadow: [BoxShadow(color: K.mint.withOpacity(0.6 * g), blurRadius: 8 * g, spreadRadius: 1.5 * g)]));
    },
  );
}

String _country(NodeInfo n) => app.tr('node.${n.code}') == 'node.${n.code}' ? n.country : app.tr('node.${n.code}');
String _flag(String code) {
  const base = 0x1F1E6;
  final cc = code.toUpperCase();
  if (cc.length < 2) return cc.isEmpty ? '··' : cc;
  return String.fromCharCode(base + cc.codeUnitAt(0) - 65) + String.fromCharCode(base + cc.codeUnitAt(1) - 65);
}
