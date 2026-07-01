// lib/screens/tariffs_screen.dart — тарифы из серверной экономики.
// Всё динамически: цены/фичи/способы оплаты приходят с бэкенда (GET /v1/config/economy),
// текущая подписка — из /v1/billing/status. Регион по языку (ru → ₽/СБП/МИР, иначе $).
import 'package:flutter/material.dart';
import '../main.dart';
import '../theme.dart';

class TariffsScreen extends StatefulWidget {
  const TariffsScreen({super.key});
  @override
  State<TariffsScreen> createState() => _TariffsScreenState();
}

class _TariffsScreenState extends State<TariffsScreen> {
  bool _year = false;
  String _method = 'mir';
  String? _note;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (app.economy == null) app.loadEconomy();
      if (app.billing == null) app.refreshAccount();
    });
  }

  String get _region => app.lang == 'ru' ? 'ru' : 'intl';

  String _tierName(String id) => {'free': 'Free', 'premium': 'Premium', 'ultimate': 'Ultimate'}[id] ?? id;

  @override
  Widget build(BuildContext context) {
    final eco = app.economy;
    final region = _region;
    // если для региона нет способов оплаты по выбранному — подправим дефолт
    final pays = ((eco?['payments'] as Map?)?[region] as List?)?.cast<String>() ?? const ['crypto'];
    if (!pays.contains(_method) && pays.isNotEmpty) _method = pays.first;

    return ListView(padding: const EdgeInsets.only(bottom: 28), children: [
      _bar(),
      if (eco == null)
        Padding(padding: const EdgeInsets.all(28),
          child: Center(child: Text(app.tr('tariffs.loading'), style: const TextStyle(color: K.muted))))
      else ...[
        _statusCard(),
        const SizedBox(height: 14),
        _durationToggle(),
        const SizedBox(height: 12),
        _methodPicker(pays),
        const SizedBox(height: 14),
        _tierCard(eco, 'premium', [
          app.tr('tariffs.relay.full'), app.tr('tariffs.gamemode'), app.tr('tariffs.devices.unlim'),
        ]),
        const SizedBox(height: 12),
        _tierCard(eco, 'ultimate', [
          app.tr('tariffs.relay.full'), app.tr('tariffs.gamemode'),
          app.tr('tariffs.devices.unlim'), app.tr('tariffs.dedip'),
        ], anchor: true),
        const SizedBox(height: 12),
        _balanceCard(eco),
        if (_note != null) ...[
          const SizedBox(height: 14),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(color: const Color(0x1434E5B0), borderRadius: BorderRadius.circular(12), border: Border.all(color: const Color(0x3334E5B0))),
            child: Row(children: [
              const Icon(Icons.check_circle, size: 18, color: K.mint),
              const SizedBox(width: 8),
              Expanded(child: Text(_note!, style: const TextStyle(fontSize: 13, color: K.txt))),
            ]),
          ),
        ],
      ],
    ]);
  }

  Widget _bar() => Padding(
    padding: const EdgeInsets.only(bottom: 16),
    child: Row(children: [
      Tooltip(message: app.tr('common.back'), child: Semantics(button: true, label: app.tr('common.back'),
        child: InkWell(borderRadius: BorderRadius.circular(11), onTap: app.closeOverlay,
          child: SizedBox(width: 44, height: 44, child: Center(child: Container(width: 36, height: 36,
            decoration: BoxDecoration(color: const Color(0x0AFFFFFF), borderRadius: BorderRadius.circular(11), border: Border.all(color: K.line)),
            child: const Icon(Icons.chevron_left, size: 20, color: K.txt2))))))),
      const SizedBox(width: 10),
      Text(app.tr('tariffs.title'), style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
    ]),
  );

  Widget _statusCard() {
    final sub = (app.billing?['subscription'] as Map?) ?? const {};
    final tier = (sub['tier'] ?? 'free').toString();
    final mode = (sub['mode'] ?? 'period').toString();
    final days = sub['days_left'] ?? 0;
    final mins = sub['active_minutes_left'] ?? 0;
    final detail = mode == 'balance'
        ? '$mins ${app.tr('tariffs.minutesleft')}'
        : '$days ${app.tr('tariffs.daysleft')}';
    return cardBox(child: Row(children: [
      Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(app.tr('tariffs.current'), style: const TextStyle(fontSize: 12, color: K.muted)),
        const SizedBox(height: 4),
        Text(_tierName(tier), style: Tg.title),
      ])),
      Text(detail, style: mono(size: 13, color: K.txt2)),
    ]));
  }

  Widget _durationToggle() {
    Widget seg(String label, bool yr) {
      final on = _year == yr;
      return Expanded(child: InkWell(
        borderRadius: BorderRadius.circular(9),
        onTap: () => setState(() => _year = yr),
        child: Container(
          alignment: Alignment.center, padding: const EdgeInsets.symmetric(vertical: 10),
          decoration: BoxDecoration(gradient: on ? K.grad : null, borderRadius: BorderRadius.circular(9)),
          child: Text(label, style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700,
              color: on ? const Color(0xFF04201A) : K.txt2)),
        ),
      ));
    }
    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(color: const Color(0x0AFFFFFF), borderRadius: BorderRadius.circular(12), border: Border.all(color: K.line)),
      child: Row(children: [seg(app.tr('tariffs.month'), false), const SizedBox(width: 4), seg(app.tr('tariffs.year'), true)]),
    );
  }

  Widget _methodPicker(List<String> pays) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(app.tr('tariffs.method'), style: const TextStyle(fontSize: 12.5, color: K.muted)),
      const SizedBox(height: 8),
      Wrap(spacing: 8, runSpacing: 8, children: pays.map((m) {
        final on = _method == m;
        return Semantics(button: true, selected: on, inMutuallyExclusiveGroup: true, label: app.tr('pay.$m'),
          child: InkWell(borderRadius: BorderRadius.circular(10), onTap: () => setState(() => _method = m),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
              decoration: BoxDecoration(gradient: on ? K.grad : null, borderRadius: BorderRadius.circular(10), border: Border.all(color: on ? Colors.transparent : K.line2)),
              child: Text(app.tr('pay.$m'), style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: on ? const Color(0xFF04201A) : K.txt2)),
            )));
      }).toList()),
    ]);
  }

  String _priceStr(Map eco, String id) {
    final pr = (eco['prices'] as Map?)?[_region] as Map?;
    final cur = (pr?['currency'] == 'USD') ? '\$' : '₽';
    final v = (pr?[id] as Map?)?[_year ? 'year' : 'month'];
    if (v == null) return '—';
    return cur == '₽' ? '$v ₽' : '\$$v';
  }

  Widget _tierCard(Map eco, String id, List<String> feats, {bool anchor = false}) {
    final product = '${id}_${_year ? 'year' : 'month'}';
    return cardBox(border: anchor ? const Color(0x3334E5B0) : null, child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Text(_tierName(id), style: Tg.title),
        const Spacer(),
        if (anchor) Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
          decoration: BoxDecoration(color: const Color(0x1F34E5B0), borderRadius: BorderRadius.circular(99)),
          child: Text(app.tr('tariffs.bestvalue'), style: const TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: K.mint)),
        ),
      ]),
      const SizedBox(height: 8),
      Row(crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
        Text(_priceStr(eco, id), style: mono(size: 22, color: K.txt, w: FontWeight.w700)),
        const SizedBox(width: 6),
        Text('/ ${_year ? app.tr('tariffs.year') : app.tr('tariffs.month')}', style: const TextStyle(fontSize: 12, color: K.muted)),
      ]),
      const SizedBox(height: 12),
      ...feats.map((f) => Padding(padding: const EdgeInsets.only(bottom: 6), child: Row(children: [
        const Icon(Icons.check, size: 15, color: K.mint),
        const SizedBox(width: 8),
        Expanded(child: Text(f, style: const TextStyle(fontSize: 13, color: K.txt2))),
      ]))),
      const SizedBox(height: 12),
      gradButton('${app.tr('tariffs.buy')} · ${app.tr('pay.$_method')}', () => _doBuy(product)),
    ]));
  }

  Widget _balanceCard(Map eco) {
    final pr = ((eco['prices'] as Map?)?['balance'] as Map?)?[_region] as Map?;
    final p100 = pr?['100h'];
    final cur = ((eco['prices'] as Map?)?[_region] as Map?)?['currency'] == 'USD' ? '\$' : '₽';
    return cardBox(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        const Icon(Icons.hourglass_bottom, size: 18, color: K.txt2),
        const SizedBox(width: 8),
        Text(app.tr('tariffs.balance'), style: Tg.label),
        const Spacer(),
        if (p100 != null) Text(cur == '₽' ? '$p100 ₽' : '\$$p100', style: mono(size: 14, color: K.txt)),
      ]),
      const SizedBox(height: 6),
      const Text('100 ч', style: TextStyle(fontSize: 12, color: K.muted)),
      const SizedBox(height: 10),
      gradButton('${app.tr('tariffs.buy')} · ${app.tr('pay.$_method')}', () => _doBuy('balance_100h'), ghost: true),
    ]));
  }

  Future<void> _doBuy(String product) async {
    final res = await app.buy(product, _method);
    if (!mounted) return;
    setState(() => _note = res != null && res['status'] == 'completed'
        ? '${app.tr('tariffs.buy')}: ${res['status']} (${app.tr('pay.$_method')})'
        : 'error');
  }
}
