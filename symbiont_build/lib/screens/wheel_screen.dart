// lib/screens/wheel_screen.dart — колесо фортуны: бесплатный ежедневный бонус.
// Результат честный и детерминированный (бэкенд /v1/wheel). Один спин в сутки.
import 'package:flutter/material.dart';
import '../main.dart';
import '../theme.dart';

class WheelScreen extends StatefulWidget {
  const WheelScreen({super.key});
  @override
  State<WheelScreen> createState() => _WheelScreenState();
}

class _WheelScreenState extends State<WheelScreen> {
  bool _spinning = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => app.loadWheel());
  }

  String _segLabel(Map seg) {
    final amount = seg['amount'] ?? 0;
    final kind = (seg['kind'] ?? 'days').toString();
    return kind == 'days' ? '+$amount ${app.tr('wheel.days')}' : '+$amount мин';
  }

  Future<void> _spin() async {
    setState(() => _spinning = true);
    await app.spinWheel();
    if (mounted) setState(() => _spinning = false);
  }

  @override
  Widget build(BuildContext context) {
    final w = app.wheel;
    final segs = ((w?['segments'] as List?) ?? const []).cast<Map<String, dynamic>>();
    final available = w?['available'] == true;
    final todayIdx = w?['today_index'] as int?;
    final lastPrize = w?['last_prize'] as Map?;

    return ListView(padding: const EdgeInsets.only(bottom: 28), children: [
      _bar(app.tr('wheel.title')),
      if (w == null)
        Padding(padding: const EdgeInsets.all(28),
          child: Center(child: Text(app.tr('tariffs.loading'), style: const TextStyle(color: K.muted))))
      else ...[
        Text(app.tr('wheel.sub'), style: const TextStyle(fontSize: 12.5, color: K.muted, height: 1.4)),
        const SizedBox(height: 16),
        // Сегменты — сетка; выигравший за сегодня подсвечен.
        GridView.count(
          crossAxisCount: 3, shrinkWrap: true, physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 10, crossAxisSpacing: 10, childAspectRatio: 1.5,
          children: List.generate(segs.length, (i) {
            final won = todayIdx == i;
            return Container(
              alignment: Alignment.center,
              decoration: BoxDecoration(
                gradient: won ? K.grad : null,
                color: won ? null : const Color(0x0AFFFFFF),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: won ? Colors.transparent : K.line2)),
              child: Text(_segLabel(segs[i]), style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700,
                color: won ? const Color(0xFF04201A) : K.txt)),
            );
          }),
        ),
        const SizedBox(height: 18),
        if (available)
          gradButton(_spinning ? '…' : app.tr('wheel.spin'), _spinning ? () {} : _spin, icon: Icons.casino_outlined)
        else ...[
          if (lastPrize != null)
            cardBox(child: Row(children: [
              const Icon(Icons.card_giftcard, size: 20, color: K.mint),
              const SizedBox(width: 10),
              Expanded(child: Text('${app.tr('wheel.today')}: ${_segLabel(lastPrize)}',
                style: const TextStyle(fontWeight: FontWeight.w600))),
            ])),
          const SizedBox(height: 10),
          Center(child: Text(app.tr('wheel.comeback'),
            style: const TextStyle(fontSize: 12.5, color: K.muted))),
        ],
      ],
    ]);
  }

  Widget _bar(String title) => Padding(
    padding: const EdgeInsets.only(bottom: 16),
    child: Row(children: [
      Tooltip(message: app.tr('common.back'), child: Semantics(button: true, label: app.tr('common.back'),
        child: InkWell(borderRadius: BorderRadius.circular(11), onTap: app.closeOverlay,
          child: SizedBox(width: 44, height: 44, child: Center(child: Container(width: 36, height: 36,
            decoration: BoxDecoration(color: const Color(0x0AFFFFFF), borderRadius: BorderRadius.circular(11), border: Border.all(color: K.line)),
            child: const Icon(Icons.chevron_left, size: 20, color: K.txt2))))))),
      const SizedBox(width: 10),
      Text(title, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
    ]),
  );
}
