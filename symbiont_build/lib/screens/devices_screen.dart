// lib/screens/devices_screen.dart — устройства аккаунта из /v1/account/devices.
// Список с ролями (owner-class), отзыв и повышение доступны усиленному устройству.
import 'package:flutter/material.dart';
import '../main.dart';
import '../theme.dart';

class DevicesScreen extends StatefulWidget {
  const DevicesScreen({super.key});
  @override
  State<DevicesScreen> createState() => _DevicesScreenState();
}

class _DevicesScreenState extends State<DevicesScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => app.refreshAccount());
  }

  bool get _amOwner => app.devicesList.any((d) => d['current'] == true && d['role'] == 'owner');

  @override
  Widget build(BuildContext context) {
    final list = app.devicesList;
    return ListView(padding: const EdgeInsets.only(bottom: 28), children: [
      _bar(app.tr('devices.title')),
      if (list.isEmpty)
        Padding(padding: const EdgeInsets.all(28),
          child: Center(child: Text(
            app.api.token == null ? app.tr('devices.empty') : app.tr('tariffs.loading'),
            style: const TextStyle(color: K.muted)))),
      if (list.isNotEmpty) ...[
        Padding(padding: const EdgeInsets.only(bottom: 12),
          child: Text(app.tr('devices.sub'), style: const TextStyle(fontSize: 12.5, color: K.muted, height: 1.4))),
        ...list.map(_deviceCard),
      ],
    ]);
  }

  Widget _deviceCard(Map<String, dynamic> d) {
    final id = d['id'].toString();
    final isOwner = d['role'] == 'owner';
    final isCurrent = d['current'] == true;
    final revoked = d['revoked'] == true;
    final platform = (d['platform'] ?? 'unknown').toString();
    final icon = <String, IconData>{
      'windows': Icons.desktop_windows_outlined, 'linux': Icons.computer_outlined,
      'macos': Icons.laptop_mac_outlined, 'android': Icons.smartphone_outlined,
      'ios': Icons.phone_iphone_outlined,
    }[platform] ?? Icons.devices_other_outlined;

    return Padding(padding: const EdgeInsets.only(bottom: 10), child: cardBox(
      border: isCurrent ? const Color(0x3334E5B0) : null,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(icon, size: 22, color: revoked ? K.muted : K.txt2),
          const SizedBox(width: 12),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Flexible(child: Text((d['name'] ?? 'Устройство').toString(),
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: TextStyle(fontWeight: FontWeight.w600,
                  decoration: revoked ? TextDecoration.lineThrough : null,
                  color: revoked ? K.muted : K.txt))),
              if (isCurrent) _pill(app.tr('devices.current'), K.mint),
              if (revoked) _pill(app.tr('devices.revoked'), K.rose),
            ]),
            const SizedBox(height: 2),
            Text(platform, style: const TextStyle(fontSize: 11.5, color: K.muted)),
          ])),
          if (isOwner && !revoked) _pill(app.tr('devices.owner'), K.aqua),
        ]),
        if (_amOwner && !isCurrent && !revoked) ...[
          const SizedBox(height: 12),
          Row(children: [
            if (!isOwner) Expanded(child: gradButton(app.tr('devices.promote'),
              () => app.promoteDevice(id), ghost: true, icon: Icons.shield_outlined)),
            if (!isOwner) const SizedBox(width: 8),
            Expanded(child: gradButton(app.tr('devices.revoke'),
              () => app.revokeDevice(id), ghost: true, icon: Icons.logout)),
          ]),
        ],
      ]),
    ));
  }

  Widget _pill(String t, Color c) => Padding(padding: const EdgeInsets.only(left: 8),
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(99)),
      child: Text(t, style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: c)),
    ));

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
