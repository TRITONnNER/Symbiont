// lib/responsive.dart
// Единая система адаптивности. Считает размеры из ширины/высоты окна, поэтому
// корректно работает при РЕСАЙЗЕ окна и в полноэкранном режиме на лету.
//
// Классы устройств по ширине:
//   compact  < 600   — телефон (нижние вкладки, одна колонка)
//   medium   600–959 — планшет/узкое окно (нижние вкладки или узкий rail)
//   expanded ≥ 960   — десктоп (боковой rail)
// Дополнительно масштабируем контент на очень больших экранах (≥1600, 4K).
import 'package:flutter/widgets.dart';

enum DeviceClass { compact, medium, expanded }

class Responsive {
  final double w, h;
  final DeviceClass cls;
  final bool portrait;

  const Responsive._(this.w, this.h, this.cls, this.portrait);

  factory Responsive.of(BuildContext context) {
    final size = MediaQuery.of(context).size;
    final w = size.width, h = size.height;
    final cls = w < 600 ? DeviceClass.compact : (w < 960 ? DeviceClass.medium : DeviceClass.expanded);
    return Responsive._(w, h, cls, h >= w);
  }

  bool get isCompact => cls == DeviceClass.compact;
  bool get isMedium => cls == DeviceClass.medium;
  bool get isExpanded => cls == DeviceClass.expanded;

  /// Боковой rail только на широких экранах.
  bool get useRail => cls == DeviceClass.expanded;
  /// Нижние вкладки на узких.
  bool get useBottomTabs => !useRail;

  /// Ширина бокового rail (чуть уже на «среднем» десктопе).
  double get railWidth => w >= 1280 ? 248 : 224;

  /// Ширина для ЧИТАЕМЫХ экранов (формы, текст, герой) — узкая, длинные строки
  /// читаются хуже. Выровнена влево/по центру в зависимости от экрана.
  double get contentMaxWidth {
    if (w >= 2000) return 860;
    if (cls == DeviceClass.expanded) return 760;
    return 640;
  }

  /// Ширина для СПИСОЧНЫХ/плотных экранов. Контейнер, а НЕ во всю ширину: строки
  /// во весь экран выглядят растянутыми (мёртвая пустота посередине). Ограничиваем.
  double get contentMaxWidthWide {
    if (w >= 1500) return 980;
    if (w >= 1100) return 900;
    if (cls == DeviceClass.expanded) return 820;
    return w; // моб./узкое — на всю доступную ширину
  }

  /// Горизонтальные поля контента.
  double get pad {
    if (cls == DeviceClass.compact) return w < 380 ? 14 : 18;
    if (cls == DeviceClass.medium) return 24;
    return 28;
  }

  /// Высота верхней панели.
  double get topBarHeight => cls == DeviceClass.compact ? 58 : 66;

  /// Диаметр круга подключения — зависит и от ширины, и от высоты (ландшафт телефона!).
  double get dialSize {
    final byW = w * (cls == DeviceClass.compact ? 0.5 : 0.32);
    final byH = h * 0.32;
    return byW.clamp(150.0, 240.0).clamp(120.0, byH.clamp(120.0, 240.0));
  }

  /// Колонок в сетке узлов (на широких — 2, на 4K — 3).
  int get nodeColumns {
    if (w >= 2000) return 3;
    if (w >= 1100) return 2;
    return 1;
  }

  /// Колонок в сетке режимов охвата.
  int get modeColumns => w < 380 ? 1 : 2;

  /// Глобальный множитель для лёгкого укрупнения на 4K.
  double get scale => w >= 2400 ? 1.15 : 1.0;
}
