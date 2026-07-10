// lib/design/tokens.dart
// Доказательные дизайн-токены (из проектного исследования дизайн-системы).
// Это ЕДИНЫЙ источник констант композиции — экраны и компоненты читают отсюда,
// чтобы ритм не «гулял» магическими числами. Цвета/типографика — в theme.dart
// (там палитра K); здесь — нейтральные к цвету оси: spacing, радиус, motion.
import 'package:flutter/animation.dart';

/// Spacing на базе 8-pt grid (4-pt полушаг для тесных мест). Internal < external:
/// отступ ВНУТРИ группы меньше, чем МЕЖДУ группами (гештальт-близость).
class Sp {
  static const double xs = 4;    // внутри мелких компонентов (иконка+лейбл)
  static const double sm = 8;    // плотные паддинги
  static const double md = 12;   // паддинг карточек (тесный)
  static const double lg = 16;   // базовый паддинг
  static const double xl = 24;   // между секциями
  static const double xxl = 32;  // крупные разрывы
  static const double xxxl = 48; // зоны
  static const double huge = 64;
}

/// Радиусы (множители 4). full — для круглых контролов (герой, чипы, аватары).
class Rad {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double full = 999;
}

/// Длительности motion. Doherty <400мс = «мгновенно». >500мс ощущается сломанным.
class Dur {
  static const Duration micro = Duration(milliseconds: 120);  // таппы, тогглы
  static const Duration short = Duration(milliseconds: 200);  // стандартные переходы
  static const Duration medium = Duration(milliseconds: 300); // межблочные
  static const Duration long = Duration(milliseconds: 400);   // hero/межэкранные
  static const Duration breath = Duration(milliseconds: 2600);// «дыхание» активного ядра
}

/// Кривые: ease-out на вход (быстро→медленно), ease-in на выход, standard между
/// состояниями одного элемента. Линейная — только вращения/спиннеры.
class Ease {
  static const Cubic standard = Cubic(0.2, 0, 0, 1);
  static const Cubic decelerate = Cubic(0, 0, 0, 1);   // вход
  static const Cubic accelerate = Cubic(0.3, 0, 1, 1); // выход
}

/// Минимальные тач-таргеты (Apple 44 / Material 48). Держим 44 кросс-платформенно,
/// между таргетами ≥8 (расстояние важнее размера против мискликов).
class TT {
  static const double min = 44;
  static const double comfortable = 48;
  static const double iconSm = 16;
  static const double icon = 20;
  static const double iconLg = 24;
}
