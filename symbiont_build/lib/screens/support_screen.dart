// lib/screens/support_screen.dart
import 'package:flutter/material.dart';
import '../main.dart';
import '../theme.dart';
import '../models/models.dart';

class SupportScreen extends StatefulWidget {
  const SupportScreen({super.key});
  @override
  State<SupportScreen> createState() => _SupportScreenState();
}

class _SupportScreenState extends State<SupportScreen> {
  final _c = TextEditingController();
  @override
  void dispose() { _c.dispose(); super.dispose(); }

  void _toast(String m) => ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(content: Text(m), backgroundColor: K.surface3, behavior: SnackBarBehavior.floating));

  @override
  Widget build(BuildContext context) {
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      titleText(app.tr('support.title')),
      const SizedBox(height: 6),
      Row(children: [
        Expanded(child: gradButton(app.tr('fix'), () => app.fixInternet(), ghost: true, icon: Icons.build_outlined)),
        const SizedBox(width: 8),
        Expanded(child: gradButton(app.tr('report'), () => app.reportServer(), ghost: true, icon: Icons.outlined_flag)),
      ]),
      const SizedBox(height: 14),
      ...app.chat.map(_bubble),
      const SizedBox(height: 8),
      Row(children: [
        Expanded(child: TextField(
          controller: _c,
          style: const TextStyle(color: K.txt),
          onSubmitted: (v) { app.sendMessage(v); _c.clear(); },
          decoration: fieldDeco(app.tr('support.ph')),
        )),
        const SizedBox(width: 8),
        Tooltip(message: app.tr('support.send'), child: Semantics(button: true, label: app.tr('support.send'),
          child: InkWell(
            borderRadius: BorderRadius.circular(13),
            onTap: () { app.sendMessage(_c.text); _c.clear(); },
            child: Container(width: 48, height: 48,
              decoration: BoxDecoration(gradient: K.grad, borderRadius: BorderRadius.circular(13)),
              child: const Icon(Icons.send, size: 20, color: Color(0xFF04201A))),
          ))),
      ]),
    ]);
  }

  Widget _bubble(SupportMessage m) {
    if (m.from == 'system') {
      return Container(
        margin: const EdgeInsets.symmetric(vertical: 5),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
        decoration: BoxDecoration(color: K.mint.withOpacity(0.08), borderRadius: BorderRadius.circular(14), border: Border.all(color: K.mint.withOpacity(0.2))),
        child: Text(m.text, textAlign: TextAlign.center, style: mono(size: 11.5, color: K.txt2)),
      );
    }
    final me = m.from == 'user';
    return Align(
      alignment: me ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 5),
        padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 12),
        constraints: const BoxConstraints(maxWidth: 320),
        decoration: BoxDecoration(
          gradient: me ? K.grad : null, color: me ? null : K.surface2,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(16), topRight: const Radius.circular(16),
            bottomLeft: Radius.circular(me ? 16 : 5), bottomRight: Radius.circular(me ? 5 : 16)),
          border: me ? null : Border.all(color: K.line)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(me ? app.tr('you') : app.tr('support.who'), style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600, color: me ? const Color(0x9904201A) : K.muted)),
          const SizedBox(height: 3),
          Text(m.text, style: TextStyle(fontSize: 13.5, height: 1.4, color: me ? const Color(0xFF04201A) : K.txt)),
        ]),
      ),
    );
  }
}
