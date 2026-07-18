/* Симбионт — SVG-флаги стран.
   Нужны для Windows, где эмодзи-флаги (regional indicators) НЕ рендерятся.
   window.SYM_FLAG_SVG('NL') → data:URI со SVG-флагом, либо null.
   При null используйте эмодзи-фолбэк (flagEmoji) — он работает на моб/mac/linux/web.
   Набор — распространённые локации VPN; простые флаги (полосы/крест/круг) нарисованы
   точно, сложные (US/GB/TR со звёздами/гербами) отдают null → фолбэк. Дополняйте по мере
   появления реальных узлов; полный набор — положить SVG в assets/flags/ и вернуть из этой карты. */
(function () {
  function uri(svg) { return 'data:image/svg+xml,' + encodeURIComponent(svg); }
  function bands(colors, vertical) {
    var n = colors.length, s = '';
    for (var i = 0; i < n; i++) {
      s += vertical
        ? '<rect x="' + (i * 3 / n) + '" width="' + (3 / n) + '" height="2" fill="' + colors[i] + '"/>'
        : '<rect y="' + (i * 2 / n) + '" width="3" height="' + (2 / n) + '" fill="' + colors[i] + '"/>';
    }
    return uri('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 2">' + s + '</svg>');
  }
  function nordic(bg, cross) {  // скандинавский крест
    return uri('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 11"><rect width="18" height="11" fill="' + bg + '"/><rect x="5" width="3" height="11" fill="' + cross + '"/><rect y="4" width="18" height="3" fill="' + cross + '"/></svg>');
  }
  var H = function (c) { return bands(c, false); }, V = function (c) { return bands(c, true); };
  var FLAGS = {
    NL: H(['#AE1C28', '#FFFFFF', '#21468B']),
    DE: H(['#000000', '#DD0000', '#FFCE00']),
    RU: H(['#FFFFFF', '#0039A6', '#D52B1E']),
    PL: H(['#FFFFFF', '#DC143C']),
    UA: H(['#0057B7', '#FFD700']),
    AT: H(['#ED2939', '#FFFFFF', '#ED2939']),
    ES: H(['#AA151B', '#F1BF00', '#AA151B']),
    NL2: null,
    FR: V(['#0055A4', '#FFFFFF', '#EF4135']),
    IT: V(['#009246', '#FFFFFF', '#CE2B37']),
    RO: V(['#002B7F', '#FCD116', '#CE1126']),
    BE: V(['#000000', '#FDDA24', '#EF3340']),
    IE: V(['#169B62', '#FFFFFF', '#FF883E']),
    FI: nordic('#FFFFFF', '#003580'),
    SE: nordic('#006AA7', '#FECC00'),
    NO: nordic('#EF2B2D', '#FFFFFF'),
    DK: nordic('#C60C30', '#FFFFFF'),
    JP: uri('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 2"><rect width="3" height="2" fill="#fff"/><circle cx="1.5" cy="1" r="0.6" fill="#BC002D"/></svg>'),
    CH: uri('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 2"><rect width="2" height="2" fill="#D52B1E"/><rect x="0.85" y="0.4" width="0.3" height="1.2" fill="#fff"/><rect x="0.4" y="0.85" width="1.2" height="0.3" fill="#fff"/></svg>')
  };
  // Всегда возвращает ЛОКАЛЬНЫЙ data:URI (никаких CDN): реальный флаг для известных
  // стран, иначе аккуратная плитка с ISO-кодом. Работает и на Windows, и офлайн.
  window.SYM_FLAG_SVG = function (cc) {
    cc = String(cc || '').toUpperCase();
    if (FLAGS[cc]) return FLAGS[cc];
    return uri('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 28"><rect width="40" height="28" rx="3" fill="#1b2733"/><text x="20" y="19" font-family="sans-serif" font-size="13" font-weight="700" fill="#8ea0b0" text-anchor="middle">' + (cc || '?') + '</text></svg>');
  };
})();
