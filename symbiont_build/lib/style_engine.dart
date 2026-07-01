// lib/style_engine.dart — движок переключаемых стилей оформления.
// 4 стиля: A (минимализм), B (брутализм), C (киберпанк-неон), D (объединённый).
// Меняет форму (скругление), рамки, тени/свечение и шрифт — ГЛОБАЛЬНО через токены,
// которые читают центральные хелперы (cardBox/gradButton/mono). Цветовая мятная
// база общая; C/D добавляют фиолетово-синюю глубину фона.
//
// Принцип скелета: добавление стиля не трогает экраны — они уже читают токены.
import 'package:flutter/material.dart';

enum AppStyle { minimal, brutal, cyber, fusion } // A, B, C, D

class StyleSpec {
  final String id;        // 'A'|'B'|'C'|'D'
  final String name;      // человеческое имя
  final String hint;      // короткое пояснение
  final double radius;    // скругление углов (0 = острые)
  final double borderWidth;
  final bool monoEverywhere; // моноширинный шрифт для всего (B/D) или только акценты
  final bool glow;        // неоновое свечение (C/D)
  final Offset shadowOffset; // «пиксельная» тень-сдвиг (B/D)
  final Color bg;         // фон scaffold
  final Color surface;    // фон карточек
  final Color cardBorder; // цвет рамки карточек
  final Color accent;     // главный акцент (мята везде)
  final Color accent2;    // вторичный акцент
  final Color shadowColor;
  const StyleSpec({
    required this.id, required this.name, required this.hint,
    required this.radius, required this.borderWidth, required this.monoEverywhere,
    required this.glow, required this.shadowOffset, required this.bg,
    required this.surface, required this.cardBorder, required this.accent,
    required this.accent2, required this.shadowColor,
  });
}

const Map<AppStyle, StyleSpec> kStyles = {
  AppStyle.minimal: StyleSpec(
    id: 'A', name: 'Минимализм', hint: 'Чистый мятный, скруглённый — как сейчас',
    radius: 20, borderWidth: 1, monoEverywhere: false, glow: false,
    shadowOffset: Offset.zero, bg: Color(0xFF070A0F), surface: Color(0xFF11151D),
    cardBorder: Color(0x12FFFFFF), accent: Color(0xFF34E5B0), accent2: Color(0xFF22D3EE),
    shadowColor: Color(0x00000000),
  ),
  AppStyle.brutal: StyleSpec(
    id: 'B', name: 'Брутализм', hint: 'Острые углы, моно-шрифт, пиксельные тени',
    radius: 0, borderWidth: 2, monoEverywhere: true, glow: false,
    shadowOffset: Offset(4, 4), bg: Color(0xFF070A0F), surface: Color(0xFF11151D),
    cardBorder: Color(0xFF34E5B0), accent: Color(0xFF34E5B0), accent2: Color(0xFF22D3EE),
    shadowColor: Color(0xFF0F6E56),
  ),
  AppStyle.cyber: StyleSpec(
    id: 'C', name: 'Киберпанк', hint: 'Неоновое свечение, фиолет-синяя глубина',
    radius: 8, borderWidth: 1, monoEverywhere: false, glow: true,
    shadowOffset: Offset.zero, bg: Color(0xFF0A0712), surface: Color(0xFF160C28),
    cardBorder: Color(0xFF22D3EE), accent: Color(0xFF34E5B0), accent2: Color(0xFF22D3EE),
    shadowColor: Color(0x5922D3EE),
  ),
  AppStyle.fusion: StyleSpec(
    id: 'D', name: 'Симбиоз', hint: 'Брутализм + киберпанк + мята вместе',
    radius: 0, borderWidth: 2, monoEverywhere: true, glow: true,
    shadowOffset: Offset(4, 4), bg: Color(0xFF0A0712), surface: Color(0xFF160C28),
    cardBorder: Color(0xFF34E5B0), accent: Color(0xFF34E5B0), accent2: Color(0xFF22D3EE),
    shadowColor: Color(0x8C534AB7),
  ),
};

/// Глобальный держатель текущего стиля. Хелперы читают Style.spec.
class Style {
  static final ValueNotifier<AppStyle> current = ValueNotifier(AppStyle.minimal);
  static StyleSpec get spec => kStyles[current.value]!;

  static AppStyle fromId(String id) {
    switch (id) {
      case 'B': return AppStyle.brutal;
      case 'C': return AppStyle.cyber;
      case 'D': return AppStyle.fusion;
      case 'A':
      default: return AppStyle.minimal;
    }
  }

  static void set(AppStyle s) { current.value = s; }
}
