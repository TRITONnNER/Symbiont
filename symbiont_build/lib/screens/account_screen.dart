// lib/screens/account_screen.dart — экран «Аккаунт».
// Реальные действия: метка (PATCH на бэк), активация ключа (redeem на бэке),
// показ токена, выход из аккаунта (очистка диска). Платёжек пока нет.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../main.dart';
import '../theme.dart';
import '../models/models.dart';

class AccountScreen extends StatefulWidget {
  const AccountScreen({super.key});
  @override
  State<AccountScreen> createState() => _AccountScreenState();
}

class _AccountScreenState extends State<AccountScreen> {
  final _label = TextEditingController();
  final _key = TextEditingController();
  bool _savingKey = false;

  @override
  void initState() {
    super.initState();
    _label.text = app.label;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (app.api.token != null) app.refreshAccount();
    });
  }
  @override
  void dispose() { _label.dispose(); _key.dispose(); super.dispose(); }

  void _toast(String m) => ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(content: Text(m), backgroundColor: K.surface3, behavior: SnackBarBehavior.floating));

  Future<void> _activate() async {
    setState(() => _savingKey = true);
    final err = await app.activateKey(_key.text);
    if (!mounted) return;
    setState(() => _savingKey = false);
    _toast(err ?? app.tr('activate.ok'));
    if (err == null) _key.clear();
  }

  Future<void> _logout() async {
    final yes = await showDialog<bool>(context: context, builder: (_) => AlertDialog(
      backgroundColor: K.surface2,
      title: Text(app.tr('logout'), style: const TextStyle(color: K.txt)),
      content: Text(app.tr('logout.confirm'), style: const TextStyle(color: K.txt2)),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: Text(app.tr('common.cancel'), style: const TextStyle(color: K.txt2))),
        TextButton(onPressed: () => Navigator.pop(context, true), child: Text(app.tr('logout'), style: const TextStyle(color: K.amber))),
      ],
    ));
    if (yes == true) await app.logout();
  }

  void _showToken() {
    final t = app.api.token ?? '—';
    showDialog(context: context, builder: (_) => AlertDialog(
      backgroundColor: K.surface2,
      title: Text(app.tr('token.title'), style: const TextStyle(color: K.txt)),
      content: SelectableText(t, style: mono(size: 13, color: K.mint)),
      actions: [
        TextButton(onPressed: () { Clipboard.setData(ClipboardData(text: t)); Navigator.pop(context); _toast(app.tr('copied')); },
          child: Text(app.tr('token.copy'), style: const TextStyle(color: K.aqua))),
        TextButton(onPressed: () => Navigator.pop(context), child: Text(app.tr('common.ok'), style: const TextStyle(color: K.txt2))),
      ],
    ));
  }

  @override
  Widget build(BuildContext context) {
    final planName = {Plan.pro: app.tr('plan.pro'), Plan.free: app.tr('plan.free'), Plan.trial: app.tr('plan.trial')}[app.plan]!;
    final paid = app.paidUntil == null ? '—' : '${app.paidUntil!.day.toString().padLeft(2, '0')}.${app.paidUntil!.month.toString().padLeft(2, '0')}.${app.paidUntil!.year}';
    const inviteCode = 'СИМБ-7K2Q'; // запасной плейсхолдер, если рефералка ещё не загружена
    final realInvite = (app.referral?['invite_code'] ?? inviteCode).toString();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      titleText(app.tr('nav.account')),
      if (app.recoveryCode != null) ...[
        Container(
          margin: const EdgeInsets.only(bottom: 14),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: const Color(0x14F2C94C),
            borderRadius: BorderRadius.circular(18), border: Border.all(color: const Color(0x4DF2C94C))),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              const Icon(Icons.vpn_key_outlined, size: 18, color: K.amber),
              const SizedBox(width: 8),
              Text(app.tr('recovery.title'), style: Tg.label),
            ]),
            const SizedBox(height: 8),
            SelectableText(app.recoveryCode!, style: mono(size: 17, color: K.txt, w: FontWeight.w700)),
            const SizedBox(height: 8),
            Text(app.tr('recovery.sub'), style: const TextStyle(fontSize: 12, color: K.txt2, height: 1.4)),
            const SizedBox(height: 12),
            Row(children: [
              Expanded(child: gradButton(app.tr('invite.copy'), () {
                Clipboard.setData(ClipboardData(text: app.recoveryCode!)); _toast(app.tr('copied'));
              }, ghost: true, icon: Icons.copy)),
              const SizedBox(width: 8),
              Expanded(child: gradButton(app.tr('recovery.saved'), app.ackRecovery, icon: Icons.check)),
            ]),
          ]),
        ),
      ],
      Container(
        margin: const EdgeInsets.only(bottom: 14),
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          gradient: const LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [K.surface2, K.surface]),
          borderRadius: BorderRadius.circular(26), border: Border.all(color: K.line2)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text(app.tr('account.label'), style: mono(size: 12, color: K.mint))),
            Tooltip(message: app.tr('token.title'), child: Semantics(button: true, label: app.tr('token.title'),
              child: InkWell(onTap: _showToken, borderRadius: BorderRadius.circular(8),
                child: Padding(padding: const EdgeInsets.all(8), child: Row(mainAxisSize: MainAxisSize.min, children: [
                  const Icon(Icons.key_outlined, size: 14, color: K.muted), const SizedBox(width: 5),
                  Text(app.tr('token.title'), style: const TextStyle(fontSize: 11.5, color: K.muted)),
                ]))))),
          ]),
          const SizedBox(height: 6),
          Text(app.label.isEmpty ? 'guest' : app.label, style: const TextStyle(fontSize: 23, fontWeight: FontWeight.w800)),
          const SizedBox(height: 12),
          TextField(controller: _label, style: mono(size: 13.5, color: K.txt),
            textInputAction: TextInputAction.done,
            onSubmitted: (v) { app.setLabel(v); _toast(app.tr('common.ok')); },
            decoration: fieldDeco(app.tr('account.label.hint'))),
          const SizedBox(height: 8),
          Text(app.tr('account.label.hint'), style: const TextStyle(fontSize: 11, color: K.muted)),
        ]),
      ),
      cardBox(child: Column(children: [
        _line(app.tr('account.sub'), pill: planName),
        _line(app.tr('account.paid'), value: paid),
        _line(app.tr('account.devices'), value: app.tr('devices.val'), valueColor: K.mint),
      ])),
      const SizedBox(height: 10),
      gradButton(app.tr('tariffs.title'), () => app.openOverlay('tariffs'), icon: Icons.workspace_premium_outlined),
      const SizedBox(height: 8),
      Row(children: [
        Expanded(child: gradButton(app.tr('devices.title'), () => app.openOverlay('devices'), ghost: true, icon: Icons.devices_outlined)),
        const SizedBox(width: 8),
        Expanded(child: gradButton(app.tr('referral.title'), () => app.openOverlay('referral'), ghost: true, icon: Icons.group_outlined)),
      ]),
      const SizedBox(height: 8),
      gradButton(app.tr('wheel.title'), () => app.openOverlay('wheel'), ghost: true, icon: Icons.casino_outlined),
      sectionLabel(app.tr('activate.label')),
      cardBox(child: Column(children: [
        TextField(controller: _key, style: mono(size: 13.5, color: K.txt), decoration: fieldDeco(app.tr('activate.ph'))),
        const SizedBox(height: 10),
        _savingKey
          ? const Center(child: Padding(padding: EdgeInsets.all(6), child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.4, valueColor: AlwaysStoppedAnimation(K.mint)))))
          : gradButton(app.tr('activate.btn'), _activate),
      ])),
      sectionLabel(app.tr('invite.label')),
      cardBox(child: Row(children: [
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(realInvite, style: const TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 3),
          Text(app.tr('invite.sub'), style: const TextStyle(fontSize: 11.5, color: K.muted)),
        ])),
        Tooltip(message: app.tr('invite.copy'), child: Semantics(button: true, label: app.tr('invite.copy'),
          child: InkWell(
            borderRadius: BorderRadius.circular(13),
            onTap: () { Clipboard.setData(ClipboardData(text: realInvite)); _toast(app.tr('copied')); },
            child: Container(
              constraints: const BoxConstraints(minHeight: 44),
              alignment: Alignment.center,
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(color: const Color(0x0DFFFFFF), borderRadius: BorderRadius.circular(13), border: Border.all(color: K.line2)),
              child: Text(app.tr('invite.copy'), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5)),
            ),
          ))),
      ])),
      const SizedBox(height: 18),
      OutlinedButton.icon(
        onPressed: _logout,
        icon: const Icon(Icons.logout, size: 17, color: K.amber),
        label: Text(app.tr('logout'), style: const TextStyle(color: K.amber, fontWeight: FontWeight.w600)),
        style: OutlinedButton.styleFrom(side: BorderSide(color: K.amber.withOpacity(0.4)), padding: const EdgeInsets.symmetric(vertical: 14),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(13))),
      ),
    ]);
  }

  Widget _line(String label, {String? value, String? pill, Color? valueColor}) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 7),
    child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
      Flexible(child: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13.5, color: K.txt2))),
      const SizedBox(width: 10),
      if (pill != null) Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(gradient: K.grad, borderRadius: BorderRadius.circular(8)),
        child: Text(pill, style: mono(size: 11, color: const Color(0xFF04201A), w: FontWeight.w600)),
      ) else Text(value ?? '', style: TextStyle(fontWeight: FontWeight.w600, color: valueColor ?? K.txt)),
    ]),
  );
}
