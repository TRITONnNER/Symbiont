// lib/main.dart — точка входа «Симбионта».
// Поток: инициализируем хранилище → создаём AppState → показываем онбординг
// (вход/регистрация) или приложение в зависимости от Store.onboarded.
// Движок-туннель сейчас Mock (UI/бэкенд реальны); боевой Sing-box — нативный шаг.
import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'dart:async';
import 'engine/mock_engine.dart';
import 'engine/desktop_engine.dart'; // реальный sing-box процессом (десктоп), иначе null
import 'state/app_state.dart';
import 'log.dart';
import 'i18n/languages.dart';
export 'state/app_state.dart' show AppState, AppScreen, kDefaultBaseUrl, kTrustedPubKey; // реэкспорт для экранов
import 'app_shell.dart';
import 'screens/onboarding_screen.dart';
import 'theme.dart';
import 'style_engine.dart';
import 'store.dart';
import 'webshell/web_shell_page.dart';

/// Показывать дизайн-оболочку (webapp/) в WebView вместо старых Flutter-экранов.
/// Тот же UI на всех платформах, движок переиспользуется. Выключить:
///   flutter run --dart-define=SYMBIONT_WEBSHELL=false   (вернёт старые экраны)
const bool kUseWebShell =
    bool.fromEnvironment('SYMBIONT_WEBSHELL', defaultValue: true);

late AppState app;

Future<void> main() async {
  runZonedGuarded(() async {
    WidgetsFlutterBinding.ensureInitialized();
    // Перехват ВСЕХ ошибок фреймворка (overflow, исключения в build/layout/paint) →
    // в консоль flutter run с тегом ERROR:flutter. Это наши «глаза» при отладке.
    FlutterError.onError = (details) {
      Log.e('flutter', details.exceptionAsString(), details.exception, details.stack);
      FlutterError.presentError(details);
    };
    // Ошибки уровня платформы/движка вне дерева виджетов.
    WidgetsBinding.instance.platformDispatcher.onError = (error, stack) {
      Log.e('platform', 'необработанная ошибка', error, stack);
      return true;
    };
    Log.w('boot', '═══ старт приложения ═══');
    await Store.init();
    Log.w('boot', 'хранилище готово: onboarded=${Store.onboarded}, style=${Store.style}');
    Style.current.value = Style.fromId(Store.style); // восстановить выбранный стиль
    // Реальный десктоп-движок включается ТОЛЬКО если найден бинарник sing-box;
    // иначе Mock (поведение приложения не меняется: прямое подключение + реальный пинг).
    final native = createDesktopEngine();
    Log.w('boot', 'движок: ${native != null ? 'нативный sing-box' : 'Mock (прямое подключение + реальный пинг)'}');
    app = AppState(native ?? MockEngine(), nativeTunnel: native != null);
    Log.w('boot', 'базовый URL бэкенда: $kDefaultBaseUrl');
    Log.env(); // снимок окружения (ОС/локаль/сеть/IP) — для отладки
    Log.w('boot', 'запуск UI…');
    runApp(const SymbiontApp());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      try {
        final v = WidgetsBinding.instance.platformDispatcher.views.first;
        Log.w('env', 'окно: ${v.physicalSize.width.toInt()}x${v.physicalSize.height.toInt()} '
            'dpr=${v.devicePixelRatio.toStringAsFixed(2)}');
      } catch (_) {}
      Log.w('boot', 'первый кадр отрисован — UI жив');
    });
  }, (error, stack) {
    Log.e('zone', 'необработанное исключение (async)', error, stack);
  });
}

class SymbiontApp extends StatelessWidget {
  const SymbiontApp({super.key});
  @override
  Widget build(BuildContext context) {
    // Перестраиваем всё приложение при смене стиля оформления.
    return ValueListenableBuilder<AppStyle>(
      valueListenable: Style.current,
      builder: (context, _, __) => MaterialApp(
        title: 'Симбионт',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(),
        home: const _Root(),
        builder: (context, child) {
          // Направление письма по выбранному языку (арабский/фарси — RTL).
          final dir = isRtlLang(app.lang) ? TextDirection.rtl : TextDirection.ltr;
          return Directionality(textDirection: dir, child: child ?? const SizedBox.shrink());
        },
      ),
    );
  }
}

/// Переключает онбординг ↔ приложение по состоянию.
class _Root extends StatelessWidget {
  const _Root();
  @override
  Widget build(BuildContext context) {
    // Новый путь: дизайн-оболочка в WebView (переиспользует движок app.engine).
    // На web WebView не нужен (там оболочку открывают напрямую) — оставляем экраны.
    if (kUseWebShell && !kIsWeb) {
      return WebShellPage(
        engine: app.engine,
        backendBase: Store.baseUrl ?? kDefaultBaseUrl,
      );
    }
    return AnimatedBuilder(
      animation: app,
      builder: (context, _) => app.onboarded ? const AppShell() : const OnboardingScreen(),
    );
  }
}
