// lib/screens/scan_screen.dart — РЕАЛЬНАЯ диагностика сети (как 2ip + спидтест +
// проверка блокировок по слоям DNS/TCP/TLS) и авто-подбор стратегии обхода.
import 'package:flutter/material.dart';
import '../main.dart';
import '../theme.dart';
import '../style_engine.dart';
import '../engine/engine.dart';

class ScanScreen extends StatelessWidget {
  const ScanScreen({super.key});

  @override
  Widget build(BuildContext context) {
    if (!app.diagnosed && !app.diagnosing) {
      return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        titleText(app.tr('diag.title')),
        subText(app.tr('diag.sub')),
        const SizedBox(height: 26),
        const Center(child: Icon(Icons.travel_explore, size: 50, color: K.muted)),
        const SizedBox(height: 14),
        Center(child: Padding(padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Text(app.tr('diag.hint'), textAlign: TextAlign.center, style: const TextStyle(color: K.txt2, height: 1.5)))),
        const SizedBox(height: 22),
        gradButton(app.tr('diag.run'), () => app.runDiagnostics(), icon: Icons.search),
      ]);
    }
    // прогресс + результаты (показываем то, что уже есть, не дожидаясь конца)
    final blocked = app.blockedHosts;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      titleText(app.tr('diag.title')),
      if (app.diagnosing) Padding(padding: const EdgeInsets.only(top: 8, bottom: 4),
        child: Row(children: [
          const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2.2, valueColor: AlwaysStoppedAnimation(K.mint))),
          const SizedBox(width: 10),
          Text(app.tr('diag.running'), style: const TextStyle(color: K.txt2, fontSize: 12.5)),
        ])),
      const SizedBox(height: 12),
      _connCard(),
      const SizedBox(height: 12),
      _pulseCard(),
      if (app.status.phase == ConnPhase.on) ...[
        const SizedBox(height: 12),
        _liveConnPanel(),
      ],
      const SizedBox(height: 12),
      _metrics(),
      const SizedBox(height: 16),
      sectionLabel(app.tr('diag.sites')),
      if (app.hostStatus.isEmpty && app.diagnosing)
        Padding(padding: const EdgeInsets.symmetric(vertical: 6),
          child: Text(app.tr('diag.checking'), style: const TextStyle(color: K.muted, fontSize: 12))),
      ...app.hostStatus.entries.map((e) => _hostRow(e.key, e.value)),
      if (app.diagnosed) ...[
        const SizedBox(height: 14),
        _recommendation(blocked),
        if (blocked.isNotEmpty) ...[
          const SizedBox(height: 12),
          if (app.tuning)
            Column(children: [
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2.4, valueColor: AlwaysStoppedAnimation(K.mint))),
                const SizedBox(width: 10),
                Flexible(child: Text(app.tr('diag.tuning'), style: const TextStyle(color: K.txt2))),
              ]),
              if (app.tuneProgress.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8),
                child: Text(app.tuneProgress, textAlign: TextAlign.center, style: mono(size: 11, color: K.muted))),
            ])
          else
            gradButton(app.tr('diag.tune'), () => app.autoTune(), icon: Icons.auto_fix_high),
          if (app.tuneTried && !app.tuning) _tuneResult(),
        ],
        const SizedBox(height: 12),
        gradButton(app.tr('diag.again'), () => app.runDiagnostics(), ghost: true),
      ],
      const SizedBox(height: 16),
      _monitorCard(),
      const SizedBox(height: 16),
      _trafficCard(),
    ]);
  }

  // ── UI карты трафика «что куда идёт» (тумблер + список потоков) ──
  Widget _trafficCard() {
    Color ruleColor(String r) => r == 'direct' ? K.amber : (r == 'reject' ? K.rose : K.mint);
    String ruleLabel(String r) => r == 'direct' ? app.tr('traffic.direct') : (r == 'reject' ? app.tr('traffic.reject') : app.tr('traffic.proxy'));
    String fmt(int b) {
      if (b >= 1048576) return '${(b / 1048576).toStringAsFixed(1)} MB';
      if (b >= 1024) return '${(b / 1024).toStringAsFixed(0)} KB';
      return '$b B';
    }
    return cardBox(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Icon(Icons.lan_outlined, size: 18, color: K.mint),
        const SizedBox(width: 8),
        Text(app.tr('traffic.title'), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
        const Spacer(),
        Switch(value: app.trafficEnabled, activeColor: K.mint, onChanged: (v) => app.setTrafficEnabled(v)),
      ]),
      if (!app.trafficEnabled)
        Padding(padding: const EdgeInsets.only(top: 4),
          child: Text(app.tr('traffic.off'), style: const TextStyle(color: K.muted, fontSize: 12.5)))
      else if (app.status.phase != ConnPhase.on)
        Padding(padding: const EdgeInsets.only(top: 4),
          child: Text(app.tr('traffic.needconn'), style: const TextStyle(color: K.muted, fontSize: 12.5)))
      else if (app.traffic.isEmpty)
        Padding(padding: const EdgeInsets.only(top: 8),
          child: Row(children: [
            const SizedBox(width: 12, height: 12, child: CircularProgressIndicator(strokeWidth: 1.6, color: K.mint)),
            const SizedBox(width: 10),
            Text(app.tr('traffic.empty'), style: const TextStyle(color: K.muted, fontSize: 12.5)),
          ]))
      else ...[
        const SizedBox(height: 10),
        ...app.traffic.take(12).map((t) => Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Row(children: [
            Container(width: 7, height: 7, decoration: BoxDecoration(color: ruleColor(t.rule), shape: BoxShape.circle)),
            const SizedBox(width: 9),
            Expanded(child: Text(t.host, overflow: TextOverflow.ellipsis, style: mono(size: 12.5, color: K.txt))),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(color: ruleColor(t.rule).withOpacity(0.14), borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 20)),
              child: Text(ruleLabel(t.rule), style: TextStyle(color: ruleColor(t.rule), fontSize: 10.5, fontWeight: FontWeight.w600)),
            ),
            const SizedBox(width: 8),
            Text('↓${fmt(t.down)}', style: mono(size: 11, color: K.muted)),
          ]),
        )),
        if (app.traffic.length > 12)
          Padding(padding: const EdgeInsets.only(top: 2),
            child: Text('+${app.traffic.length - 12} ${app.tr('traffic.more')}', style: const TextStyle(color: K.muted, fontSize: 11.5))),
      ],
    ]));
  }

  // ── Живая панель подключения (вдохновлено Discord Connection Info) ──
  Widget _liveConnPanel() {
    String fmtDur(Duration d) {
      final h = d.inHours, m = d.inMinutes % 60, s = d.inSeconds % 60;
      if (h > 0) return '${h}ч ${m}м';
      if (m > 0) return '${m}м ${s}с';
      return '${s}с';
    }
    final hb = {'good': K.mint, 'ok': K.amber, 'bad': K.rose, 'unknown': K.muted}[app.monHealth] ?? K.muted;
    return cardBox(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Icon(Icons.insights, size: 18, color: hb),
        const SizedBox(width: 8),
        Text(app.tr('conn.title'), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
        const Spacer(),
        Container(width: 8, height: 8, decoration: BoxDecoration(color: hb, shape: BoxShape.circle)),
        const SizedBox(width: 6),
        Text(app.tr('mon.health.${app.monHealth}'), style: TextStyle(color: hb, fontSize: 11.5, fontWeight: FontWeight.w600)),
      ]),
      const SizedBox(height: 12),
      SizedBox(height: 48, width: double.infinity,
        child: CustomPaint(painter: _PingGraph(List.of(app.pingHistory), hb))),
      const SizedBox(height: 12),
      Wrap(spacing: 10, runSpacing: 10, children: [
        _stat(app.tr('conn.ping'), app.monPingMs != null ? '${app.monPingMs}' : '—', 'ms'),
        _stat(app.tr('conn.jitter'), app.monJitter > 0 ? app.monJitter.toStringAsFixed(0) : '—', 'ms'),
        _stat(app.tr('conn.loss'), app.monLossPct != null ? app.monLossPct!.toStringAsFixed(0) : '—', '%'),
        _stat(app.tr('conn.speed'), app.speedtestMbps != null ? app.speedtestMbps!.toStringAsFixed(1) : '—', 'Mbps'),
        _stat(app.tr('conn.uptime'), fmtDur(app.connectedFor), ''),
        _stat(app.tr('conn.proto'), (app.status.protocol ?? '—').toUpperCase(), ''),
      ]),
    ]));
  }

  Widget _stat(String label, String value, String unit) => Container(
    width: 96,
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
    decoration: BoxDecoration(
      color: const Color(0x0AFFFFFF),
      borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 12),
      border: Border.all(color: K.line, width: Style.spec.borderWidth),
    ),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: const TextStyle(fontSize: 10.5, color: K.muted)),
      const SizedBox(height: 3),
      Row(crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
        Flexible(child: Text(value, overflow: TextOverflow.ellipsis, style: mono(size: 17, color: K.txt))),
        if (unit.isNotEmpty) ...[const SizedBox(width: 3), Text(unit, style: const TextStyle(fontSize: 10, color: K.muted))],
      ]),
    ]),
  );

  // ── UI модуля мониторинга (с тумблером и спидтестом) ──
  Widget _monitorCard() {
    final hb = {'good': K.mint, 'ok': K.amber, 'bad': K.rose, 'unknown': K.muted}[app.monHealth] ?? K.muted;
    return cardBox(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Icon(Icons.monitor_heart, size: 18, color: hb),
        const SizedBox(width: 8),
        Text(app.tr('mon.title'), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
        const Spacer(),
        // тумблер модуля — выключил, и он молчит
        Switch(value: app.monitorEnabled, activeColor: K.mint, onChanged: (v) => app.setMonitorEnabled(v)),
      ]),
      if (!app.monitorEnabled)
        Padding(padding: const EdgeInsets.only(top: 4),
          child: Text(app.tr('mon.off'), style: const TextStyle(color: K.muted, fontSize: 12.5)))
      else ...[
        const SizedBox(height: 8),
        Row(children: [
          _monPill(app.tr('mon.health.${app.monHealth}'), hb),
          const SizedBox(width: 10),
          Text(app.monPingMs != null ? '${app.monPingMs} ms' : '—', style: mono(size: 13, color: K.txt)),
          const SizedBox(width: 10),
          Text(app.monLossPct != null ? '${app.tr('mon.loss')} ${app.monLossPct!.toStringAsFixed(0)}%' : '', style: mono(size: 12, color: K.muted)),
          if (app.monitorRunning) ...[const SizedBox(width: 8),
            const SizedBox(width: 11, height: 11, child: CircularProgressIndicator(strokeWidth: 1.6, color: K.mint))],
        ]),
        const SizedBox(height: 12),
        gradButton(
          app.speedtestRunning ? app.tr('mon.testing') : '${app.tr('mon.speedtest')}${app.speedtestMbps != null ? ' · ${app.speedtestMbps!.toStringAsFixed(1)} Mbps' : ''}',
          () => app.runSpeedtest(), icon: Icons.speed, ghost: true),
      ],
    ]));
  }

  Widget _monPill(String t, Color c) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
    decoration: BoxDecoration(color: c.withOpacity(0.14), borderRadius: BorderRadius.circular(20)),
    child: Text(t, style: TextStyle(color: c, fontSize: 11.5, fontWeight: FontWeight.w600)));

  Widget _connCard() => cardBox(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
    _kv(app.tr('diag.ip'), app.netInfo['ip'], fallback: app.tr('diag.unknown')),
    _kv(app.tr('diag.isp'), _brandedIsp(app.netInfo['isp']), fallback: app.tr('diag.unknown')),
    _kv(app.tr('diag.country'), app.netInfo['country'], fallback: app.tr('diag.unknown')),
    _kv(app.tr('diag.as'), _brandedIsp(app.netInfo['as']), last: true),
  ]));

  /// Узнаваемое имя оператора: к юр.названию (PJSC VimpelCom / AS3216 …) добавляем
  /// потребительский бренд (Билайн), чтобы пользователь не путался.
  String? _brandedIsp(String? raw) {
    if (raw == null || raw.isEmpty) return raw;
    final l = raw.toLowerCase();
    const brands = {
      'vimpelcom': 'Билайн', 'vimpel-com': 'Билайн', 'as3216': 'Билайн', 'beeline': 'Билайн',
      'mts ': 'МТС', 'mobile telesystems': 'МТС', 'as8359': 'МТС',
      'megafon': 'МегаФон', 'as31133': 'МегаФон',
      'rostelecom': 'Ростелеком', 'as12389': 'Ростелеком',
      'tele2': 'Tele2', 't2 mobile': 'Tele2',
      'yota': 'Yota', 'er-telecom': 'Дом.ru', 'ertelecom': 'Дом.ru',
    };
    for (final e in brands.entries) {
      if (l.contains(e.key) && !raw.contains(e.value)) return '$raw (${e.value})';
    }
    return raw;
  }

  Widget _kv(String k, String? v, {bool last = false, String fallback = '—'}) => Padding(
    padding: EdgeInsets.only(bottom: last ? 0 : 8),
    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      SizedBox(width: 92, child: Text(k, style: const TextStyle(fontSize: 12, color: K.muted))),
      Expanded(child: Text((v == null || v.isEmpty) ? fallback : v, style: mono(size: 12.5, color: K.txt))),
    ]),
  );

  Widget _metrics() => Row(children: [
    Expanded(child: _metric(app.netPing != null ? '${app.netPing}' : '—', '${app.tr('diag.ping')} ms')),
    const SizedBox(width: 11),
    Expanded(child: _metric(app.netSpeed != null ? app.netSpeed!.toStringAsFixed(1) : '—', '${app.tr('diag.speed')} Mbps')),
  ]);

  Widget _metric(String v, String k) => Container(
    padding: const EdgeInsets.all(13),
    decoration: BoxDecoration(color: K.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: K.line)),
    child: Column(children: [
      FittedBox(child: Text(v, style: mono(size: 19, color: K.txt, w: FontWeight.w600))),
      const SizedBox(height: 3),
      Text(k.toUpperCase(), maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 10.5, color: K.muted, letterSpacing: 0.8)),
    ]),
  );

  Widget _hostRow(String host, String status) {
    final cfg = {
      'ok':  [K.mint,  'diag.host.ok',  Icons.check_circle_outline],
      'tls': [K.rose,  'diag.host.tls', Icons.gpp_bad_outlined],
      'tcp': [K.amber, 'diag.host.tcp', Icons.block_outlined],
      'dns': [K.amber, 'diag.host.dns', Icons.dns_outlined],
    }[status] ?? [K.muted, 'diag.host.tls', Icons.help_outline];
    final color = cfg[0] as Color;
    return Padding(padding: const EdgeInsets.only(bottom: 8), child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 11),
      decoration: BoxDecoration(color: K.surface, borderRadius: BorderRadius.circular(12), border: Border.all(color: K.line)),
      child: Row(children: [
        Icon(cfg[2] as IconData, size: 17, color: color),
        const SizedBox(width: 10),
        Expanded(child: Text(host, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500))),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
          decoration: BoxDecoration(color: color.withOpacity(0.13), borderRadius: BorderRadius.circular(7)),
          child: Text(app.tr(cfg[1] as String), style: mono(size: 10.5, color: color, w: FontWeight.w600)),
        ),
      ]),
    ));
  }

  Widget _recommendation(List<String> blocked) {
    final String key;
    final Color color;
    if (blocked.isEmpty) { key = 'diag.recommend.none'; color = K.mint; }
    else if (app.dpiDetected) { key = 'diag.recommend.dpi'; color = K.amber; }
    else { key = 'diag.recommend.partial'; color = K.amber; }
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(color: color.withOpacity(0.10), borderRadius: BorderRadius.circular(13), border: Border.all(color: color.withOpacity(0.35))),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(blocked.isEmpty ? Icons.verified_outlined : Icons.lightbulb_outline, size: 18, color: color),
        const SizedBox(width: 10),
        Expanded(child: Text(app.tr(key), style: const TextStyle(fontSize: 12.5, color: K.txt2, height: 1.45))),
      ]),
    );
  }

  Widget _tuneResult() {
    final ok = app.tunedStrategy != null;
    final color = ok ? K.mint : K.amber;
    final text = ok ? '${app.tr('diag.tuned')} ${app.tunedStrategy}' : app.tr('diag.tune_fail');
    return Padding(padding: const EdgeInsets.only(top: 10), child: Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: color.withOpacity(0.10), borderRadius: BorderRadius.circular(12), border: Border.all(color: color.withOpacity(0.35))),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(ok ? Icons.check_circle_outline : Icons.info_outline, size: 16, color: color),
        const SizedBox(width: 9),
        Expanded(child: Text(text, style: const TextStyle(fontSize: 11.5, color: K.txt2, height: 1.4))),
      ]),
    ));
  }

  // ── UI Pulse-мониторинга региона ──
  Widget _pulseCard() {
    final lvl = app.pulseLevel;
    final c = {'good': K.mint, 'partial': K.amber, 'bad': K.rose, 'unknown': K.muted}[lvl] ?? K.muted;
    final total = app.pulseTotal;
    return cardBox(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Icon(Icons.monitor_heart_outlined, size: 18, color: c),
        const SizedBox(width: 8),
        Expanded(child: Text(app.tr('pulse.title'), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14))),
        Text(app.tr('pulse.$lvl'), style: TextStyle(color: c, fontSize: 12, fontWeight: FontWeight.w700)),
      ]),
      const SizedBox(height: 4),
      Text(app.tr('pulse.sub'), style: const TextStyle(fontSize: 11.5, color: K.muted, height: 1.4)),
      if (total > 0) ...[
        const SizedBox(height: 10),
        ClipRRect(
          borderRadius: BorderRadius.circular(Style.spec.radius == 0 ? 0 : 6),
          child: LinearProgressIndicator(
            value: app.pulseReachable / total,
            minHeight: 8, backgroundColor: const Color(0x14FFFFFF), color: c,
          ),
        ),
        const SizedBox(height: 6),
        Text('${app.pulseReachable} / $total ${app.tr('pulse.reachable')}', style: mono(size: 11.5, color: K.muted)),
      ] else
        Padding(padding: const EdgeInsets.only(top: 8),
          child: Text(app.tr('pulse.empty'), style: const TextStyle(fontSize: 12, color: K.muted))),
    ]));
  }

}

/// Живой график пинга для Connection-панели.
class _PingGraph extends CustomPainter {
  final List<double> data;
  final Color color;
  _PingGraph(this.data, this.color);
  @override
  void paint(Canvas canvas, Size size) {
    if (size.width <= 0 || size.height <= 0) return; // защита от invalid matrix
    // сетка
    final grid = Paint()..color = const Color(0x14FFFFFF)..strokeWidth = 1;
    for (int i = 1; i < 3; i++) {
      final y = size.height * i / 3;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }
    if (data.length < 2) return;
    final maxV = (data.reduce((a, b) => a > b ? a : b)).clamp(1, 100000).toDouble();
    final minV = (data.reduce((a, b) => a < b ? a : b)).toDouble();
    final range = (maxV - minV).abs() < 1 ? 1.0 : (maxV - minV);
    final dx = size.width / (data.length - 1);
    final path = Path();
    final fill = Path();
    for (int i = 0; i < data.length; i++) {
      final x = dx * i;
      final y = size.height - ((data[i] - minV) / range) * (size.height - 6) - 3;
      if (i == 0) { path.moveTo(x, y); fill.moveTo(x, size.height); fill.lineTo(x, y); }
      else { path.lineTo(x, y); fill.lineTo(x, y); }
    }
    fill.lineTo(size.width, size.height);
    fill.close();
    canvas.drawPath(fill, Paint()..shader = LinearGradient(
      begin: Alignment.topCenter, end: Alignment.bottomCenter,
      colors: [color.withOpacity(0.28), color.withOpacity(0.0)]).createShader(Offset.zero & size));
    canvas.drawPath(path, Paint()..color = color..style = PaintingStyle.stroke..strokeWidth = 2
      ..strokeCap = StrokeCap.round..strokeJoin = StrokeJoin.round);
    // точка на последнем значении
    final lastX = dx * (data.length - 1);
    final lastY = size.height - ((data.last - minV) / range) * (size.height - 6) - 3;
    canvas.drawCircle(Offset(lastX, lastY), 3, Paint()..color = color);
  }
  @override
  bool shouldRepaint(_PingGraph old) => old.data.length != data.length || old.color != color
      || (data.isNotEmpty && old.data.isNotEmpty && old.data.last != data.last);
}
