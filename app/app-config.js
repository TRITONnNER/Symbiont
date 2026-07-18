/* Симбионт — приложение: конфигурация данных (значения по умолчанию).
   Все числа и списки приложения вынесены сюда (window.SYM_CONFIG) и служат
   ФОЛБЭКОМ, когда сервер недоступен. app-api.js заменяет их живыми ответами
   и рассылает событие 'sym-config' — приложение перерисовывается.

   Это аналог site-config.js из витрины, но с данными приложения
   (узлы, кошелёк/баланс, история, рефералы, устройства, ваучеры, колесо).

   Заменить ответом сервера:
     SYM_API.base='https://ваш-домен'; SYM_API.bootstrap();
   или точечно:
     SYM_CONFIG.merge({ economy: <ответ /v1/config/economy> }); */
(function () {
  var SYM_CONFIG = {

    /* ── GET /v1/config/economy ── тарифы, цены, продукты ──────────────── */
    economy: {
      version: 1,
      crypto_discount: 0.10,                 // −10 % при оплате криптовалютой
      currency_by_region: { ru: 'rub', intl: 'usd' },
      methods: {
        rub: ['sbp', 'mir', 'yoomoney', 'visa', 'mc', 'crypto'],
        usd: ['visa', 'mc', 'crypto']
      },
      // devices: 0 = без лимита; locations: -1 = все.
      // КАНОН тарифов (как в бэкенде и на сайте): free / premium / ultimate.
      tiers: {
        free:     { devices: 2, relay: 'limited', speed: 'basic', locations: 2,  dedicated_ip: false, gaming: false },
        premium:  { devices: 0, relay: 'full',    speed: 'full',  locations: -1, dedicated_ip: false, gaming: true  },
        ultimate: { devices: 0, relay: 'full',    speed: 'max',   locations: -1, dedicated_ip: true,  gaming: true  }
      },
      // ключи tier: premium / ultimate ; период: month / year
      prices: {
        premium:  { rub: { month: 199, year: 1490 }, usd: { month: 2.99, year: 24.99 } },
        ultimate: { rub: { month: 349, year: 2790 }, usd: { month: 4.99, year: 39.99 } }
      },
      // пакеты активного времени (минуты), тир premium
      balance: {
        premium: {
          rub: { '20h': 39, '100h': 149, '300h': 399 },
          usd: { '20h': 0.49, '100h': 1.99, '300h': 4.99 }
        }
      },
      products: {
        premium_month:  { tier: 'premium',  grant_days: 30 },
        premium_year:   { tier: 'premium',  grant_days: 365 },
        ultimate_month: { tier: 'ultimate', grant_days: 30 },
        ultimate_year:  { tier: 'ultimate', grant_days: 365 },
        balance_20h:    { tier: 'premium',  grant_minutes: 1200, once: true },
        balance_100h:   { tier: 'premium',  grant_minutes: 6000 },
        balance_300h:   { tier: 'premium',  grant_minutes: 18000 }
      }
    },

    /* ── GET /v1/config/flags ── фиче-флаги (что показывать / поведение) ─ */
    flags: {
      show_wheel: true,
      show_referral: true,
      show_vouchers: true,
      show_key_check: true,
      crypto_discount_enabled: true,
      live_payment_provider: false,          // false = sandbox (см. PRICING.md)
      // значения переключателей защиты/системы по умолчанию
      default_settings: {
        killswitch: true, adblock: true, trackers: true, phishing: false,
        dns: true, autostart: false, tray: true, reduceMotion: false
      }
    },

    /* ── GET /v1/config/discounts ── скидки и бонус-механики ───────────── */
    discounts: {
      crypto: 0.10,
      promo_codes: [],                       // сервер: [{ code, percent, until }]
      combo: [
        { months: 3,  bonus_days: 7 },
        { months: 6,  bonus_days: 20 },
        { months: 12, bonus_days: 45 }
      ],
      referral: {
        signup:        { inviter: 3,   invitee: 3 },
        premium_month: { inviter: 30,  invitee: 10 },
        premium_year:  { inviter: 90,  invitee: 30 },
        ultimate:      { inviter: 120, invitee: 40 }
      }
    },

    /* ── GET /v1/config/parse-bridge ── конфиг «мост/парсер» ───────────── */
    parseBridge: {
      enabled: true,
      // только то, что умеет каскад движка (allow-list). НЕ vmess/trojan/wireguard.
      formats: ['vless (reality)', 'shadowsocks-2022', 'hysteria2'],
      examples: ['vless://…@bridge:443?type=reality', 'ss://…', 'hysteria2://…'],
      updated_at: null
    },

    /* ── Список узлов (серверов) ──────────────────────────────────────────
       Нейтральные поля; названия страны/города берутся из локализации
       (SYM_I18N geo) либо из полей country/city, если сервер их пришлёт. */
    // БЕЗ заготовок: реальных серверов пока нет. Узлы приходят из /v1/manifest
    // (адаптер app-api.js). Пустой список = честное состояние «серверов ещё нет».
    nodes: [],

    /* ── Контакты/бренд (владелец заполняет реальными) ── */
    contacts: {
      email: 'support@example.com',          // TODO: реальный e-mail поддержки
      telegram: '',                          // TODO: https://t.me/…
      site: '',                              // TODO: домен
      legal_entity: '',                      // TODO: юрлицо для копирайта
      socials: []                            // TODO: ссылки
    },

    /* ── GET /v1/account/status → кошелёк, план, токен ────────────────── */
    account: {
      token: 'sb_live_7K2Q9xR4mT8vB1nW6pL3',  // демо; при живом входе — реальный токен
      plan: 'ultimate',                        // free | premium | ultimate
      expiry_at: null,                         // при живом входе берётся из billing/status
      invite: 'SYM-7K2Q'                       // реальный формат: SYM-XXXXXX
    },

    /* ── GET /v1/billing/ledger → баланс активного времени + история ──── */
    ledger: {
      balance: { hours: 412, minutes: 30 },
      // kind: keyact|wheel|debit|ref_earn|purchase|grant|ref_payout ; unit: d|h|m|rub
      entries: [
        { kind: 'keyact',     val: '+30',  unit: 'd',   date: '14.04.2026' },
        { kind: 'wheel',      val: '+2',   unit: 'h',   date: '14.04.2026' },
        { kind: 'debit',      val: '−80',  unit: 'm',   date: '13.04.2026' },
        { kind: 'ref_earn',   val: '+7',   unit: 'd',   date: '12.04.2026' },
        { kind: 'purchase',   val: '+30',  unit: 'd',   date: '14.03.2026' },
        { kind: 'grant',      val: '+3',   unit: 'd',   date: '10.03.2026' },
        { kind: 'ref_payout', val: '−120', unit: 'rub', date: '05.03.2026' },
        { kind: 'debit',      val: '−250', unit: 'm',   date: '04.03.2026' }
      ]
    },

    /* ── Карта трафика (живые соединения) ─────────────────────────────── */
    traffic: [
      { host: 'youtube.com',         path: 'proxy',  down: '4.2', up: '0.3' },
      { host: 'github.com',          path: 'direct', down: '1.1', up: '0.2' },
      { host: 'telegram.org',        path: 'proxy',  down: '0.8', up: '0.4' },
      { host: 'ads.doubleclick.net', path: 'reject', down: '—',   up: '—' },
      { host: 'steampowered.com',    path: 'direct', down: '8.6', up: '0.1' },
      { host: 'instagram.com',       path: 'proxy',  down: '2.4', up: '0.6' },
      { host: 'tracker.metrika.net', path: 'reject', down: '—',   up: '—' },
      { host: 'wikipedia.org',       path: 'direct', down: '0.5', up: '0.1' }
    ],

    /* ── GET /v1/account/referrals → реферальная статистика ───────────── */
    referral: {
      code: 'SYM-7K2Q',                      // реальный формат: SYM-XXXXXX
      level: 1,                              // 0..3 (new→verified→established→ambassador)
      invited: 12,
      converted: 5,
      earned_days: 180                       // награда ТОЛЬКО временем (дни), не рублями
    },

    /* ── GET /v1/account/devices → устройства аккаунта ────────────────── */
    devices: [
      { name: 'Рабочий ПК',    icon: 'desktop_windows', last: 'сейчас',    owner: true,  current: true },
      { name: 'iPhone 15 Pro', icon: 'smartphone',      last: '2 ч назад', owner: false, current: false },
      { name: 'MacBook Air',   icon: 'laptop_mac',      last: 'вчера',     owner: false, current: false }
    ],

    /* ── Ваучеры (админ-консоль) ──────────────────────────────────────── */
    vouchers: [
      { label: 'promo-июнь',  tariff: 'Премиум',  total: 100, used: 73,  date: '01.06.2026', color: '#34E5B0' },
      { label: 'partners-q2', tariff: 'Максимум', total: 50,  used: 12,  date: '15.05.2026', color: '#A78BFA' },
      { label: 'trial-bulk',  tariff: 'Пробный',  total: 500, used: 341, date: '20.04.2026', color: '#5B9BFF' }
    ],

    /* ── Колесо удачи (provably-fair) ─────────────────────────────────────
       Призы — только время (минуты/дни), без вывода в деньги. */
    wheel: {
      daily_free: 1,
      prizes: [
        { amount: 15,   unit: 'm' },
        { amount: 30,   unit: 'm' },
        { amount: 60,   unit: 'm' },
        { amount: 120,  unit: 'm' },
        { amount: 240,  unit: 'm' },
        { amount: 480,  unit: 'm' },
        { amount: 1,    unit: 'd' },
        { amount: 7,    unit: 'd', special: true }
      ]
    }
  };

  /* Мягкое (глубокое) слияние: сервер переопределяет только присланные поля. */
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
