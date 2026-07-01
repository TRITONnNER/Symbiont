// lib/screens/widgets/connection_core.dart
// СИГНАТУРНЫЙ ЭЛЕМЕНТ «Симбионта»: не тумблер вкл/выкл, а живое ЯДРО СВЯЗИ.
// Показывает не только «вкл», но и КАКИМ путём через каскад установлена связь
// (прямой/обход/туннель/реле) — то, чего нет у обычных VPN, потому что у них нет
// каскада. Конечный автомат честный: во время подключения НИКОГДА не «зелёный».
// Сигнал дублируется цветом + иконкой + текстом (8% мужчин — дальтоники).
import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../../theme.dart';
import '../../style_engine.dart';
import '../../design/tokens.dart';

enum CoreState { idle, measuring, connecting, connected, blocking, error }

class ConnectionCore extends StatefulWidget {
  final double size;
  final CoreState state;
  final String label;        // главный статус: «Защищён» / «Не защищён» / «Подключение…»
  final String? pathLabel;   // активный путь каскада (только в connected): Прямой/Обход/Туннель/Реле
  final String? sub;         // мелкая строка под статусом (сервер/подсказка)
  final VoidCallback onTap;
  const ConnectionCore({
    super.key, required this.size, required this.state,
    required this.label, this.pathLabel, this.sub, required this.onTap,
  });

  @override
  State<ConnectionCore> createState() => _ConnectionCoreState();
}

class _ConnectionCoreState extends State<ConnectionCore> with TickerProviderStateMixin {
  late final AnimationController _spin;   // вращение дуги (connecting/measuring)
  late final AnimationController _breath; // «дыхание» (connected)
  double _press = 1.0;

  @override
  void initState() {
    super.initState();
    _spin = AnimationController(vsync: this, duration: const Duration(milliseconds: 1400));
    _breath = AnimationController(vsync: this, duration: Dur.breath);
    _syncAnim();
  }

  @override
  void didUpdateWidget(ConnectionCore old) {
    super.didUpdateWidget(old);
    if (old.state != widget.state) _syncAnim();
  }

  void _syncAnim() {
    final s = widget.state;
    final spinning = s == CoreState.connecting || s == CoreState.measuring;
    if (spinning) {
      if (!_spin.isAnimating) _spin.repeat();
    } else {
      _spin.stop();
    }
    if (s == CoreState.connected) {
      if (!_breath.isAnimating) _breath.repeat(reverse: true);
    } else {
      _breath.stop();
      _breath.value = 0;
    }
  }

  @override
  void dispose() {
    _spin.dispose();
    _breath.dispose();
    super.dispose();
  }

  // Цвет/иконка по состоянию — избыточная сигнализация (не только цвет).
  Color _color(StyleSpec sp) {
    switch (widget.state) {
      case CoreState.idle: return K.muted;
      case CoreState.measuring: return K.aqua;
      case CoreState.connecting: return sp.accent;
      case CoreState.connected: return sp.accent;
      case CoreState.blocking: return K.amber;
      case CoreState.error: return K.rose;
    }
  }

  IconData _icon() {
    switch (widget.state) {
      case CoreState.idle: return Icons.shield_outlined;
      case CoreState.measuring: return Icons.lan_outlined;
      case CoreState.connecting: return Icons.shield_moon_outlined;
      case CoreState.connected: return Icons.verified_user_rounded;
      case CoreState.blocking: return Icons.block;
      case CoreState.error: return Icons.error_outline_rounded;
    }
  }

  String get _semantic {
    switch (widget.state) {
      case CoreState.idle: return 'Не защищён. Нажмите, чтобы подключиться';
      case CoreState.measuring: return 'Измерение сети';
      case CoreState.connecting: return 'Подключение';
      case CoreState.connected:
        return 'Защищён${widget.pathLabel != null ? ', путь: ${widget.pathLabel}' : ''}. Нажмите, чтобы отключиться';
      case CoreState.blocking: return 'Трафик заблокирован';
      case CoreState.error: return 'Ошибка подключения. Нажмите, чтобы повторить';
    }
  }

  @override
  Widget build(BuildContext context) {
    final sp = Style.spec;
    final color = _color(sp);
    final spinning = widget.state == CoreState.connecting || widget.state == CoreState.measuring;
    final sz = widget.size;

    return Semantics(
      button: true,
      enabled: true,
      toggled: widget.state == CoreState.connected,
      label: _semantic,
      child: GestureDetector(
        onTap: widget.onTap,
        onTapDown: (_) => setState(() => _press = 0.97),
        onTapUp: (_) => setState(() => _press = 1.0),
        onTapCancel: () => setState(() => _press = 1.0),
        child: AnimatedScale(
          scale: _press,
          duration: Dur.micro,
          child: AnimatedBuilder(
            animation: Listenable.merge([_spin, _breath]),
            builder: (context, _) {
              final reduce = MediaQuery.maybeOf(context)?.disableAnimations ?? false;
              final breath = (widget.state == CoreState.connected && !reduce)
                  ? 1.0 + 0.018 * math.sin(_breath.value * math.pi)
                  : 1.0;
              return SizedBox(
                width: sz, height: sz,
                child: Stack(alignment: Alignment.center, children: [
                  // мягкое свечение под ядром (только активные состояния)
                  if (widget.state == CoreState.connected || widget.state == CoreState.connecting)
                    Container(
                      width: sz * 0.9, height: sz * 0.9,
                      decoration: BoxDecoration(shape: BoxShape.circle, boxShadow: [
                        BoxShadow(color: color.withOpacity(0.18), blurRadius: sz * 0.18, spreadRadius: sz * 0.02),
                      ]),
                    ),
                  // кольцо состояния
                  Transform.scale(
                    scale: breath,
                    child: CustomPaint(
                      size: Size(sz, sz),
                      painter: _RingPainter(
                        color: color,
                        track: K.line2,
                        spin: spinning ? _spin.value : null,
                        full: widget.state == CoreState.connected ||
                              widget.state == CoreState.blocking ||
                              widget.state == CoreState.error,
                      ),
                    ),
                  ),
                  // центр: иконка + статус + (путь каскада) + подпись
                  Column(mainAxisSize: MainAxisSize.min, children: [
                    Icon(_icon(), size: sz * 0.18, color: color),
                    SizedBox(height: Sp.sm),
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: Sp.lg),
                      child: Text(widget.label, textAlign: TextAlign.center, maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: sz * 0.092, height: 1.15,
                          fontWeight: FontWeight.w700,
                          color: widget.state == CoreState.idle ? K.txt2 : K.txt)),
                    ),
                    if (widget.pathLabel != null && widget.state == CoreState.connected) ...[
                      SizedBox(height: Sp.sm),
                      _pathChip(widget.pathLabel!, color),
                    ],
                    if (widget.sub != null) ...[
                      SizedBox(height: Sp.xs),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: Sp.lg),
                        child: Text(widget.sub!, textAlign: TextAlign.center, maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: mono(size: sz * 0.05, color: K.muted)),
                      ),
                    ],
                  ]),
                ]),
              );
            },
          ),
        ),
      ),
    );
  }

  Widget _pathChip(String text, Color color) => Container(
    padding: const EdgeInsets.symmetric(horizontal: Sp.md, vertical: Sp.xs),
    decoration: BoxDecoration(
      color: color.withOpacity(0.12),
      borderRadius: BorderRadius.circular(Rad.full),
      border: Border.all(color: color.withOpacity(0.35)),
    ),
    child: Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(Icons.alt_route_rounded, size: TT.iconSm, color: color),
      const SizedBox(width: Sp.xs),
      Text(text, style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: color, letterSpacing: 0.2)),
    ]),
  );
}

class _RingPainter extends CustomPainter {
  final Color color;       // цвет состояния
  final Color track;       // фон-дорожка
  final double? spin;      // 0..1 фаза вращения (connecting/measuring), либо null
  final bool full;         // сплошное кольцо (connected/blocking/error)
  _RingPainter({required this.color, required this.track, this.spin, required this.full});

  @override
  void paint(Canvas canvas, Size size) {
    if (size.width <= 2) return;
    final center = Offset(size.width / 2, size.height / 2);
    final radius = size.width / 2 - 3;
    final rect = Rect.fromCircle(center: center, radius: radius);
    // дорожка
    canvas.drawCircle(center, radius, Paint()
      ..style = PaintingStyle.stroke..strokeWidth = 3..color = track);
    final p = Paint()
      ..style = PaintingStyle.stroke..strokeWidth = 3.5..strokeCap = StrokeCap.round;
    if (spin != null) {
      // вращающаяся дуга 120° с градиентным хвостом
      final start = spin! * 2 * math.pi;
      p.shader = SweepGradient(
        colors: [color.withOpacity(0), color],
        startAngle: 0, endAngle: math.pi * 1.4,
        transform: GradientRotation(start),
      ).createShader(rect);
      canvas.drawArc(rect, start, math.pi * 1.2, false, p);
    } else if (full) {
      // сплошное кольцо состояния
      p.color = color;
      canvas.drawArc(rect, -math.pi / 2, 2 * math.pi, false, p);
    } else {
      // idle: тонкое тусклое кольцо (поверх дорожки — чуть заметный акцент сверху)
      p.color = color.withOpacity(0.5);
      canvas.drawArc(rect, -math.pi / 2, math.pi * 0.5, false, p);
    }
  }

  @override
  bool shouldRepaint(_RingPainter old) =>
      old.spin != spin || old.color != color || old.full != full;
}
