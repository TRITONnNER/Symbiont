/* Симбионт — локальные иконки приложений / сервисов / сайтов (без CDN).

   Проблема: раньше иконки тянулись с cdn.simpleicons.org (внешний CDN — нарушает
   «ноль CDN», не работает офлайн и на Windows). Решение — генерируем ЛОКАЛЬНО.

   window.SYM_ICON_SVG(key, colorOverride?, labelOverride?) → data:URI (SVG):
   аккуратная плитка с 1–2 буквами на ФИРМЕННОМ цвете. Для известных брендов цвет/имя
   берутся из карты BRAND; для сайтов/приложений без бренда — инициалы + цвет из хеша.
   Работает офлайн и на Windows.

   Апгрейд до «настоящих» лого: Simple Icons — MIT; можно положить нужные .svg в
   assets/icons/ и вернуть их из BRAND (путь/данные) вместо буквенной плитки. */
(function () {
  function uri(svg) { return 'data:image/svg+xml,' + encodeURIComponent(svg); }
  function hashHue(s) { var h = 0; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h % 360; }
  // slug (как у simpleicons) → [читаемое имя, фирменный цвет]
  var BRAND = {
    telegram: ['Telegram', '#229ED9'], discord: ['Discord', '#5865F2'], docker: ['Docker', '#2496ED'],
    epicgames: ['Epic', '#2A2A2A'], facebook: ['Facebook', '#0866FF'], firefoxbrowser: ['Firefox', '#FF7139'],
    github: ['GitHub', '#24292E'], googlechrome: ['Chrome', '#4285F4'], instagram: ['Instagram', '#E4405F'],
    netflix: ['Netflix', '#E50914'], obsstudio: ['OBS', '#302E31'], paypal: ['PayPal', '#003087'],
    reddit: ['Reddit', '#FF4500'], riotgames: ['Riot', '#D32936'], spotify: ['Spotify', '#1DB954'],
    steam: ['Steam', '#3A6E8F'], tiktok: ['TikTok', '#EE1D52'], twitch: ['Twitch', '#9146FF'],
    valorant: ['Valorant', '#FF4655'], vlcmediaplayer: ['VLC', '#FF8800'], whatsapp: ['WhatsApp', '#25D366'],
    wikipedia: ['Wikipedia', '#636466'], wise: ['Wise', '#2A5B3A'], x: ['X', '#111111'],
    youtube: ['YouTube', '#FF0000'], codecrafters: ['CodeCrafters', '#171920']
  };
  function initials(label) {
    label = String(label || '?').trim().replace(/\.(exe|app|com|org|net|io|ru)$/i, '');
    var p = label.split(/[\s._\-\/]+/).filter(Boolean);
    if (p.length >= 2) return (p[0][0] + p[1][0]).toUpperCase();
    return label.slice(0, 2).toUpperCase();
  }
  window.SYM_ICON_SVG = function (key, colorOverride, labelOverride) {
    var b = BRAND[String(key || '').toLowerCase()];
    var label = labelOverride || (b ? b[0] : key) || '?';
    var color = colorOverride || (b ? b[1] : 'hsl(' + hashHue(String(key || label)) + ' 52% 46%)');
    var t = initials(label), fs = t.length > 1 ? 11 : 14;
    return uri('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28"><rect width="28" height="28" rx="7" fill="' + color + '"/><text x="14" y="18.6" font-family="sans-serif" font-size="' + fs + '" font-weight="700" fill="#fff" text-anchor="middle">' + t + '</text></svg>');
  };
})();
