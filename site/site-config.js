/* Симбионт — витрина: конфигурация данных (значения по умолчанию).
   Эти структуры ПОВТОРЯЮТ форму ответов бэкенда и служат фолбэком, когда
   сервер недоступен. site-api.js заменяет их живыми ответами и рассылает
   событие 'sym-config' — страницы слушают его и перерисовываются.

   Как заменить ответом сервера:
     1) поднимите бэкенд с эндпоинтами из site-api.js;
     2) SYM_API.base = 'https://ваш-домен';  SYM_API.bootstrap();
     3) либо вручную: SYM_CONFIG.merge({ economy: <ответ /v1/config/economy> }). */
(function () {
  var SYM_CONFIG = {

    /* ── GET /v1/config/economy ──────────────────────────────────────────
       Единый источник цифр (backend DEFAULT_ECONOMY / economy.json).
       Регион → валюта по языку: ru → rub, иначе → usd. */
    economy: {
      version: 1,
      crypto_discount: 0.10,                 // −10 % при оплате криптовалютой
      currency_by_region: { ru: 'rub', intl: 'usd' },
      methods: {
        rub: ['sbp', 'mir', 'visa', 'mc', 'yoomoney', 'crypto'],
        usd: ['visa', 'mc', 'crypto']
      },
      // devices: 0 = без лимита; locations: -1 = все
      tiers: {
        free:     { devices: 2, relay: 'limited', speed: 'basic', locations: 2,  dedicated_ip: false, gaming: false },
        premium:  { devices: 0, relay: 'full',    speed: 'full',  locations: -1, dedicated_ip: false, gaming: true  },
        ultimate: { devices: 0, relay: 'full',    speed: 'max',   locations: -1, dedicated_ip: true,  gaming: true  }
      },
      prices: {
        premium:  { rub: { month: 199, year: 1490 }, usd: { month: 2.99, year: 24.99 } },
        ultimate: { rub: { month: 349, year: 2790 }, usd: { month: 4.99, year: 39.99 } }
      },
      // пакеты активного времени (минуты), тир premium
      balance: {
        premium: {
          rub: { '100h': 149, '300h': 399 },
          usd: { '100h': 1.99, '300h': 4.99 }
        }
      },
      // каталог продуктов бэкенда (product → начисление)
      products: {
        premium_month:  { tier: 'premium',  grant_days: 30 },
        premium_year:   { tier: 'premium',  grant_days: 365 },
        ultimate_month: { tier: 'ultimate', grant_days: 30 },
        ultimate_year:  { tier: 'ultimate', grant_days: 365 },
        balance_100h:   { tier: 'premium',  grant_minutes: 6000 },
        balance_300h:   { tier: 'premium',  grant_minutes: 18000 }
      }
    },

    /* ── GET /v1/config/flags ── фиче-флаги (показ блоков / поведение) ──── */
    flags: {
      show_bonuses: true,
      show_balance_packages: true,
      show_key_check: true,
      crypto_discount_enabled: true,
      wheel_enabled: true,
      referrals_enabled: true,
      live_payment_provider: false           // false = sandbox (см. PRICING.md)
    },

    /* ── GET /v1/config/discounts ── скидки и бонус-механики ───────────── */
    discounts: {
      crypto: 0.10,                          // дублирует economy.crypto_discount
      promo_codes: [],                       // сервер: [{ code, percent, until }]
      combo: [                               // бонус за длительность подписки
        { months: 3,  bonus_days: 7 },
        { months: 6,  bonus_days: 20 },
        { months: 12, bonus_days: 45 }
      ],
      referral: {                            // реферальные начисления, дни
        signup:        { inviter: 3,   invitee: 3 },
        premium_month: { inviter: 30,  invitee: 10 },
        premium_year:  { inviter: 90,  invitee: 30 },
        ultimate:      { inviter: 120, invitee: 40 }
      }
    },

    /* ── Импорт «свой мост» (POST /v1/config/parse-bridge) ─────────────────
       Поддерживаемые протоколы = то, что умеет каскад движка (allow-list).
       Не Tor-бриджи (obfs4/snowflake), а узлы VLESS/SS/Hysteria2. */
    parseBridge: {
      enabled: true,
      formats: ['vless (reality)', 'shadowsocks-2022', 'hysteria2'],
      examples: ['vless://…@bridge:443?type=reality', 'ss://…', 'hysteria2://…'],
      updated_at: null
    },

    /* ── Каталог платформ витрины «Скачать» ──────────────────────────────
       Метаданные UI (иконки, экран превью, иконки кнопок) остаются в самой
       странице; отсюда берутся версия / размер / требования / ссылка. */
    platforms: {
      order: ['ios', 'android', 'windows', 'macos', 'linux', 'ext'],
      items: {
        ios:     { version: '0.1.0', size_mb: 24,  requirements: 'iOS 15+',                 url: '' },
        android: { version: '0.1.0', size_mb: 19,  requirements: 'Android 8+',              url: '' },
        windows: { version: '0.1.0', size_mb: 68,  requirements: 'Win 10 / 11 · x64',       url: '' },
        macos:   { version: '0.1.0', size_mb: 72,  requirements: 'macOS 12+',               url: '' },
        linux:   { version: '0.1.0', size_mb: 58,  requirements: 'glibc 2.31+ · x64/arm64', url: '' },
        ext:     { version: '2.1.0', size_mb: 2.4, requirements: 'Chrome · FF · Edge',      url: '' }
      }
    }
  };

  /* Мягкое (глубокое) слияние: сервер переопределяет только присланные поля,
     объекты сливаются рекурсивно, массивы/примитивы заменяются целиком. */
  SYM_CONFIG.merge = function (patch) {
    if (!patch || typeof patch !== 'object') return SYM_CONFIG;
    function deep(dst, src) {
      for (var k in src) {
        if (!Object.prototype.hasOwnProperty.call(src, k)) continue;
        var v = src[k];
        if (v && typeof v === 'object' && !Array.isArray(v) &&
            dst[k] && typeof dst[k] === 'object' && !Array.isArray(dst[k])) {
          deep(dst[k], v);
        } else {
          dst[k] = v;
        }
      }
      return dst;
    }
    for (var section in patch) {
      if (!Object.prototype.hasOwnProperty.call(patch, section)) continue;
      if (section === 'merge') continue;
      var p = patch[section];
      if (p && typeof p === 'object' && !Array.isArray(p) &&
          SYM_CONFIG[section] && typeof SYM_CONFIG[section] === 'object') {
        deep(SYM_CONFIG[section], p);
      } else {
        SYM_CONFIG[section] = p;
      }
    }
    return SYM_CONFIG;
  };

  window.SYM_CONFIG = SYM_CONFIG;
})();
