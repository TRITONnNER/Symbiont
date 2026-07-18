/* site/selftest_connector.js — проверка связки site-api.js ↔ бэкенд (node).
   Мокает fetch ответами В ФОРМЕ РЕАЛЬНОГО бэкенда (server.py) и проверяет, что
   адаптеры приводят их к форме витрины (site-config.js), сохраняя верные
   статические значения (combo/referral). Запуск:  node site/selftest_connector.js */
const fs = require('fs'), path = require('path'), vm = require('vm');

// Фикстуры — форма ответов бэкенда (DEFAULT_ECONOMY / flags / discounts).
const BE = {
  economy: {
    tiers: [
      { id: 'free',     features: { relay: 'limited', speed: 'basic', locations: 2,             dedicated_ip: false, game_mode: false }, devices: 2 },
      { id: 'premium',  features: { relay: 'full',    speed: 'full',  locations: 'all',         dedicated_ip: false, game_mode: true  }, devices: 0 },
      { id: 'ultimate', features: { relay: 'full',    speed: 'max',   locations: 'all+premium', dedicated_ip: true,  game_mode: true  }, devices: 0 }
    ],
    prices: {
      ru:   { premium: { month: 199, year: 1490 }, ultimate: { month: 349, year: 2790 }, currency: 'RUB' },
      intl: { premium: { month: 2.99, year: 24.99 }, ultimate: { month: 4.99, year: 39.99 }, currency: 'USD' },
      balance: { ru: { '20h': 39, '100h': 149, '300h': 399 }, intl: { '20h': 0.49, '100h': 1.99, '300h': 4.99 } },
      crypto_discount: 0.10
    },
    payments: { ru: ['sbp', 'mir', 'visa', 'mastercard', 'yoomoney', 'crypto'], intl: ['visa', 'mastercard', 'crypto'] }
  },
  flags: {
    version: 1,
    download: { ios: true, android: true, windows: true, macos: true, linux: true, extension: true },
    coming_soon: { enabled: true }, site: { referrals: true },
    legal: { canary: false }, pricing: { balance: true, crypto_discount: true },
    app: { wheel: true, referrals: true, key_checker: true }
  },
  discounts: { campaigns: [{ percent: 20, scope: 'all', expires_at: null, label: 'Test -20' }] }
};

global.window = { dispatchEvent() {} };
global.CustomEvent = function (n, o) { this.detail = o && o.detail; };
global.fetch = function (url) {
  let body = '{}';
  if (url.includes('/config/economy')) body = JSON.stringify(BE.economy);
  else if (url.includes('/config/flags')) body = JSON.stringify(BE.flags);
  else if (url.includes('/config/discounts')) body = JSON.stringify(BE.discounts);
  else if (url.includes('/account/login')) body = JSON.stringify({ token: 'TESTTOKEN', account_id: 'a1' });
  return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(body) });
};

const dir = __dirname;
vm.runInThisContext(fs.readFileSync(path.join(dir, 'site-config.js'), 'utf8'));
vm.runInThisContext(fs.readFileSync(path.join(dir, 'site-api.js'), 'utf8'));

const A = global.window.SYM_API, C = global.window.SYM_CONFIG;
let ok = 0, fail = 0;
const chk = (c, n) => { if (c) { ok++; console.log('[OK]', n); } else { fail++; console.log('[FAIL]', n); } };

A.bootstrap().then(() => {
  const e = C.economy, f = C.flags, d = C.discounts;
  chk(e.prices.premium.rub.month === 199 && e.prices.premium.rub.year === 1490, 'economy: premium ₽ 199/1490 (форма витрины)');
  chk(e.prices.ultimate.usd.month === 4.99, 'economy: ultimate $4.99');
  chk(e.balance.premium.rub['100h'] === 149 && e.balance.premium.rub['300h'] === 399, 'economy: баланс 100/300 ч');
  chk(e.tiers.free.devices === 2 && e.tiers.premium.devices === 0 && e.tiers.ultimate.dedicated_ip === true, 'economy: тиры 2/∞ + dedicated_ip');
  chk(e.tiers.premium.locations === -1, 'economy: premium locations=all(-1)');
  chk(e.methods.rub.indexOf('mc') >= 0 && e.methods.rub.indexOf('sbp') >= 0, 'economy: методы ₽ (mastercard→mc)');
  chk(f.show_balance_packages === true && f.wheel_enabled === true && f.crypto_discount_enabled === true, 'flags: витрина-флаги смаплены');
  chk(f.download && f.download.macos === true, 'flags: сырые флаги платформ (download.macos)');
  chk(Array.isArray(d.campaigns) && d.campaigns.length >= 1 && d.campaigns[0].percent === 20, 'discounts: кампания −20% пришла');
  chk(d.combo && d.combo.length === 3, 'discounts: combo (site-config) сохранён');
  chk(d.referral.premium_month.inviter === 30, 'discounts: referral (site-config) сохранён');
  chk(A._ready === true, 'bootstrap: SYM_API._ready выставлен (сайт подключён)');
  // login-gate: после входа выставляется Bearer-токен
  return A.login({ token: 'x' }).then(function (res) {
    chk(res.token === 'TESTTOKEN', 'login: токен получен');
    chk(A.hasToken() && A.headers.Authorization === 'Bearer TESTTOKEN', 'login: Bearer-заголовок выставлен');
    console.log(`\n[site-connector] ${ok}/${ok + fail} зелёные`);
    process.exit(fail ? 1 : 0);
  });
}).catch(err => { console.error(err); process.exit(2); });
