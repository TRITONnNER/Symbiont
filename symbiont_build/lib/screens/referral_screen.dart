// lib/screens/referral_screen.dart — друзья и репутация из /v1/referral.
// Инвайт-код, уровень репутации, лимит выплат, статистика. Приглашать можно
// неограниченно — метятся только выплаты (объясняем это пользователю).
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../main.dart';
import '../theme.dart';

class ReferralScreen extends StatefulWidget {
  const ReferralScreen({super.key});
  @override
  State<ReferralScreen> createState() => _ReferralScreenState();
}

class _ReferralScreenState extends State<ReferralScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => app.refreshAccount());
  }

  String _repName(Map rep) {
    final n = rep['name'];
    if (n is Map) return (n[app.lang] ?? n['en'] ?? n['ru'] ?? rep['level'] ?? '—').toString();
    return (rep['level'] ?? '—').toString();
  }

  @override
  Widget build(BuildContext context) {
    final ref = app.referral;
    return ListView(padding: const EdgeInsets.only(bottom: 28), children: [
      _bar(app.tr('referral.title')),
      if (ref == null)
        Padding(padding: const EdgeInsets.all(28),
          child: Center(child: Text(
            app.api.token == null ? app.tr('devices.empty') : app.tr('tariffs.loading'),
            style: const TextStyle(color: K.muted))))
      else ...[
        Padding(padding: const EdgeInsets.only(bottom: 14),
          child: Text(app.tr('referral.sub'), style: const TextStyle(fontSize: 12.5, color: K.muted, height: 1.4))),
        _codeCard(ref),
        const SizedBox(height: 12),
        _reputationCard(ref),
        const SizedBox(height: 12),
        _statsRow(ref),
        const SizedBox(height: 14),
        Text(app.tr('referral.howto'), style: const TextStyle(fontSize: 12, color: K.muted, height: 1.45)),
      ],
    ]);
  }

  Widget _codeCard(Map ref) {
    final code = (ref['invite_code'] ?? '—').toString();
    return cardBox(child: Row(children: [
      Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(app.tr('referral.code'), style: const TextStyle(fontSize: 12, color: K.muted)),
        const SizedBox(height: 4),
        Text(code, style: mono(size: 18, color: K.txt, w: FontWeight.w700)),
      ])),
      Tooltip(message: app.tr('invite.copy'), child: Semantics(button: true, label: app.tr('invite.copy'),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () { Clipboard.setData(ClipboardData(text: code)); _toast(app.tr('copied')); },
          child: Container(
            constraints: const BoxConstraints(minHeight: 44),
            alignment: Alignment.center,
            padding: const EdgeInsets.symmetric(horizontal: 16),
            decoration: BoxDecoration(gradient: K.grad, borderRadius: BorderRadius.circular(12)),
            child: const Icon(Icons.copy, size: 18, color: Color(0xFF04201A)),
          )))),
    ]));
  }

  Widget _reputationCard(Map ref) {
    final rep = (ref['reputation'] as Map?) ?? const {};
    final cap = rep['payout_cap_month'];
    final used = rep['payouts_used'] ?? 0;
    final capStr = (cap == null || cap == 0) ? app.tr('referral.unlimited') : '$cap';
    final progress = (cap == null || cap == 0) ? 0.4 : (cap == 0 ? 0.0 : (used / cap).clamp(0.0, 1.0));
    return cardBox(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        const Icon(Icons.military_tech_outlined, size: 20, color: K.mint),
        const SizedBox(width: 8),
        Text(app.tr('referral.level'), style: const TextStyle(fontSize: 12, color: K.muted)),
        const Spacer(),
        Text(_repName(rep), style: Tg.label),
      ]),
      const SizedBox(height: 12),
      ClipRRect(borderRadius: BorderRadius.circular(99),
        child: LinearProgressIndicator(value: progress.toDouble(), minHeight: 7,
          backgroundColor: const Color(0x14FFFFFF), valueColor: const AlwaysStoppedAnimation(K.mint))),
      const SizedBox(height: 8),
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(app.tr('referral.payouts'), style: const TextStyle(fontSize: 11.5, color: K.muted)),
        Text('$used / $capStr', style: mono(size: 12, color: K.txt2)),
      ]),
    ]));
  }

  Widget _statsRow(Map ref) {
    Widget stat(String label, String val) => Expanded(child: cardBox(child: Column(children: [
      Text(val, style: mono(size: 22, color: K.txt, w: FontWeight.w700)),
      const SizedBox(height: 3),
      Text(label, style: const TextStyle(fontSize: 11.5, color: K.muted)),
    ])));
    return Row(children: [
      stat(app.tr('referral.invited'), '${ref['invited'] ?? 0}'),
      const SizedBox(width: 12),
      stat(app.tr('referral.converted'), '${ref['converted'] ?? 0}'),
    ]);
  }

  void _toast(String m) {
    final messenger = ScaffoldMessenger.maybeOf(context);
    messenger?.showSnackBar(SnackBar(content: Text(m), duration: const Duration(seconds: 2)));
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
