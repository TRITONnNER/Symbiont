// lib/net/curtain.dart
// Классификатор «16-килобайтного занавеса» ТСПУ (см. blueprint): соединение к
// «подозрительному» IP (иностранный датацентр) отдаёт первые ~16 КБ и ЗАМИРАЕТ
// без RST — просто перестаёт слать данные. Это не блок и не медленный канал, а
// прицельный троттлинг по объёму. Детектируем по тому, сколько байт пришло и
// зависло ли соединение, и уводим трафик на relay через «белый» IP.

enum CurtainVerdict {
  clear,    // получили всё — занавеса нет
  curtain,  // ~16 КБ и замерло — фирменный «занавес»
  blocked,  // умерло раньше ~12 КБ — другой блок (RST/handshake)
  slow,     // >24 КБ, но тормозит — обычный троттлинг, не занавес
  partial,  // закрылось чисто, но меньше цели — не занавес (короткий ответ сервера)
}

/// Порог занавеса: наблюдается заморозка в районе 15–20 КБ. Берём окно 12–24 КБ.
const int kCurtainLo = 12 * 1024;
const int kCurtainHi = 24 * 1024;

/// Чистое решение (без IO — тестируемо). [stalled] = соединение перестало слать
/// данные и вышло по таймауту тишины (а не закрылось штатно).
CurtainVerdict classifyCurtain({
  required int receivedBytes,
  required int targetBytes,
  required bool stalled,
}) {
  if (receivedBytes >= targetBytes) return CurtainVerdict.clear;
  if (!stalled) return CurtainVerdict.partial;          // закрылось само, но меньше — не занавес
  if (receivedBytes < kCurtainLo) return CurtainVerdict.blocked; // умерло до 12 КБ
  if (receivedBytes <= kCurtainHi) return CurtainVerdict.curtain; // ~16 КБ и замерло
  return CurtainVerdict.slow;                            // >24 КБ и тормозит
}

/// Нужно ли уводить трафик на relay (обход занавеса/CIDR-whitelist).
bool curtainNeedsRelay(CurtainVerdict v) =>
    v == CurtainVerdict.curtain || v == CurtainVerdict.slow;
