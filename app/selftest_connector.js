/* app/selftest_connector.js — проверка связки app-api.js ↔ бэкенд (node).
   Мокает fetch ответами В ФОРМЕ РЕАЛЬНОГО бэкенда и проверяет адаптеры + аккаунт.
   Запуск:  node app/selftest_connector.js */
const fs = require('fs'), path = require('path'), vm = require('vm');
const now = Math.floor(Date.now() / 1000);
const BE = {
  economy: {
    tiers: [
      { id: 'free', features: { relay: 'limited', speed: 'basic', locations: 2, dedicated_ip: false, game_mode: false }, devices: 2 },
      { id: 'premium', features: { relay: 'full', speed: 'full', locations: 'all', dedicated_ip: false, game_mode: true }, devices: 0 },
      { id: 'ultimate', features: { relay: 'full', speed: 'max', locations: 'all+premium', dedicated_ip: true, game_mode: true }, devices: 0 }
    ],
    prices: { ru: { premium: { month: 199, year: 1490 }, ultimate: { month: 349, year: 2790 } }, intl: { premium: { month: 2.99, year: 24.99 }, ultimate: { month: 4.99, year: 39.99 } }, balance: { ru: { '100h': 149, '300h': 399 }, intl: { '100h': 1.99, '300h': 4.99 } }, crypto_discount: 0.10 },
    payments: { ru: ['sbp', 'mir', 'visa', 'mastercard', 'yoomoney', 'crypto'], intl: ['visa', 'mastercard', 'crypto'] }
  },
  flags: { download: { macos: true }, site: { referrals: true }, pricing: { crypto_discount: true }, app: { wheel: true, referrals: true, key_checker: true } },
  discounts: { campaigns: [{ percent: 20, label: 'X' }] },
  manifest: { nodes: [{ code: 'nl', loadPct: 34, roles: ['entry'] }, { code: 'relay1', roles: ['relay'] }] },
  billing: { subscription: { tier: 'premium', mode: 'period', days_left: 30, active_minutes_left: 0 } },
  ledger: { subscription: { active_minutes_left: 5990 }, entries: [{ at: now, kind: 'purchase', days: 30 }, { at: now, kind: 'debit', minutes: 80 }] },
  referral: { code: 'SYM-ABC', invited: 12, paying: 5, earned_days: 180 },
  login: { token: 'APPTOKEN', account_id: 'a1' }
};
global.window = { dispatchEvent() {} };
global.CustomEvent = function (n, o) { this.detail = o && o.detail; };
global.fetch = function (url, opts) {
  var body = '{}';
  if (url.includes('/config/economy')) body = JSON.stringify(BE.economy);
  else if (url.includes('/config/flags')) body = JSON.stringify(BE.flags);
  else if (url.includes('/config/discounts')) body = JSON.stringify(BE.discounts);
  else if (url.includes('/v1/manifest')) body = JSON.stringify(BE.manifest);
  else if (url.includes('/billing/status')) body = JSON.stringify(BE.billing);
  else if (url.includes('/billing/ledger')) body = JSON.stringify(BE.ledger);
  else if (url.includes('/v1/referral')) body = JSON.stringify(BE.referral);
  else if (url.includes('/account/login')) body = JSON.stringify(BE.login);
  return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(body) });
};
const dir = __dirname;
vm.runInThisContext(fs.readFileSync(path.join(dir, 'app-config.js'), 'utf8'));
vm.runInThisContext(fs.readFileSync(path.join(dir, 'app-api.js'), 'utf8'));
const A = global.window.SYM_API, C = global.window.SYM_CONFIG;
let ok = 0, fail = 0;
const chk = (c, n) => { if (c) { ok++; console.log('[OK]', n); } else { fail++; console.log('[FAIL]', n); } };

A.login({ token: 'x' }).then(function () {
  chk(A.hasToken() && A.headers.Authorization === 'Bearer APPTOKEN', 'login: Bearer выставлен');
  chk(C.account.token === 'APPTOKEN', 'login: токен положен в config.account');
  return A.bootstrap();
}).then(function () {
  const e = C.economy, f = C.flags, d = C.discounts, n = C.nodes, ac = C.account, lg = C.ledger, rf = C.referral;
  chk(e.prices.premium.rub.month === 199, 'economy: premium ₽199 (форма приложения)');
  chk(e.tiers.free.devices === 2 && e.tiers.premium.devices === 0 && e.tiers.ultimate.dedicated_ip === true, 'economy: тиры 2/∞ premium/ultimate');
  chk(f.show_wheel === true && f.crypto_discount_enabled === true && f.download.macos === true, 'flags: смаплены + сырые');
  chk(Array.isArray(d.campaigns) && d.campaigns[0].percent === 20, 'discounts: кампания −20%');
  chk(Array.isArray(n) && n.length === 1 && n[0].code === 'NL', 'nodes: из манифеста, relay-only отфильтрован');
  chk(ac.plan === 'premium' && /^\d{4}-\d{2}-\d{2}$/.test(ac.expiry_at), 'account: план+срок из billing/status');
  chk(lg.balance.hours === 99 && lg.balance.minutes === 50, 'ledger: баланс 5990 мин → 99ч50м');
  chk(Array.isArray(lg.entries) && lg.entries.length === 2, 'ledger: история смаплена');
  chk(rf.code === 'SYM-ABC' && rf.invited === 12 && rf.converted === 5 && rf.earned_days === 180, 'referral: код/инвайты/дни');
  chk(A._ready === true, 'bootstrap: _ready');
  console.log(`\n[app-connector] ${ok}/${ok + fail} зелёные`);
  process.exit(fail ? 1 : 0);
}).catch(e => { console.error(e); process.exit(2); });
