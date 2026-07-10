// lib/theme.dart — фирменная тёмная тема и переиспользуемые элементы.
// Шрифты — системные (без google_fonts): на Windows это Segoe UI + Consolas,
// на других платформах берётся ближайший sans/monospace. Это убирает тяжёлую
// зависимость google_fonts → objective_c, ломавшую сборку под Windows.
import 'package:flutter/material.dart';
import 'style_engine.dart';
import 'design/tokens.dart';

// Моноширинное семейство для цифр/метрик: бандленный JetBrains Mono (одинаков на
// всех платформах, tabular figures для выравнивания цифр). Фолбэки — на случай,
// если ассет не подхватился.
const String _kMonoFamily = 'JetBrains Mono';
const List<String> _kMonoFallback = ['Consolas', 'Menlo', 'DejaVu Sans Mono', 'monospace'];

class K {
  static const ink = Color(0xFF070A0F);
  static const ink2 = Color(0xFF090C12);
  static const surface = Color(0xFF11151D);
  static const surface2 = Color(0xFF161B25);
  static const surface3 = Color(0xFF1C2330);
  static const line = Color(0x12FFFFFF);
  static const line2 = Color(0x1FFFFFFF);
  static const txt = Color(0xFFEAF0F6);
  static const txt2 = Color(0xFF9AA7B8);
  static const muted = Color(0xFF6E7B8A); // поднят контраст: подписи читаемы (APCA Lc≈60)
  static const mint = Color(0xFF34E5B0);
  static const aqua = Color(0xFF22D3EE);
  static const blue = Color(0xFF5B9BFF);
  static const amber = Color(0xFFF4B740);
  static const rose = Color(0xFFFF6B7A);
  static const grad = LinearGradient(
    begin: Alignment.topLeft, end: Alignment.bottomRight,
    colors: [mint, aqua, blue],
  );
  static const gradSoft = LinearGradient(
    begin: Alignment.topLeft, end: Alignment.bottomRight,
    colors: [Color(0x2934E5B0), Color(0x1A22D3EE)],
  );
}

/// Типографическая шкала (Major Third 1.25 от базы 16). Веса и line-height —
/// из исследования: тело 1.5, заголовки 1.2–1.3, лейблы с трекингом. Единый
/// набор размеров → нет «случайных» кеглей. Цвет по умолчанию задаёт уровень.
class Tg {
  static const display = TextStyle(fontSize: 40, height: 1.15, fontWeight: FontWeight.w700, letterSpacing: -0.5, color: K.txt);
  static const h1     = TextStyle(fontSize: 32, height: 1.2,  fontWeight: FontWeight.w700, letterSpacing: -0.3, color: K.txt);
  static const h2     = TextStyle(fontSize: 25, height: 1.25, fontWeight: FontWeight.w700, letterSpacing: -0.2, color: K.txt);
  static const title  = TextStyle(fontSize: 20, height: 1.3,  fontWeight: FontWeight.w600, color: K.txt);
  static const bodyL  = TextStyle(fontSize: 16, height: 1.5,  fontWeight: FontWeight.w400, color: K.txt);
  static const body   = TextStyle(fontSize: 14, height: 1.5,  fontWeight: FontWeight.w400, color: K.txt2);
  static const label  = TextStyle(fontSize: 13, height: 1.35, fontWeight: FontWeight.w600, letterSpacing: 0.2, color: K.txt2);
  static const caption= TextStyle(fontSize: 12, height: 1.4,  fontWeight: FontWeight.w400, color: K.muted);
}

ThemeData buildTheme() {
  final base = ThemeData.dark(useMaterial3: true);
  final s = Style.spec;
  return base.copyWith(
    scaffoldBackgroundColor: s.bg,
    textTheme: base.textTheme.apply(
      bodyColor: K.txt, displayColor: K.txt,
    ),
    colorScheme: base.colorScheme.copyWith(primary: K.mint, surface: s.surface),
  );
}

TextStyle mono({double size = 13, Color color = K.txt2, FontWeight w = FontWeight.w500}) =>
    TextStyle(fontFamily: _kMonoFamily, fontFamilyFallback: _kMonoFallback,
      fontSize: size, color: color, fontWeight: w,
      fontFeatures: const [FontFeature.tabularFigures()]); // цифры одинаковой ширины

Color loadColor(int l) => l < 35 ? K.mint : (l < 60 ? K.amber : K.rose);

// ── переиспользуемые виджеты ──────────────────────────────────────────────────
Widget cardBox({required Widget child, EdgeInsets? padding, VoidCallback? onTap, Color? border}) {
  final s = Style.spec;
  final shadows = <BoxShadow>[];
  if (s.shadowOffset != Offset.zero) {
    shadows.add(BoxShadow(color: s.shadowColor, offset: s.shadowOffset, blurRadius: 0));
  }
  if (s.glow) {
    shadows.add(BoxShadow(color: s.accent2.withOpacity(0.12), blurRadius: 16, spreadRadius: 0));
  }
  final w = Container(
    padding: padding ?? const EdgeInsets.all(Sp.lg),
    decoration: BoxDecoration(
      color: s.surface,
      borderRadius: BorderRadius.circular(s.radius),
      border: Border.all(color: border ?? (s.borderWidth > 1 ? s.cardBorder : K.line), width: s.borderWidth),
      boxShadow: shadows.isEmpty ? null : shadows,
    ),
    child: child,
  );
  if (onTap == null) return w;
  return Semantics(button: true, child: InkWell(borderRadius: BorderRadius.circular(s.radius), onTap: onTap, child: w));
}

/// Доступная иконочная кнопка: тач-таргет ≥44, Tooltip (десктоп) + Semantics.
/// Использовать вместо «голых» InkWell+Icon во всём интерфейсе.
Widget iconButton(IconData icon, VoidCallback onTap, {required String label, double size = TT.icon, Color? color}) {
  final btn = InkWell(
    borderRadius: BorderRadius.circular(Rad.md),
    onTap: onTap,
    child: SizedBox(width: TT.min, height: TT.min,
      child: Icon(icon, size: size, color: color ?? K.txt2)),
  );
  return Tooltip(message: label,
    child: Semantics(button: true, label: label, child: btn));
}

Widget sectionLabel(String t) => Padding(
  padding: const EdgeInsets.fromLTRB(2, Sp.xl, 2, Sp.md),
  child: Semantics(header: true, child: Text(t.toUpperCase(),
    style: const TextStyle(fontSize: 11.5, letterSpacing: 1.3, color: K.muted, fontWeight: FontWeight.w700))),
);

Widget gradButton(String label, VoidCallback onTap, {IconData? icon, bool ghost = false}) {
  final s = Style.spec;
  final txtStyle = TextStyle(
    fontWeight: FontWeight.w700, fontSize: 15,
    fontFamily: s.monoEverywhere ? _kMonoFamily : null,
    fontFamilyFallback: s.monoEverywhere ? _kMonoFallback : null,
    color: ghost ? K.txt : const Color(0xFF04201A));
  final child = Row(mainAxisAlignment: MainAxisAlignment.center, mainAxisSize: MainAxisSize.min, children: [
    if (icon != null) Padding(padding: const EdgeInsets.only(right: 8), child: Icon(icon, size: 18, color: ghost ? K.txt : const Color(0xFF04201A))),
    Text(label, style: txtStyle),
  ]);
  final shadows = <BoxShadow>[];
  if (!ghost && s.shadowOffset != Offset.zero) {
    shadows.add(BoxShadow(color: s.shadowColor, offset: s.shadowOffset, blurRadius: 0));
  }
  if (!ghost && s.glow) {
    shadows.add(BoxShadow(color: s.accent.withOpacity(0.4), blurRadius: 16));
  }
  return Semantics(button: true, label: label, child: InkWell(
    borderRadius: BorderRadius.circular(s.radius),
    onTap: onTap,
    child: ConstrainedBox(
      constraints: const BoxConstraints(minHeight: TT.min),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(vertical: Sp.lg),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          gradient: ghost ? null : K.grad,
          color: ghost ? const Color(0x0DFFFFFF) : null,
          borderRadius: BorderRadius.circular(s.radius),
          border: ghost ? Border.all(color: K.line2, width: s.borderWidth) : (s.borderWidth > 1 ? Border.all(color: const Color(0xFF04342C), width: 2) : null),
          boxShadow: shadows.isEmpty ? null : shadows,
        ),
        child: child,
      ),
    ),
  ));
}

Widget titleText(String t) => Padding(
  padding: const EdgeInsets.fromLTRB(2, 2, 2, Sp.xs),
  child: Semantics(header: true, child: Text(t, style: Tg.h2)),
);

Widget subText(String t) => Padding(
  padding: const EdgeInsets.fromLTRB(2, 0, 2, Sp.lg),
  child: Text(t, style: Tg.body),
);

InputDecoration fieldDeco(String hint) => InputDecoration(
  hintText: hint,
  hintStyle: const TextStyle(color: K.muted),
  filled: true, fillColor: K.ink,
  contentPadding: const EdgeInsets.symmetric(horizontal: Sp.lg, vertical: Sp.lg),
  enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(Rad.md), borderSide: const BorderSide(color: K.line2)),
  focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(Rad.md), borderSide: const BorderSide(color: K.mint, width: 2)),
);
