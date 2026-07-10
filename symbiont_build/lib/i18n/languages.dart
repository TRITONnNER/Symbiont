// lib/i18n/languages.dart — реестр языков интерфейса (полный набор).
// Один источник правды: что показывать в переключателе, какие RTL, что уже
// переведено (complete) vs пока на английском фоллбэке. Добавление языка =
// строка здесь + карта строк в strings.dart (ключ = code).
//
// Самоназвания (эндонимы) выверены; RTL отмечены (fa, ar, he, ur). Скрипты
// CJK/тайский/индийские/эфиопский рендерятся системными шрифтами-фоллбэками
// платформы — на узких платформах при необходимости добавим спец-шрифты.

class Lang {
  final String code;     // код-ключ в словаре (strings.dart)
  final String native;   // самоназвание (в меню показываем его)
  final String english;  // английское имя
  final bool rtl;        // письмо справа-налево
  final bool complete;   // полный перевод есть; иначе — фоллбэк на английский
  const Lang(this.code, this.native, this.english, {this.rtl = false, this.complete = false});
}

/// Полный список поддерживаемых языков. `complete: true` — перевод готов;
/// остальные показываются в меню (помечены β), а текст пока идёт по английскому
/// фоллбэку, пока не добавим их карту строк. Так «все языки» доступны структурно.
const List<Lang> kLanguages = [
  // — готовые —
  Lang('ru', 'Русский', 'Russian', complete: true),
  Lang('en', 'English', 'English', complete: true),

  // — славянская семья —
  Lang('uk', 'Українська', 'Ukrainian', complete: true),
  Lang('be', 'Беларуская', 'Belarusian', complete: true),
  Lang('pl', 'Polski', 'Polish', complete: true),
  Lang('cs', 'Čeština', 'Czech', complete: true),
  Lang('sk', 'Slovenčina', 'Slovak', complete: true),
  Lang('sl', 'Slovenščina', 'Slovenian', complete: true),
  Lang('hr', 'Hrvatski', 'Croatian', complete: true),
  Lang('sr', 'Српски', 'Serbian', complete: true),
  Lang('bg', 'Български', 'Bulgarian', complete: true),
  Lang('mk', 'Македонски', 'Macedonian', complete: true),
  Lang('isv', 'Medžuslovjansky', 'Interslavic', complete: true), // межславянский (машинная реконструкция, требует ревью носителей)

  // — западноевропейские —
  Lang('es', 'Español', 'Spanish', complete: true),
  Lang('de', 'Deutsch', 'German', complete: true),
  Lang('fr', 'Français', 'French', complete: true),
  Lang('it', 'Italiano', 'Italian', complete: true),
  Lang('pt', 'Português', 'Portuguese', complete: true),
  Lang('nl', 'Nederlands', 'Dutch', complete: true),

  // — северо- и юго-европейские —
  Lang('ro', 'Română', 'Romanian', complete: true),
  Lang('hu', 'Magyar', 'Hungarian', complete: true),
  Lang('el', 'Ελληνικά', 'Greek', complete: true),
  Lang('fi', 'Suomi', 'Finnish', complete: true),
  Lang('sv', 'Svenska', 'Swedish', complete: true),
  Lang('no', 'Norsk', 'Norwegian', complete: true),
  Lang('da', 'Dansk', 'Danish', complete: true),

  // — тюркские / Кавказ / Центральная Азия —
  Lang('tr', 'Türkçe', 'Turkish', complete: true),
  Lang('az', 'Azərbaycanca', 'Azerbaijani', complete: true),
  Lang('kk', 'Қазақша', 'Kazakh', complete: true),
  Lang('uz', 'Oʻzbekcha', 'Uzbek', complete: true),
  Lang('ky', 'Кыргызча', 'Kyrgyz', complete: true),
  Lang('tk', 'Türkmençe', 'Turkmen', complete: true),
  Lang('tg', 'Тоҷикӣ', 'Tajik', complete: true),
  Lang('mn', 'Монгол хэл', 'Mongolian', complete: true),
  Lang('hy', 'Հայերեն', 'Armenian', complete: true),
  Lang('ka', 'ქართული', 'Georgian', complete: true),

  // — Ближний Восток / RTL —
  Lang('fa', 'فارسی', 'Persian', rtl: true, complete: true),
  Lang('ar', 'العربية', 'Arabic', rtl: true, complete: true),
  Lang('he', 'עברית', 'Hebrew', rtl: true, complete: true),
  Lang('ur', 'اردو', 'Urdu', rtl: true, complete: true),

  // — Южная и Юго-Восточная Азия —
  Lang('hi', 'हिन्दी', 'Hindi', complete: true),
  Lang('bn', 'বাংলা', 'Bengali', complete: true),
  Lang('ta', 'தமிழ்', 'Tamil', complete: true),
  Lang('te', 'తెలుగు', 'Telugu', complete: true),
  Lang('mr', 'मराठी', 'Marathi', complete: true),
  Lang('id', 'Bahasa Indonesia', 'Indonesian', complete: true),
  Lang('ms', 'Bahasa Melayu', 'Malay', complete: true),
  Lang('vi', 'Tiếng Việt', 'Vietnamese', complete: true),
  Lang('th', 'ไทย', 'Thai', complete: true),

  // — Восточная Азия —
  Lang('zh', '中文', 'Chinese', complete: true),
  Lang('ja', '日本語', 'Japanese', complete: true),
  Lang('ko', '한국어', 'Korean', complete: true),

  // — Африка —
  Lang('sw', 'Kiswahili', 'Swahili', complete: true),
  Lang('ha', 'Hausa', 'Hausa', complete: true),
  Lang('am', 'አማርኛ', 'Amharic', complete: true),
];

Lang langByCode(String code) =>
    kLanguages.firstWhere((l) => l.code == code, orElse: () => kLanguages.first);

bool isRtlLang(String code) => langByCode(code).rtl;
