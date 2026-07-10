// lib/screens/onboarding_screen.dart
// Первый экран: вход/регистрация. Аккаунт «придумай что угодно» — только метка,
// без личных данных. Опционально: адрес сервера и вход по существующему токену.
// Полностью адаптивен: центрированная карточка ≤ 460px на любом экране.
import 'package:flutter/material.dart';
import '../main.dart';
import '../theme.dart';
import '../style_engine.dart';
import '../responsive.dart';

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});
  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final _label = TextEditingController();
  final _server = TextEditingController(text: kDefaultBaseUrl);
  final _token = TextEditingController();
  final _recovery = TextEditingController();
  bool _advanced = false;
  bool _loginMode = false;
  bool _recoveryMode = false;   // в режиме входа: false=токен, true=recovery-код

  @override
  void dispose() {
    _label.dispose(); _server.dispose(); _token.dispose(); _recovery.dispose();
    super.dispose();
  }

  void _toast(String m) => ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(content: Text(m), backgroundColor: K.surface3, behavior: SnackBarBehavior.floating));

  /// Небольшая сегментная кнопка выбора способа входа (токен / recovery-код).
  Widget _segBtn(String label, bool active, VoidCallback onTap) => Semantics(
    button: true, selected: active, label: label,
    child: InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(10),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 9),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: active ? K.surface3 : Colors.transparent,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: active ? K.mint : K.line2)),
        child: Text(label, textAlign: TextAlign.center,
          style: TextStyle(fontSize: 12.5, color: active ? K.txt : K.muted,
            fontWeight: active ? FontWeight.w600 : FontWeight.w400)),
      ),
    ),
  );

  Future<void> _submit() async {
    final base = _advanced ? _server.text : null;
    final bool ok;
    if (!_loginMode) {
      ok = await app.register(_label.text, baseUrl: base);
    } else if (_recoveryMode) {
      ok = await app.loginWithRecovery(_recovery.text, baseUrl: base);
    } else {
      ok = await app.loginWithToken(_token.text, baseUrl: base);
    }
    if (!ok && mounted) _toast(app.lastError ?? app.tr('onb.error'));
  }

  @override
  Widget build(BuildContext context) {
    final r = Responsive.of(context);
    return Scaffold(
      backgroundColor: Style.spec.bg,
      body: Stack(children: [
        const _AuroraBackdrop(),
        SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: EdgeInsets.symmetric(horizontal: r.pad, vertical: 28),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 460),
                child: AnimatedBuilder(
                  animation: app,
                  builder: (context, _) => _card(r),
                ),
              ),
            ),
          ),
        ),
      ]),
    );
  }

  Widget _card(Responsive r) {
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
      // лого + имя
      Row(mainAxisAlignment: MainAxisAlignment.center, children: [
        Container(width: 44, height: 44,
          decoration: BoxDecoration(gradient: K.grad, borderRadius: BorderRadius.circular(13)),
          child: const Icon(Icons.all_inclusive, size: 26, color: Color(0xFF04201A))),
        const SizedBox(width: 12),
        const Text('Симбионт', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800, letterSpacing: 0.5)),
      ]),
      const SizedBox(height: 10),
      Text(_loginMode ? app.tr('onb.login.sub') : app.tr('onb.sub'),
        textAlign: TextAlign.center, style: const TextStyle(fontSize: 14, color: K.txt2, height: 1.5)),
      const SizedBox(height: 26),

      Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: K.surface, borderRadius: BorderRadius.circular(22), border: Border.all(color: K.line2)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (!_loginMode) ...[
            sectionLabel(app.tr('onb.label')),
            TextField(controller: _label, style: const TextStyle(color: K.txt),
              textInputAction: TextInputAction.done, onSubmitted: (_) => _submit(),
              decoration: fieldDeco(app.tr('onb.label.hint'))),
            const SizedBox(height: 6),
            Text(app.tr('onb.label.note'), style: const TextStyle(fontSize: 11.5, color: K.muted)),
          ] else ...[
            // выбор способа входа: по токену или по recovery-коду
            Row(children: [
              Expanded(child: _segBtn(app.tr('onb.byToken'), !_recoveryMode,
                  () => setState(() => _recoveryMode = false))),
              const SizedBox(width: 8),
              Expanded(child: _segBtn(app.tr('onb.byRecovery'), _recoveryMode,
                  () => setState(() => _recoveryMode = true))),
            ]),
            const SizedBox(height: 12),
            if (!_recoveryMode) ...[
              sectionLabel(app.tr('onb.token')),
              TextField(controller: _token, style: mono(size: 13, color: K.txt),
                textInputAction: TextInputAction.done, onSubmitted: (_) => _submit(),
                decoration: fieldDeco('symb-xxxxxxxx')),
            ] else ...[
              sectionLabel(app.tr('onb.recovery')),
              TextField(controller: _recovery, style: mono(size: 13, color: K.txt),
                textInputAction: TextInputAction.done, onSubmitted: (_) => _submit(),
                decoration: fieldDeco(app.tr('onb.recovery.hint'))),
            ],
          ],

          // расширенно: адрес сервера
          const SizedBox(height: 14),
          Semantics(button: true, expanded: _advanced, label: app.tr('onb.advanced'), child: InkWell(
            onTap: () => setState(() => _advanced = !_advanced),
            child: Padding(padding: const EdgeInsets.symmetric(vertical: 6), child: Row(children: [
              Icon(_advanced ? Icons.expand_less : Icons.expand_more, size: 18, color: K.muted),
              const SizedBox(width: 6),
              Text(app.tr('onb.advanced'), style: const TextStyle(fontSize: 12.5, color: K.muted)),
            ])),
          )),
          if (_advanced) ...[
            const SizedBox(height: 10),
            sectionLabel(app.tr('onb.server')),
            TextField(controller: _server, style: mono(size: 12.5, color: K.txt),
              decoration: fieldDeco('http://127.0.0.1:8000')),
            const SizedBox(height: 6),
            Text(app.tr('onb.server.note'), style: const TextStyle(fontSize: 11, color: K.muted)),
          ],

          const SizedBox(height: 18),
          app.busy
            ? const Padding(padding: EdgeInsets.symmetric(vertical: 6),
                child: Center(child: SizedBox(width: 26, height: 26,
                  child: CircularProgressIndicator(strokeWidth: 2.6, valueColor: AlwaysStoppedAnimation(K.mint)))))
            : gradButton(_loginMode ? app.tr('onb.login.btn') : app.tr('onb.btn'), _submit,
                icon: _loginMode ? Icons.login : Icons.arrow_forward),
        ]),
      ),

      const SizedBox(height: 16),
      Center(child: Semantics(button: true, child: InkWell(
        onTap: () => setState(() => _loginMode = !_loginMode),
        child: Padding(padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 8),
          child: Text(_loginMode ? app.tr('onb.toReg') : app.tr('onb.toLogin'),
            style: const TextStyle(fontSize: 12.5, color: K.aqua, decoration: TextDecoration.underline))),
      ))),
      const SizedBox(height: 18),
      Text(app.tr('onb.privacy'), textAlign: TextAlign.center,
        style: const TextStyle(fontSize: 11, color: K.muted, height: 1.5)),
    ]);
  }
}

class _AuroraBackdrop extends StatelessWidget {
  const _AuroraBackdrop();
  @override
  Widget build(BuildContext context) => Positioned.fill(
    child: DecoratedBox(
      decoration: BoxDecoration(gradient: RadialGradient(
        center: const Alignment(0, -0.5), radius: 1.1,
        colors: [K.aqua.withOpacity(0.12), K.ink], stops: const [0, 0.7],
      )),
    ),
  );
}
