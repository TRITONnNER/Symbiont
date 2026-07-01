// lib/screens/home_screen.dart — экран «Защита». Адаптивен: размер круга и сетки
// режимов считаются из Responsive (корректно при ресайзе/повороте/фуллскрине).
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../main.dart';
import '../theme.dart';
import '../style_engine.dart';
import '../errors.dart';
import '../responsive.dart';
import '../engine/engine.dart';
import 'widgets/connection_core.dart';
import '../design/tokens.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final r = Responsive.of(context);
    final hasServers = app.nodes.isNotEmpty;
    final canConnect = app.nativeTunnel || hasServers; // реальный движок умеет коннектиться по config.json
    final on = canConnect && app.status.phase == ConnPhase.on;
    final connecting = app.status.phase == ConnPhase.connecting;
    final error = app.status.phase == ConnPhase.error ? app.status.error : null;
    final n = app.node;
    final proto = app.status.protocol; // 'vpn' | 'bypass' | null
    final vpnOn = on && proto == 'vpn';

    // РЕАЛЬНЫЕ значения. VPN-режим → пинг до узла (если измерен). Обход и прямое
    // подключение идут по прямому каналу → показываем реальный прямой пинг.
    final pingStr = vpnOn
        ? (n.pingMs != null ? '${n.pingMs}' : '—')
        : (app.measuringDirect && app.directPingMs == null ? '…' : (app.directPingMs?.toString() ?? '—'));
    final lossStr = vpnOn ? '—' : (app.directLossPct != null ? app.directLossPct!.toStringAsFixed(1) : '—');
    final protoStr = on
        ? ((proto == 'bypass' || proto == 'bypass-proxy') ? app.tr('proto.bypass') : app.tr('proto.vpn'))
        : app.tr('proto.direct');

    if (!canConnect) {
      // НЕТ СЕРВЕРОВ и нет реального движка → честное прямое подключение с реальным пингом.
      return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        SizedBox(height: r.isCompact ? 4 : 8),
        Center(child: ConnectionCore(
          size: r.dialSize,
          state: app.measuringDirect ? CoreState.measuring : CoreState.idle,
          label: app.tr('direct.title'),
          sub: app.directPingMs != null ? '${app.directPingMs} ms' : null,
          onTap: app.measureDirect)),
        SizedBox(height: r.isCompact ? 16 : 20),
        _metricsRow(pingStr, lossStr, protoStr, green: false),
        const SizedBox(height: 16),
        cardBox(child: Row(children: [
          const Icon(Icons.dns_outlined, size: 20, color: K.muted),
          const SizedBox(width: 12),
          Expanded(child: Text(app.tr('direct.none'),
            style: const TextStyle(fontSize: 12.5, color: K.txt2, height: 1.45))),
        ])),
        const SizedBox(height: 12),
        Center(child: Text(app.backendOnline ? app.tr('online.badge') : app.tr('offline.badge'),
          style: const TextStyle(fontSize: 11.5, color: K.muted))),
      ]);
    }

    // ЕСТЬ КУДА ПОДКЛЮЧАТЬСЯ: серверы из манифеста и/или реальный движок (config.json).
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      SizedBox(height: r.isCompact ? 4 : 8),
      Center(child: ConnectionCore(
        size: r.dialSize,
        state: error != null ? CoreState.error
             : connecting ? CoreState.connecting
             : on ? CoreState.connected : CoreState.idle,
        label: connecting ? app.tr('status.conn')
             : on ? app.tr('status.on') : app.tr('status.off'),
        pathLabel: on
          ? (app.preferRelay && app.relays.isNotEmpty ? app.tr('path.relay')
             : proto == 'vpn' ? app.tr('path.tunnel')
             : (proto == 'bypass' || proto == 'bypass-proxy') ? app.tr('path.bypass')
             : app.tr('path.direct'))
          : null,
        sub: on && n.host != null ? n.host : null,
        onTap: app.toggleConnect)),
      SizedBox(height: r.isCompact ? 16 : 20),
      _metricsRow(pingStr, lossStr, protoStr, green: on),
      if (app.pingHistory.length >= 2) Padding(
        padding: const EdgeInsets.only(top: 12),
        child: cardBox(padding: const EdgeInsets.fromLTRB(14, 12, 14, 10), child: Column(
          crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              const Icon(Icons.show_chart_rounded, size: 14, color: K.mint),
              const SizedBox(width: 6),
              Flexible(child: Text(app.tr('graph.ping'), maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 11.5, color: K.txt2, fontWeight: FontWeight.w600))),
              const Spacer(),
              Text('${app.directPingMs ?? '—'} ms', style: mono(size: 11.5, color: K.mint)),
            ]),
            const SizedBox(height: 8),
            SizedBox(height: 44, width: double.infinity,
              child: TweenAnimationBuilder<double>(
                tween: Tween(begin: 0.0, end: 1.0), duration: Dur.long,
                builder: (c, t, _) => CustomPaint(painter: _Sparkline(List.of(app.pingHistory), t)))),
          ]))),
      if (error != null) _errorBox(error),
      if (on && proto == 'bypass-proxy') _noteBox(app.tr('bypass.proxy.note')),
      sectionLabel(app.tr('modes.label')),
      _modes(r),
      const SizedBox(height: 10),
      sectionLabel(app.tr('proto.label')),
      _protoChips(),
      const SizedBox(height: 10),
      sectionLabel(app.tr('boost.label')),
      _gameBoostCard(),
      const SizedBox(height: 4),
      if (hasServers) cardBox(
        onTap: () => app.go(AppScreen.nodes),
        child: Row(children: [
          Text(_flag(n.code), style: const TextStyle(fontSize: 25)),
          const SizedBox(width: 12),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Flexible(child: Text(_country(n), maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14))),
              if (on) ...[
                const SizedBox(width: 8),
                Container(width: 7, height: 7, decoration: const BoxDecoration(color: K.mint, shape: BoxShape.circle)),
              ],
            ]),
            const SizedBox(height: 2),
            // как у коммерческих VPN: при подключении показываем реальный адрес сервера
            Text(
              on && n.host != null
                ? '${app.tr('status.connected')}: ${n.host}${n.pingMs != null ? ' · ${n.pingMs} ms' : ''}'
                : (n.pingMs != null ? '${app.tr('fastest.sub')} · ${n.pingMs} ms' : app.tr('fastest.sub')),
              style: mono(size: 11, color: on ? K.mint : K.muted)),
          ])),
          const Icon(Icons.chevron_right, size: 20, color: K.muted),
        ]),
      )
      else cardBox(
        onTap: app.toggleConnect,
        child: Row(children: [
          Icon(on && (proto == 'bypass' || proto == 'bypass-proxy') ? Icons.check_circle : Icons.bolt_outlined,
            size: 22, color: K.mint),
          const SizedBox(width: 12),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(app.tr('bypass.card.title'), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
            const SizedBox(height: 2),
            Text(app.tr('bypass.card.sub'), style: const TextStyle(fontSize: 11.5, color: K.muted, height: 1.4)),
          ])),
          const SizedBox(width: 8),
          Icon(on ? Icons.power_settings_new : Icons.power_settings_new_outlined,
            size: 19, color: on ? K.mint : K.muted),
        ]),
      ),
      const SizedBox(height: 12),
      gradButton(app.tr('home.analyze'), () => app.go(AppScreen.scan), icon: Icons.search),
      const SizedBox(height: 10),
      Center(child: Text(app.tr('home.analyze.sub'), textAlign: TextAlign.center, style: const TextStyle(fontSize: 11.5, color: K.muted))),
    ]);
  }

  Widget _noteBox(String msg) => Padding(padding: const EdgeInsets.only(top: 12), child: Container(
    padding: const EdgeInsets.all(13),
    decoration: BoxDecoration(color: K.amber.withOpacity(0.10), borderRadius: BorderRadius.circular(13),
      border: Border.all(color: K.amber.withOpacity(0.35))),
    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Icon(Icons.info_outline, size: 17, color: K.amber),
      const SizedBox(width: 10),
      Expanded(child: Text(msg, style: const TextStyle(fontSize: 11.5, color: K.txt2, height: 1.45))),
    ]),
  ));

  Widget _errorBox(String code) {
    // показываем карточку только если режим её разрешает
    final mode = app.errorDisplay; // 'both'|'card'|'toast'|'none'
    if (mode == 'none' || mode == 'toast') return const SizedBox.shrink();
    return _ErrorCard(code: code);
  }

  Widget _metricsRow(String ping, String loss, String proto, {required bool green}) => Row(children: [
    _metric(ping, '${app.tr('metric.ping')} ms', green: green),
    const SizedBox(width: 11),
    _metric(loss, '${app.tr('metric.loss')} %'),
    const SizedBox(width: 11),
    _metric(proto, app.tr('metric.proto')),
  ]);

  Widget _metric(String v, String k, {bool green = false}) => Expanded(child: Container(
    padding: const EdgeInsets.all(13),
    decoration: BoxDecoration(color: K.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: K.line)),
    child: Column(children: [
      FittedBox(child: Text(v, style: mono(size: 19, color: green ? K.mint : K.txt, w: FontWeight.w600))),
      const SizedBox(height: 3),
      Text(k.toUpperCase(), maxLines: 1, overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontSize: 10.5, color: K.muted, letterSpacing: 0.8)),
    ]),
  ));

  // Выбор протокола: Авто (умный каскад) или конкретный. С подсказкой для любого уровня.
  // ── Карточка геймбустера: измеряет узлы и предлагает лучший по пингу ──
  Widget _gameBoostCard() {
    final hasNodes = app.nodes.isNotEmpty;
    return cardBox(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Icon(Icons.rocket_launch_outlined, size: 18, color: K.blue),
        const SizedBox(width: 8),
        Expanded(child: Text(app.tr('boost.title'), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14))),
        if (app.gameBoosting)
          const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 1.8, color: K.blue)),
      ]),
      const SizedBox(height: 4),
      Text(app.tr('boost.sub'), style: const TextStyle(fontSize: 11.5, color: K.muted, height: 1.4)),
      if (!hasNodes)
        Padding(padding: const EdgeInsets.only(top: 10),
          child: Text(app.tr('boost.nonodes'), style: const TextStyle(fontSize: 12, color: K.amber, height: 1.4)))
      else ...[
        const SizedBox(height: 12),
        // до / после
        if (app.gamePingAfter != null)
          Row(children: [
            _boostStat(app.tr('boost.before'), app.gamePingBefore != null ? '${app.gamePingBefore}' : '—', K.muted),
            const Padding(padding: EdgeInsets.symmetric(horizontal: 10), child: Icon(Icons.arrow_forward, size: 16, color: K.muted)),
            _boostStat(app.tr('boost.after'), '${app.gamePingAfter}', K.mint),
          ]),
        if (app.gamePingAfter != null) const SizedBox(height: 12),
        Row(children: [
          Expanded(child: gradButton(app.gameBoosting ? app.tr('boost.measuring') : app.tr('boost.measure'),
            app.gameBoosting ? () {} : () => app.runGameBoost(), icon: Icons.speed)),
          if (app.gameBestNodeId != null && !app.gameBoosting) ...[
            const SizedBox(width: 8),
            Expanded(child: gradButton(app.tr('boost.apply'), () => app.applyGameBoost(), icon: Icons.check, ghost: true)),
          ],
        ]),
        Padding(padding: const EdgeInsets.only(top: 8),
          child: Text(app.tr('boost.note'), style: const TextStyle(fontSize: 10.5, color: K.muted, fontStyle: FontStyle.italic, height: 1.4))),
      ],
    ]));
  }

  Widget _boostStat(String label, String ping, Color c) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
    Text(label, style: const TextStyle(fontSize: 10.5, color: K.muted)),
    const SizedBox(height: 2),
    Row(crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
      Text(ping, style: mono(size: 22, color: c)),
      const SizedBox(width: 3),
      const Text('ms', style: TextStyle(fontSize: 10, color: K.muted)),
    ]),
  ]);

  Widget _protoChips() {
    final items = [
      ['auto', 'Авто'], ['reality', 'Reality'], ['hysteria2', 'Hysteria2'], ['ss2022', 'SS2022'],
    ];
    return Wrap(spacing: 8, runSpacing: 8, children: items.map((it) {
      final key = it[0]; final label = it[1];
      final on = app.protoChoice == key;
      return InkWell(
        borderRadius: BorderRadius.circular(20), onTap: () => app.setProtoChoice(key),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
            gradient: on ? K.gradSoft : null, color: on ? null : K.surface,
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: on ? K.mint.withOpacity(0.5) : K.line)),
          child: Text(label, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: on ? K.mint : K.txt2)),
        ),
      );
    }).toList());
  }

  Widget _modes(Responsive r) {
    final items = [
      ['smart', CoverageMode.smart], ['whole', CoverageMode.whole],
      ['selected', CoverageMode.selected], ['off', CoverageMode.off],
    ];
    final cols = r.modeColumns;
    return LayoutBuilder(builder: (context, c) {
      final gap = 8.0;
      final tileW = cols == 1 ? c.maxWidth : ((c.maxWidth - gap) / 2).floorToDouble();
      return Wrap(spacing: gap, runSpacing: gap, children: items.map((it) {
        final key = it[0] as String; final m = it[1] as CoverageMode;
        final on = app.mode == m;
        return SizedBox(
          width: tileW,
          child: InkWell(
            borderRadius: BorderRadius.circular(13), onTap: () => app.setMode(m),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 12),
              decoration: BoxDecoration(
                gradient: on ? K.gradSoft : null, color: on ? null : K.surface,
                borderRadius: BorderRadius.circular(13),
                border: Border.all(color: on ? K.mint.withOpacity(0.5) : K.line)),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(app.tr('mode.$key.t'), style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: on ? K.mint : K.txt)),
                const SizedBox(height: 2),
                Text(app.tr('mode.$key.d'), style: const TextStyle(fontSize: 11, color: K.muted)),
              ]),
            ),
          ),
        );
      }).toList());
    });
  }
}

// Мини-график пинга: плавная линия по нашей гамме + лёгкая заливка снизу.
class _Sparkline extends CustomPainter {
  final List<double> data; final double t; // t: 0..1 прогресс прорисовки
  _Sparkline(this.data, this.t);
  @override
  void paint(Canvas canvas, Size size) {
    if (data.length < 2) return;
    if (size.width <= 0 || size.height <= 0) return; // защита от invalid matrix
    final lo = data.reduce((a, b) => a < b ? a : b);
    final hi = data.reduce((a, b) => a > b ? a : b);
    final span = (hi - lo).abs() < 1 ? 1.0 : (hi - lo);
    final n = data.length;
    Offset pt(int i) {
      final x = size.width * (i / (n - 1));
      final norm = (data[i] - lo) / span;          // 0..1
      final y = size.height * (1 - norm) * 0.9 + size.height * 0.05;
      return Offset(x, y);
    }
    // сколько точек показываем по прогрессу t
    final shown = (2 + (n - 2) * t).clamp(2, n).toInt();
    final path = Path()..moveTo(pt(0).dx, pt(0).dy);
    for (var i = 1; i < shown; i++) { path.lineTo(pt(i).dx, pt(i).dy); }
    // заливка под линией
    final fill = Path.from(path)
      ..lineTo(pt(shown - 1).dx, size.height)
      ..lineTo(0, size.height)..close();
    canvas.drawPath(fill, Paint()..shader = LinearGradient(
      begin: Alignment.topCenter, end: Alignment.bottomCenter,
      colors: [K.mint.withOpacity(0.28), K.mint.withOpacity(0.0)]).createShader(Offset.zero & size));
    // линия
    canvas.drawPath(path, Paint()
      ..style = PaintingStyle.stroke..strokeWidth = 2..strokeCap = StrokeCap.round..strokeJoin = StrokeJoin.round
      ..shader = const LinearGradient(colors: [K.mint, K.aqua]).createShader(Offset.zero & size));
    // точка на конце
    final end = pt(shown - 1);
    canvas.drawCircle(end, 3.2, Paint()..color = K.mint);
    canvas.drawCircle(end, 6, Paint()..color = K.mint.withOpacity(0.25));
  }
  @override
  bool shouldRepaint(_Sparkline old) => old.t != t || old.data.length != data.length
      || (data.isNotEmpty && old.data.isNotEmpty && old.data.last != data.last);
}

String _country(NodeInfo n) => app.tr('node.${n.code}') == 'node.${n.code}' ? n.country : app.tr('node.${n.code}');
String _flag(String code) {
  const base = 0x1F1E6;
  final cc = code.toUpperCase();
  if (cc.length < 2) return cc.isEmpty ? '··' : cc;
  return String.fromCharCode(base + cc.codeUnitAt(0) - 65) + String.fromCharCode(base + cc.codeUnitAt(1) - 65);
}

/// Карточка ошибки: понятный текст + разворачиваемые детали + действия.
class _ErrorCard extends StatefulWidget {
  final String code;
  const _ErrorCard({required this.code});
  @override
  State<_ErrorCard> createState() => _ErrorCardState();
}

class _ErrorCardState extends State<_ErrorCard> {
  bool _expanded = false;
  @override
  Widget build(BuildContext context) {
    final e = humanizeError(widget.code, app.lang);
    return Padding(padding: const EdgeInsets.only(top: 12), child: Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: K.rose.withOpacity(0.10),
        borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 14),
        border: Border.all(color: K.rose.withOpacity(0.35), width: Style.spec.borderWidth),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Icon(Icons.error_outline, size: 18, color: K.rose),
          const SizedBox(width: 10),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(e.title, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: K.rose)),
            const SizedBox(height: 3),
            Text(e.message, style: const TextStyle(fontSize: 12, color: K.txt2, height: 1.45)),
          ])),
        ]),
        const SizedBox(height: 10),
        Row(children: [
          if (e.canRetry) ...[
            _miniBtn(app.tr('errh.retry'), Icons.refresh, () => app.toggleConnect()),
            const SizedBox(width: 8),
          ],
          if (e.needAdmin) ...[
            _miniBtn(app.tr('err.restart_admin'), Icons.admin_panel_settings, () => app.requestAdmin()),
            const SizedBox(width: 8),
          ],
          _miniBtn(_expanded ? app.tr('errh.hide') : app.tr('errh.details'),
              _expanded ? Icons.expand_less : Icons.expand_more,
              () => setState(() => _expanded = !_expanded)),
        ]),
        if (_expanded) Padding(padding: const EdgeInsets.only(top: 10), child: Container(
          width: double.infinity,
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(color: const Color(0x33000000), borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 8)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SelectableText(e.raw, style: mono(size: 11, color: K.muted)),
            const SizedBox(height: 8),
            InkWell(onTap: () {
              Clipboard.setData(ClipboardData(text: e.raw));
              ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(app.tr('errh.copy')), duration: const Duration(seconds: 1)));
            }, child: Row(mainAxisSize: MainAxisSize.min, children: [
              const Icon(Icons.copy, size: 13, color: K.mint), const SizedBox(width: 6),
              Text(app.tr('errh.copy'), style: const TextStyle(fontSize: 11.5, color: K.mint, fontWeight: FontWeight.w600)),
            ])),
          ]),
        )),
      ]),
    ));
  }

  Widget _miniBtn(String label, IconData icon, VoidCallback onTap) => InkWell(
    borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 10), onTap: onTap,
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
      decoration: BoxDecoration(
        color: const Color(0x0DFFFFFF),
        borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 10),
        border: Border.all(color: K.line2, width: Style.spec.borderWidth),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 14, color: K.txt2), const SizedBox(width: 6),
        Text(label, style: const TextStyle(fontSize: 11.5, color: K.txt2, fontWeight: FontWeight.w600)),
      ]),
    ),
  );
}
