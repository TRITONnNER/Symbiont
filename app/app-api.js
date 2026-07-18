/* Симбионт — приложение: слой обращения к API (адаптер к реальному бэкенду).

   По умолчанию НИЧЕГО не запрашивает — приложение работает на значениях из
   app-config.js. Когда бэкенд готов:

       SYM_API.base = 'https://api.simbiont.app';   // ваш origin ('' = тот же)
       SYM_API.setToken(token);                      // после входа (login-gate)
       SYM_API.bootstrap();                          // config + (при токене) аккаунт/биллинг

   bootstrap() ПРЕОБРАЗУЕТ ответы бэкенда в форму приложения (app-config.js) и
   рассылает 'sym-config'. Приложение слушает и перерисовывается.

   Реальные эндпоинты (symbiont_build/backend/server.py):
     GET  /v1/config/economy    · /v1/config/flags · /v1/config/discounts
     POST /v1/config/parse-bridge { uri }
     GET  /v1/manifest          — узлы (адаптируются в список серверов)
     POST /v1/billing/purchase  { product, method, region, discount_code }
     POST /v1/billing/discount/check { code, product }
     GET  /v1/billing/status    — подписка/грант · /v1/billing/ledger — баланс+история
     GET  /v1/referral · /v1/account/devices
     GET  /v1/account/pow · POST /v1/account/register|login|recover
   Аутентификация — Bearer-токен (не куки) → credentials:'omit'. */
(function () {
  var REQ_TIMEOUT_MS = 15000;   // без таймаута зависший бэкенд подвешивал fetch навсегда → bootstrap/Promise.all не завершались
  function request(url, opts) {
    opts = opts || {};
    var ctl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var tid = ctl ? setTimeout(function () { ctl.abort(); }, REQ_TIMEOUT_MS) : null;
    if (ctl) opts = Object.assign({}, opts, { signal: ctl.signal });
    return fetch(url, opts).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        if (t) { try { data = JSON.parse(t); } catch (e) { data = null; } }
        if (!r.ok) { var err = new Error('HTTP ' + r.status + ' ' + url); err.status = r.status; err.data = data; throw err; }
        return data;
      });
    }).catch(function (e) {
      if (e && e.name === 'AbortError') { var te = new Error('timeout ' + url); te.status = 0; throw te; }
      throw e;
    }).finally(function () { if (tid) clearTimeout(tid); });
  }

  // ── Адаптеры: форма бэкенда → форма приложения (app-config.js) ───────────────
  function adaptEconomy(be) {
    if (!be || !be.prices) return null;
    var P = be.prices, out = { crypto_discount: P.crypto_discount };
    if (P.ru && P.intl) out.prices = { premium: { rub: P.ru.premium, usd: P.intl.premium }, ultimate: { rub: P.ru.ultimate, usd: P.intl.ultimate } };
    if (P.balance) out.balance = { premium: { rub: P.balance.ru, usd: P.balance.intl } };
    if (be.payments) { var mm = function (a) { return (a || []).map(function (m) { return m === 'mastercard' ? 'mc' : m; }); }; out.methods = { rub: mm(be.payments.ru), usd: mm(be.payments.intl) }; }
    if (be.tiers && be.tiers.length) {
      out.tiers = {};
      be.tiers.forEach(function (t) { var f = t.features || {}; out.tiers[t.id] = { devices: t.devices, relay: f.relay, speed: f.speed, locations: (f.locations === 'all' || f.locations === 'all+premium') ? -1 : f.locations, dedicated_ip: !!f.dedicated_ip, gaming: !!f.game_mode }; });
    }
    return out;
  }
  function adaptFlags(be) {
    if (!be) return null;
    var a = be.app || {}, p = be.pricing || {};
    return { show_wheel: a.wheel !== false, show_referral: a.referrals !== false, show_vouchers: true,
             show_key_check: a.key_checker !== false, crypto_discount_enabled: p.crypto_discount !== false,
             download: be.download, site: be.site, legal: be.legal, pricing: be.pricing, app: be.app };
  }
  function adaptDiscounts(be) { return be ? { campaigns: be.campaigns || [] } : null; }
  function adaptNodes(man) {
    if (!man || !Array.isArray(man.nodes)) return null;
    var out = [];
    man.nodes.forEach(function (n) {
      var roles = n.roles || [];
      if (roles.indexOf('relay') !== -1 && roles.length === 1) return;   // реле — часть каскада, не выбирается
      out.push({ code: (n.code || '').toUpperCase(), host: (n.id || n.code || 'node') + '.symbiont.net',
                 ping: Math.max(18, Math.round(24 + (n.loadPct || 0) * 0.9)), load: n.loadPct || 0, fav: false });
    });
    return out.length ? out : null;
  }
  function adaptAccount(b) {
    if (!b || !b.subscription) return null;
    var s = b.subscription, out = { plan: s.tier };
    if (s.days_left > 0) { var t = new Date(Date.now() + s.days_left * 86400000); out.expiry_at = t.toISOString().slice(0, 10); }
    return out;
  }
  function adaptLedger(be) {
    if (!be) return null;
    var sub = be.subscription || {}, mins = sub.active_minutes_left || 0;
    var out = { balance: { hours: Math.floor(mins / 60), minutes: mins % 60 } };
    if (Array.isArray(be.entries)) {
      // Бэкенд уже отдаёт журнал новыми записями сверху (API.md: «Новые записи —
      // сверху»), и оболочка рендерит их в порядке массива — доп. reverse переворачивал
      // ленту в старые-сверху.
      out.entries = be.entries.slice().map(function (e) {
        var val = '', unit = '';
        if (e.days != null) { val = (e.days >= 0 ? '+' : '') + e.days; unit = 'd'; }
        else if (e.minutes != null) { val = (e.kind === 'debit' ? '−' : '+') + Math.abs(e.minutes); unit = 'm'; }
        var d = new Date((e.at || 0) * 1000);
        var date = String(d.getDate()).padStart(2, '0') + '.' + String(d.getMonth() + 1).padStart(2, '0') + '.' + d.getFullYear();
        return { kind: e.kind, val: val, unit: unit, date: date };
      });
    }
    return out;
  }
  function adaptReferral(be) {
    if (!be) return null;
    var out = {};
    if (be.code || be.invite_code) out.code = be.code || be.invite_code;
    if (be.invited != null) out.invited = be.invited; else if (Array.isArray(be.referrals)) out.invited = be.referrals.length;
    if (be.paying != null) out.converted = be.paying; else if (be.converted != null) out.converted = be.converted;
    if (be.earned_days != null) out.earned_days = be.earned_days;
    return out;
  }

  var SYM_API = {
    base: '', credentials: 'omit', headers: {},
    endpoints: {
      economy: '/v1/config/economy', flags: '/v1/config/flags', discounts: '/v1/config/discounts',
      parseBridge: '/v1/config/parse-bridge', manifest: '/v1/manifest',
      purchase: '/v1/billing/purchase', discountCheck: '/v1/billing/discount/check',
      billingStatus: '/v1/billing/status', ledger: '/v1/billing/ledger', payment: '/v1/billing/payment/',
      referral: '/v1/referral', devices: '/v1/account/devices'
    },

    setToken: function (t) {
      var h = Object.assign({}, this.headers);
      if (t) h.Authorization = 'Bearer ' + t; else delete h.Authorization;
      this.headers = h;
      try { if (t) localStorage.setItem('sym-token', t); else localStorage.removeItem('sym-token'); } catch (e) {}
    },
    loadToken: function () { try { var t = localStorage.getItem('sym-token'); if (t) this.setToken(t); return t || null; } catch (e) { return null; } },
    hasToken: function () { return !!(this.headers && this.headers.Authorization); },

    _get: function (p) { return request((this.base || '') + p, { method: 'GET', credentials: this.credentials, headers: Object.assign({ 'Accept': 'application/json' }, this.headers) }); },
    _post: function (p, b) { return request((this.base || '') + p, { method: 'POST', credentials: this.credentials, headers: Object.assign({ 'Content-Type': 'application/json', 'Accept': 'application/json' }, this.headers), body: JSON.stringify(b || {}) }); },

    getEconomy: function () { return this._get(this.endpoints.economy).then(adaptEconomy); },
    getFlags: function () { return this._get(this.endpoints.flags).then(adaptFlags); },
    getDiscounts: function () { return this._get(this.endpoints.discounts).then(adaptDiscounts); },
    getNodes: function () { return this._get(this.endpoints.manifest).then(adaptNodes); },
    getAccount: function () { return this._get(this.endpoints.billingStatus).then(adaptAccount); },
    getLedger: function () { return this._get(this.endpoints.ledger).then(adaptLedger); },
    getReferrals: function () { return this._get(this.endpoints.referral).then(adaptReferral); },
    getDevices: function () { return this._get(this.endpoints.devices); },
    getPayment: function (id) { return this._get(this.endpoints.payment + encodeURIComponent(id)); },
    parseBridge: function (uri) { return this._post(this.endpoints.parseBridge, { uri: uri }); },
    checkDiscount: function (code, product, region) { return this._post(this.endpoints.discountCheck, { code: code, product: product, region: region || 'ru' }); },

    purchase: function (payload) {
      payload = payload || {};
      var region = (payload.currency === 'usd' || payload.region === 'intl') ? 'intl' : 'ru';
      var body = { product: payload.product, region: region, method: (payload.method === 'mc' ? 'mastercard' : payload.method) };
      var code = payload.discountCode || payload.discount_code; if (code) body.discount_code = code;
      var apiBase = this.base || '';
      return this._post(this.endpoints.purchase, body).then(function (r) {
        if (!r) return r;
        // относительный checkout_url бэкенда → абсолютный (origin бэкенда), если API на другом хосте
        var co = r.checkout_url;
        if (co && co.charAt(0) === '/' && apiBase) co = apiBase.replace(/\/+$/, '') + co;
        return { status: (r.status === 'completed' ? 'ok' : r.status), paymentId: r.payment_id, checkoutUrl: co, qr: r.qr, amount: r.amount, currency: r.currency, discount: r.discount, subscription: r.subscription, raw: r };
      });
    },

    // ── аккаунт (login-gate) ──
    _sha256hex: function (str) { var buf = new TextEncoder().encode(str); return crypto.subtle.digest('SHA-256', buf).then(function (d) { return Array.from(new Uint8Array(d)).map(function (b) { return b.toString(16).padStart(2, '0'); }).join(''); }); },
    // Число ведущих НУЛЕВЫХ БИТ в hex-дайджесте (1:1 с backend identity.pow_ok:
    // sha256(challenge:nonce) < 2^(256-bits)). Раньше клиент считал нулевые hex-символы
    // и не ставил разделитель ':' — nonce не проходил серверную проверку при POW_BITS>0.
    _powBits: function (h) { var c = 0; for (var i = 0; i < h.length; i++) { var v = parseInt(h[i], 16); if (v === 0) { c += 4; continue; } c += (v >= 8 ? 0 : v >= 4 ? 1 : v >= 2 ? 2 : 3); break; } return c; },
    _solvePow: function (ch, bits) { var self = this; bits = bits | 0; if (!ch || bits <= 0) return Promise.resolve('0'); var n = 0; function step() { return self._sha256hex(String(ch) + ':' + n).then(function (h) { if (self._powBits(h) >= bits) return String(n); n++; return n > 5000000 ? '0' : step(); }); } return step(); },
    register: function (opts) {
      opts = opts || {}; var self = this;
      return this._get('/v1/account/pow').then(function (p) {
        return self._solvePow(p.challenge, p.bits || p.difficulty || 0).then(function (nonce) {
          return self._post('/v1/account/register', { aliases: opts.aliases || [{ value: opts.nick || 'guest', kind: 'nick' }], password: opts.password || null, device: opts.device || { name: 'app', platform: 'app' }, invite: opts.invite || null, pow_challenge: p.challenge, pow_nonce: nonce });
        });
      }).then(function (res) { if (res && res.token) self._afterAuth(res); return res; });
    },
    login: function (opts) { var self = this; return this._post('/v1/account/login', opts || {}).then(function (res) { if (res && res.token) self._afterAuth(res); return res; }); },
    recover: function (opts) { var self = this; return this._post('/v1/account/recover', opts || {}).then(function (res) { if (res && res.token) self._afterAuth(res); return res; }); },
    logout: function () { this.setToken(null); },
    _afterAuth: function (res) {
      this.setToken(res.token);
      try { if (window.SYM_CONFIG && window.SYM_CONFIG.merge) window.SYM_CONFIG.merge({ account: { token: res.token } }); } catch (e) {}
    },

    connect: function (base) { if (base != null) this.base = base; this.loadToken(); return this.bootstrap(); },

    bootstrap: function () {
      this._ready = true;
      var self = this;
      function into(section) { return function (d) { if (d && window.SYM_CONFIG && window.SYM_CONFIG.merge) { var patch = {}; patch[section] = d; window.SYM_CONFIG.merge(patch); } }; }
      function ignore() {}
      var jobs = [
        this.getEconomy().then(into('economy')).catch(ignore),
        this.getFlags().then(into('flags')).catch(ignore),
        this.getDiscounts().then(into('discounts')).catch(ignore),
        this.getNodes().then(into('nodes')).catch(ignore)
      ];
      if (this.hasToken()) {   // приватные данные — только при входе
        jobs.push(this.getAccount().then(into('account')).catch(ignore));
        jobs.push(this.getLedger().then(into('ledger')).catch(ignore));
        jobs.push(this.getReferrals().then(into('referral')).catch(ignore));
      }
      return Promise.all(jobs).then(function () {
        try { window.dispatchEvent(new CustomEvent('sym-config', { detail: window.SYM_CONFIG })); } catch (e) {}
        return window.SYM_CONFIG;
      });
    }
  };

  window.SYM_API = SYM_API;
})();
