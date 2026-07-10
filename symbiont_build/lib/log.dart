// lib/log.dart — единый журнал событий. Условный экспорт: на native пишем в файл
// %APPDATA%\Symbiont\symbiont.log + держим в памяти; на web — только память.
export 'log_web.dart' if (dart.library.io) 'log_io.dart';
