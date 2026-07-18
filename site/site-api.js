/* Симбионт — витрина: слой обращения к API (адаптер к реальному бэкенду).

   По умолчанию НИЧЕГО не запрашивает — сайт работает на значениях из
   site-config.js. Когда бэкенд готов:

       SYM_API.base = 'https://api.simbiont.app';   // ваш origin ('' = тот же)
       SYM_API.setToken(token);                      // после входа (login-gate)
       SYM_API.bootstrap();                          // подтянуть economy/flags/discounts

   bootstrap() тянет конфиги параллельно, ПРЕОБРАЗУЕТ их из формы бэкенда в форму
   витрины (site-config.js) и рассылает 'sym-config'. Страницы слушают и
   перерисовываются — цены/платформы/флаги/скидки обновятся без перезагрузки.

   Реальные эндпоинты бэкенда (symbiont_build/backend/server.py):
     GET  /v1/config/economy       — тарифы/цены/продукты/способы оплаты/крипто-скидка
     GET  /v1/config/flags         — фиче-флаги (гейтинг блоков/платформ)
     GET  /v1/config/discounts     — активные авто-кампании (−N%)
     POST /v1/config/parse-bridge  — разбор ссылки «свой мост» { uri } → узел
     POST /v1/billing/purchase     — покупка { product, method, region, discount_code }
     POST /v1/billing/discount/check — проверка купона { code, product }
   Аутентификация — Bearer-токен в заголовке (не куки), поэтому credentials:'omit'. */
(function () {
  function request(url, opts) {
    return fetch(url, opts).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        if (t) { try { data = JSON.parse(t); } catch (e) { data = null; } }
        if (!r.ok) {
          var err = new Error('HTTP ' + r.status + ' ' + url);
          err.status = r.status; err.data = data;
          throw err;
        }
        return data;
      });
    });
  }

  // ── Адаптеры: форма ответа бэкенда → форма витрины (site-config.js) ──────────
  function adaptEconomy(be) {
    if (!be || !be.prices) return null;
    var P = be.prices, out = { crypto_discount: P.crypto_discount };
    if (P.ru && P.intl) {
      out.prices = { premium: { rub: P.ru.premium, usd: P.intl.premium },
                     ultimate: { rub: P.ru.ultimate, usd: P.intl.ultimate } };
    }
    if (P.balance) out.balance = { premium: { rub: P.balance.ru, usd: P.balance.intl } };
    if (be.payments) {
      var mm = function (a) { return (a || []).map(function (m) { return m === 'mastercard' ? 'mc' : m; }); };
      out.methods = { rub: mm(be.payments.ru), usd: mm(be.payments.intl) };
    }
    if (be.tiers && be.tiers.length) {
      out.tiers = {};
      be.tiers.forEach(function (t) {
        var f = t.features || {};
        var loc = (f.locations === 'all' || f.locations === 'all+premium') ? -1 : f.locations;
        out.tiers[t.id] = { devices: t.devices, relay: f.relay, speed: f.speed,
                            locations: loc, dedicated_ip: !!f.dedicated_ip, gaming: !!f.game_mode };
      });
    }
    return out;
  }

  function adaptFlags(be) {
    if (!be) return null;
    var p = be.pricing || {}, a = be.app || {}, s = be.site || {};
    var out = {
      show_balance_packages: p.balance !== false,
      crypto_discount_enabled: p.crypto_discount !== false,
      wheel_enabled: a.wheel !== false,
      referrals_enabled: a.referrals !== false,
      show_key_check: a.key_checker !== false,
      show_bonuses: s.referrals !== false
    };
    // сырые флаги бэкенда — для точечного гейтинга платформ/секций на страницах
    out.download = be.download; out.coming_soon = be.coming_soon;
    out.site = be.site; out.legal = be.legal; out.pricing = be.pricing; out.app = be.app;
    return out;
  }

  function adaptDiscounts(be) {
    if (!be) return null;
    return { campaigns: be.campaigns || [] };   // combo/referral остаются из site-config (они верны)
  }

  var SYM_API = {
    base: '',                    // '' = тот же origin; иначе 'https://api.example.com'
    credentials: 'omit',         // Bearer-токен в заголовке, не куки
    headers: {},                 // напр. { Authorization: 'Bearer ' + token }

    endpoints: {
      economy:       '/v1/config/economy',
      flags:         '/v1/config/flags',
      discounts:     '/v1/config/discounts',
      parseBridge:   '/v1/config/parse-bridge',
      purchase:      '/v1/billing/purchase',
      discountCheck: '/v1/billing/discount/check'
    },

    setToken: function (t) {
      var h = Object.assign({}, this.headers);
      if (t) h.Authorization = 'Bearer ' + t; else delete h.Authorization;
      this.headers = h;
      try { if (t) localStorage.setItem('sym-token', t); else localStorage.removeItem('sym-token'); } catch (e) {}
    },
    loadToken: function () { try { var t = localStorage.getItem('sym-token'); if (t) this.setToken(t); return t || null; } catch (e) { return null; } },
    hasToken: function () { return !!(this.headers && this.headers.Authorization); },

    _get: function (path) {
      return request((this.base || '') + path, {
        method: 'GET', credentials: this.credentials,
        headers: Object.assign({ 'Accept': 'application/json' }, this.headers)
      });
    },
    _post: function (path, body) {
      return request((this.base || '') + path, {
        method: 'POST', credentials: this.credentials,
        headers: Object.assign({ 'Content-Type': 'application/json', 'Accept': 'application/json' }, this.headers),
        body: JSON.stringify(body || {})
      });
    },

    getEconomy:   function () { return this._get(this.endpoints.economy).then(adaptEconomy); },
    getFlags:     function () { return this._get(this.endpoints.flags).then(adaptFlags); },
    getDiscounts: function () { return this._get(this.endpoints.discounts).then(adaptDiscounts); },

    /* Разбор пользовательской ссылки «свой мост» → узел каскада. */
    parseBridge: function (uri) { return this._post(this.endpoints.parseBridge, { uri: uri }); },

    /* Проверка купона до оплаты → { ok, percent, final_amount, currency }. */
    checkDiscount: function (code, product) {
      return this._post(this.endpoints.discountCheck, { code: code, product: product });
    },

    /* Покупка. payload { product, method, currency('rub'|'usd'), discountCode? }.
       Нормализует ответ бэкенда к виду витрины { status:'ok'|'pending', paymentId, checkoutUrl }. */
    purchase: function (payload) {
      payload = payload || {};
      var region = (payload.currency === 'usd' || payload.region === 'intl') ? 'intl' : 'ru';
      var body = { product: payload.product, region: region,
                   method: (payload.method === 'mc' ? 'mastercard' : payload.method) };
      var code = payload.discountCode || payload.discount_code;
      if (code) body.discount_code = code;
      var apiBase = this.base || '';
      return this._post(this.endpoints.purchase, body).then(function (r) {
        if (!r) return r;
        // checkout_url бэкенда — относительный (/v1/billing/...). Если API на другом
        // origin, редиректить нужно на origin БЭКЕНДА, иначе попадём на сайт (404).
        var co = r.checkout_url;
        if (co && co.charAt(0) === '/' && apiBase) co = apiBase.replace(/\/+$/, '') + co;
        return { status: (r.status === 'completed' ? 'ok' : r.status),
                 paymentId: r.payment_id, checkoutUrl: co, qr: r.qr,
                 amount: r.amount, currency: r.currency, discount: r.discount,
                 subscription: r.subscription, raw: r };
      });
    },

    // ── Аккаунт (login-gate: покупки требуют токен) ─────────────────────────────
    _sha256hex: function (str) {
      var buf = new TextEncoder().encode(str);
      return crypto.subtle.digest('SHA-256', buf).then(function (d) {
        return Array.from(new Uint8Array(d)).map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
      });
    },
    _solvePow: function (challenge, bits) {
      var self = this; bits = bits | 0;
      if (!challenge || bits <= 0) return Promise.resolve('0');   // bits=0 → любой nonce
      var prefix = new Array(bits + 1).join('0'), n = 0;
      function step() {
        return self._sha256hex(String(challenge) + n).then(function (h) {
          if (h.slice(0, bits) === prefix) return String(n);
          n++; return (n > 5000000) ? '0' : step();
        });
      }
      return step();
    },
    register: function (opts) {
      opts = opts || {}; var self = this;
      return this._get('/v1/account/pow').then(function (p) {
        return self._solvePow(p.challenge, p.bits || p.difficulty || 0).then(function (nonce) {
          return self._post('/v1/account/register', {
            aliases: opts.aliases || [{ value: opts.nick || 'guest', kind: 'nick' }],
            password: opts.password || null,
            device: opts.device || { name: 'web', platform: 'web' },
            invite: opts.invite || null,
            pow_challenge: p.challenge, pow_nonce: nonce
          });
        });
      }).then(function (res) { if (res && res.token) self.setToken(res.token); return res; });
    },
    login: function (opts) {
      var self = this;
      return this._post('/v1/account/login', opts || {}).then(function (res) {
        if (res && res.token) self.setToken(res.token); return res;
      });
    },
    recover: function (opts) {
      var self = this;
      return this._post('/v1/account/recover', opts || {}).then(function (res) {
        if (res && res.token) self.setToken(res.token); return res;
      });
    },
    logout: function () { this.setToken(null); },
    billingStatus: function () { return this._get('/v1/billing/status'); },
    keyCheck: function (code) { return this._post('/v1/key/check', { code: code }); },

    /* Удобный старт: задать origin, поднять токен из localStorage, подтянуть конфиги. */
    connect: function (base) { if (base != null) this.base = base; this.loadToken(); return this.bootstrap(); },

    /* Тянет конфиги параллельно, вливает в SYM_CONFIG, рассылает 'sym-config'.
       Ошибки отдельных запросов проглатываются — остаются значения по умолчанию. */
    bootstrap: function () {
      this._ready = true;   // сайт «подключён» — страницы могут ходить в API
      function into(section) {
        return function (d) {
          if (d && window.SYM_CONFIG && window.SYM_CONFIG.merge) {
            var patch = {}; patch[section] = d; window.SYM_CONFIG.merge(patch);
          }
        };
      }
      function ignore() {}
      var jobs = [
        this.getEconomy().then(into('economy')).catch(ignore),
        this.getFlags().then(into('flags')).catch(ignore),
        this.getDiscounts().then(into('discounts')).catch(ignore)
      ];
      return Promise.all(jobs).then(function () {
        try { window.dispatchEvent(new CustomEvent('sym-config', { detail: window.SYM_CONFIG })); } catch (e) {}
        return window.SYM_CONFIG;
      });
    }
  };

  window.SYM_API = SYM_API;
})();
