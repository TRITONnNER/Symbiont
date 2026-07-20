/*
 * symbiont-bridge.js — мост между дизайн-оболочкой и реальным бэкендом/движком.
 *
 * Оболочка (Симбионт.dc.html) остаётся неизменной по вёрстке: renderVals() и
 * шаблоны не трогаем. Мост лишь подменяет ДАННЫЕ и ДЕЙСТВИЯ:
 *   • window.SYM_API    — клиент REST /v1/* (см. symbiont_build/backend/API.md)
 *   • window.SYM_ENGINE — абстракция VPN-движка (нативные обёртки её реализуют)
 *   • window.SYM_DATA    — «живые» данные (узлы, тарифы…), которые методы
 *                          оболочки читают ПЕРВЫМИ, откатываясь к демо-данным.
 *   • window.SYM_BRIDGE  — загрузка: определить бэкенд, подтянуть манифест/тарифы,
 *                          разбудить оболочку событием 'sym-data'.
 *
 * РЕЖИМЫ:
 *   — Демо (нет бэкенда / live:false): оболочка работает как раньше, байт-в-байт.
 *   — Живой (бэкенд отвечает): узлы/тарифы/ключи/колесо/оплата идут в сеть.
 *
 * Конфигурация — до загрузки этого файла:
 *   window.SYM_CONFIG = { apiBase:'http://127.0.0.1:8600', live:true }
 *   apiBase='' → тот же origin (когда бэкенд отдаёт и статику). live:false → демо.
 */
(function () {
  'use strict';

  var CFG = (typeof window !== 'undefined' && window.SYM_CONFIG) || {};
  var BASE = CFG.apiBase != null ? String(CFG.apiBase) : '';   // '' => same-origin
  var LIVE = CFG.live !== false;                               // явный live:false => демо
  var LS_TOKEN = 'sym_token';

  var TOKEN = null;
  try { TOKEN = localStorage.getItem(LS_TOKEN) || null; } catch (e) {}

  function u(p) { return (BASE || '') + p; }
  function setToken(t) {
    TOKEN = t || null;
    try { if (t) localStorage.setItem(LS_TOKEN, t); else localStorage.removeItem(LS_TOKEN); } catch (e) {}
  }
  function authHeaders(extra) {
    var h = Object.assign({ 'Content-Type': 'application/json' }, extra || {});
    if (TOKEN) h['Authorization'] = 'Bearer ' + TOKEN;
    return h;
  }

  var REQ_TIMEOUT_MS = 15000;   // без таймаута зависший бэкенд подвешивал fetch/req навсегда
  async function req(method, path, body, opt) {
    opt = opt || {};
    var init = { method: method, headers: authHeaders(opt.headers) };
    if (body !== undefined) init.body = JSON.stringify(body);
    var ctl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var tid = ctl ? setTimeout(function () { ctl.abort(); }, REQ_TIMEOUT_MS) : null;
    if (ctl) init.signal = ctl.signal;
    var r;
    try {
      r = await fetch(u(path), init);
    } catch (netErr) {
      var e0 = new Error(netErr && netErr.name === 'AbortError' ? 'timeout' : 'network'); e0.status = 0; e0.cause = netErr; throw e0;
    } finally {
      if (tid) clearTimeout(tid);
    }
    var data = null;
    try { data = await r.json(); } catch (e) { /* пустой/не-JSON ответ */ }
    if (!r.ok) {
      var e = new Error((data && (data.detail || data.message)) || ('http_' + r.status));
      e.status = r.status; e.data = data; throw e;
    }
    return data;
  }

  // ── Proof-of-Work: sha256(challenge:nonce) с `bits` ведущими нулевыми БИТАМИ ──
  // 1:1 с backend identity.pow_ok (sha256(challenge:nonce) < 2^(256-bits)).
  async function sha256hex(str) {
    var buf = new TextEncoder().encode(str);
    var dig = await crypto.subtle.digest('SHA-256', buf);
    var arr = Array.from(new Uint8Array(dig));
    return arr.map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
  }
  function powBits(h) { var c = 0; for (var i = 0; i < h.length; i++) { var v = parseInt(h[i], 16); if (v === 0) { c += 4; continue; } c += (v >= 8 ? 0 : v >= 4 ? 1 : v >= 2 ? 2 : 3); break; } return c; }
  async function solvePow(challenge, bits) {
    bits = bits | 0;
    if (!challenge || bits <= 0) return '0';               // bits=0 → любой nonce годится
    for (var n = 0; n < 5000000; n++) {
      var h = await sha256hex(String(challenge) + ':' + n);
      if (powBits(h) >= bits) return String(n);
    }
    return '0';
  }

  // ── REST-клиент /v1/* ─────────────────────────────────────────────────────────
  var API = {
    base: BASE,
    isLive: function () { return LIVE; },
    hasToken: function () { return !!TOKEN; },
    token: function () { return TOKEN; },
    setToken: setToken,

    // публичные
    manifest: function (since) { return req('GET', '/v1/manifest' + (since ? ('?since=' + since) : '')); },
    economy: function () { return req('GET', '/v1/config/economy'); },
    flags: function () { return req('GET', '/v1/config/flags'); },
    pubkey: function () { return req('GET', '/v1/pubkey'); },
    keyCheck: function (code) { return req('POST', '/v1/key/check', { code: code }); },

    // аккаунт
    anon: function (label) { return req('POST', '/v1/account/anon', { label: label || null }); },
    pow: function () { return req('GET', '/v1/account/pow'); },
    register: async function (opts) {
      opts = opts || {};
      var p = await API.pow();
      var nonce = await solvePow(p.challenge, p.bits || p.difficulty || 0);
      var body = Object.assign({
        aliases: opts.aliases || [{ value: opts.nick || 'guest', kind: 'nick' }],
        password: opts.password || null,
        device: opts.device || { name: 'Устройство', platform: 'web' },
        invite: opts.invite || null,
        pow_challenge: p.challenge,
        pow_nonce: nonce
      }, {});
      var res = await req('POST', '/v1/account/register', body);
      if (res && res.token) setToken(res.token);
      return res;
    },
    login: async function (opts) {
      var res = await req('POST', '/v1/account/login', opts || {});
      if (res && res.token) setToken(res.token);
      return res;
    },
    recover: async function (opts) {
      var res = await req('POST', '/v1/account/recover', opts || {});
      if (res && res.token) setToken(res.token);
      return res;
    },
    logout: function () { setToken(null); },

    // устройства
    devices: function () { return req('GET', '/v1/account/devices'); },
    revokeDevice: function (id) { return req('POST', '/v1/account/devices/revoke', { device_id: id }); },
    promoteDevice: function (id) { return req('POST', '/v1/account/devices/promote', { device_id: id }); },

    // колесо
    wheel: function () { return req('GET', '/v1/wheel'); },
    wheelSpin: function () { return req('POST', '/v1/wheel/spin', {}); },

    // биллинг
    billingStatus: function () { return req('GET', '/v1/billing/status'); },
    billingLedger: function () { return req('GET', '/v1/billing/ledger'); },
    billingPurchase: function (opts) { return req('POST', '/v1/billing/purchase', opts || {}); },
    payment: function (id) { return req('GET', '/v1/billing/payment/' + encodeURIComponent(id)); },
    keyRedeem: function (code) { return req('POST', '/v1/key/redeem', { code: code }); },

    // рефералы
    referral: function () { return req('GET', '/v1/referral'); },

    // поддержка
    supportThread: function () { return req('GET', '/v1/support/thread'); },
    supportSend: function (text) { return req('POST', '/v1/support/message', { text: text }); },

    // «свой мост»: разбор пользовательской ссылки vless/ss/hysteria2 в узел каскада (stateless).
    parseBridge: function (uri) { return req('POST', '/v1/config/parse-bridge', { uri: uri }); }
  };

  // ── Движок VPN: в браузере/веб-портале его нет; нативные обёртки внедряют свой. ──
  // Ожидаемая форма: { connect(node), disconnect(), status():Promise<{conn,stage,ping...}>,
  //                    onEvent(cb) }.  null → оболочка использует симуляцию каскада.
  if (typeof window.SYM_ENGINE === 'undefined') window.SYM_ENGINE = null;

  // ── Преобразование манифеста → узлы в форме, которую понимает _nodes() оболочки ──
  // Манифест: {id, country, code, loadPct, protocols, roles, whiteIp?}.
  // Оболочка сама локализует страну/город по code; мы даём code/host/load/ping/fav.
  function nodesFromManifest(man) {
    if (!man || !Array.isArray(man.nodes)) return null;
    var out = [];
    man.nodes.forEach(function (n) {
      // Реле-узлы (role=relay) — часть каскада, не выбираются вручную: пропускаем в списке.
      var roles = n.roles || [];
      if (roles.indexOf('relay') !== -1 && roles.length === 1) return;
      // РЕАЛЬНЫЙ хост из манифеста. Без host узел не подключить — не показываем его
      // (раньше подставляли выдуманный *.symbiont.net, по которому движок пытался
      // поднять VPN). Так в списке — только настоящие узлы.
      if (!n.host) return;
      out.push({
        code: (n.code || '').toUpperCase(),
        host: n.host,
        // Пинг манифест не отдаёт (меряется клиентом при подключении). Не выдумываем:
        // null = «ещё не измерен», оболочка покажет «—». Нагрузка — реальная, из heartbeat.
        ping: (typeof n.ping === 'number' ? n.ping : null),
        load: (typeof n.loadPct === 'number' ? n.loadPct : null),
        fav: false,
        id: n.id,
        protocols: n.protocols || [],
        _country_en: n.country || null,
        // Секреты подключения — движок берёт их при connect (см. sym_engine_channel).
        port: n.port || 443,
        transport: n.transport || null
      });
    });
    return out.length ? out : null;
  }

  // ── Адаптеры: ответы бэкенда → формы, которые ждёт оболочка (инлайн-компоненты) ──
  function _pad(n) { return (n < 10 ? '0' : '') + n; }
  function _planLabel(tier) {
    var t = String(tier || 'free').toLowerCase();
    return t === 'ultimate' ? 'Ultimate' : t === 'premium' ? 'Premium' : 'Free';
  }
  function _dmy(ms) { var d = new Date(ms); return _pad(d.getDate()) + '.' + _pad(d.getMonth() + 1) + '.' + d.getFullYear(); }
  function _fmtEpoch(epoch) { return epoch ? _dmy(epoch * 1000) : ''; }
  // GET /v1/billing/status → { token, plan, expiry?, invite? } для инлайн-объекта account (шелл).
  function _accountFrom(status, ref) {
    var sub = (status && status.subscription) || {};
    var acc = { token: TOKEN || '', plan: _planLabel(sub.tier) };
    if (sub.days_left && sub.days_left > 0) acc.expiry = _dmy(Date.now() + sub.days_left * 86400000);
    if (ref && ref.invite_code) acc.invite = ref.invite_code;
    return acc;
  }
  function _balanceFrom(status) {
    var sub = (status && status.subscription) || {};
    var mins = sub.active_minutes_left || 0;
    return { h: Math.floor(mins / 60), m: mins % 60 };
  }
  // GET /v1/billing/ledger.entries (at/kind/days/minutes) → [{kind,val,unit,date}] как ждёт шелл.
  function _ledgerFrom(led) {
    return ((led && led.entries) || []).map(function (e) {
      var val = '', unit = 'm';
      if (e.days != null) { val = (e.days >= 0 ? '+' : '') + e.days; unit = 'd'; }
      else if (e.minutes != null) { val = (e.minutes >= 0 ? '+' : '') + e.minutes; unit = 'm'; }
      return { kind: e.kind || 'grant', val: val, unit: unit, date: _fmtEpoch(e.at) };
    });
  }
  function _iconFor(platform) {
    var p = String(platform || '').toLowerCase();
    if (p.indexOf('ios') !== -1 || p.indexOf('android') !== -1 || p.indexOf('phone') !== -1) return 'smartphone';
    if (p.indexOf('mac') !== -1) return 'laptop_mac';
    return 'desktop_windows';
  }
  function _rel(epoch) {
    if (!epoch) return '';
    var s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
    if (s < 90) return 'сейчас';
    if (s < 3600) return Math.floor(s / 60) + ' мин назад';
    if (s < 86400) return Math.floor(s / 3600) + ' ч назад';
    return Math.floor(s / 86400) + ' дн назад';
  }
  // GET /v1/account/devices.devices → [{name,icon,last,owner,current,id}] как ждёт шелл (демо _devRaw).
  function _devicesFrom(dv) {
    return ((dv && dv.devices) || []).filter(function (d) { return !d.revoked; }).map(function (d) {
      return { name: d.name || 'Устройство', icon: _iconFor(d.platform), last: _rel(d.lastSeen),
               owner: d.role === 'owner', current: !!d.current, id: d.id };
    });
  }
  // SYM_ENGINE.traffic().conns (TrafficConn {host,rule,up,down}) → [{host,path,d,u}] (шелл: _trData).
  function _mbps(bytes) {
    if (!bytes || bytes <= 0) return '0';
    var mb = bytes / 1048576;
    return mb >= 10 ? String(Math.round(mb)) : (mb >= 0.1 ? mb.toFixed(1) : mb.toFixed(2));
  }
  function _trafficFrom(conns) {
    return (conns || []).map(function (c) {
      var rej = c.rule === 'reject' || c.rule === 'block';
      return { host: c.host, path: c.rule === 'direct' ? 'direct' : (rej ? 'reject' : 'proxy'),
               d: rej ? '—' : _mbps(c.down), u: rej ? '—' : _mbps(c.up) };
    });
  }
  // SYM_ENGINE.scanApps().apps (InstalledApp {name,exe,path}) → [{id,name,exe,cat,logo}] (шелл: DEVICE_APPS).
  function _catFor(exe, name) {
    var s = ((exe || '') + ' ' + (name || '')).toLowerCase();
    if (/chrome|firefox|edge|opera|yandex|browser|safari|tor /.test(s)) return 'browser';
    if (/telegram|whatsapp|discord|viber|signal|slack|skype/.test(s)) return 'messenger';
    if (/steam|epicgames|riot|battle|minecraft|roblox|game|dota|valorant/.test(s)) return 'game';
    if (/vlc|spotify|youtube|kmplayer|potplayer|netflix|media|player/.test(s)) return 'media';
    return 'dev';
  }
  function _appsFrom(apps) {
    return (apps || []).map(function (a) {
      return { id: a.exe || a.name, name: a.name, exe: a.exe || '', cat: _catFor(a.exe, a.name), logo: '' };
    });
  }

  // ── Загрузка: определить бэкенд, подтянуть данные, разбудить оболочку ───────────
  var _comps = [];            // все смонтированные экземпляры оболочки
  var _bootPromise = null;    // single-flight: сколько бы экземпляров ни звало boot()

  var BRIDGE = {
    ready: false,
    config: { apiBase: BASE, live: LIVE },

    // Надёжный признак «живого» режима: не изменяемый флаг (его гонки затирают),
    // а факт наличия загруженного манифеста от бэкенда.
    isLive: function () { return LIVE && !!(window.SYM_DATA && window.SYM_DATA.manifest); },
    get live() { return this.isLive(); },

    attach: function (comp) { if (comp && _comps.indexOf(comp) === -1) _comps.push(comp); },

    _wake: function () {
      // Оболочка перерисуется: _nodes() и пр. прочитают window.SYM_DATA.
      _comps.forEach(function (c) { try { if (c && c.forceUpdate && c._isMounted !== false) c.forceUpdate(); } catch (e) {} });
      try { window.dispatchEvent(new Event('sym-data')); } catch (e) {}
    },

    boot: function (comp) {
      if (comp) this.attach(comp);
      window.SYM_DATA = window.SYM_DATA || {};
      if (_bootPromise) return _bootPromise;      // уже грузим/загрузили — не дублируем
      var self = this;
      _bootPromise = (async function () {
        if (!LIVE) { self.ready = true; return; }
        try {
          var man = await API.manifest();
          var nodes = nodesFromManifest(man);
          // Бэкенд ОТВЕТИЛ → всегда выставляем массив (пусть даже пустой). Так оболочка
          // отличает «ответил, узлов нет» (честный пустой список) от «ещё не грузили».
          window.SYM_DATA.nodes = nodes || [];
          window.SYM_DATA.manifest = man;           // ← отсюда isLive() = true
        } catch (e) {
          // Бэкенд недоступен → тихий откат в демо-режим (важно для оффлайн-показа).
          window.SYM_DATA.offlineBackend = true;
          self.ready = true;
          self._wake();
          return;
        }
        try { window.SYM_DATA.economy = await API.economy(); } catch (e) {}
        try { window.SYM_DATA.flags = await API.flags(); } catch (e) {}   // видимость блоков (сайт+приложение)
        try { await self.refreshAccount(); } catch (e) {}                 // живые данные аккаунта (если есть токен)
        try { self.startTraffic(); } catch (e) {}                         // живая карта трафика (когда движок подключён)
        self.ready = true;
        self._wake();
      })();
      return _bootPromise;
    },

    // Живой трафик: пока движок подключён, тянем соединения каждые ~2.5с → SYM_DATA.traffic.
    startTraffic: function () {
      if (this._trafInt || typeof window === 'undefined' || !window.SYM_ENGINE || !window.SYM_ENGINE.traffic) return;
      var self = this;
      var tick = function () {
        try { Promise.resolve(window.SYM_ENGINE.traffic()).then(function (r) {
          if (r && r.ok && r.conns) { window.SYM_DATA.traffic = _trafficFrom(r.conns); self._wake(); }
        }).catch(function () {}); } catch (e) {}
      };
      this._trafInt = setInterval(tick, 2500); tick();
    },
    // Реальные установленные приложения (для сканера «Маршрутизации») — по запросу.
    refreshApps: function () {
      if (typeof window === 'undefined' || !window.SYM_ENGINE || !window.SYM_ENGINE.scanApps) return Promise.resolve();
      var self = this;
      return Promise.resolve(window.SYM_ENGINE.scanApps()).then(function (r) {
        if (r && r.ok && r.apps) { window.SYM_DATA.installedApps = _appsFrom(r.apps); self._wake(); }
      }).catch(function () {});
    },
    // Применить правила маршрутизации / настройки защиты к нативному движку.
    applyRules: function (rules) { try { if (window.SYM_ENGINE && window.SYM_ENGINE.applyRules) window.SYM_ENGINE.applyRules(rules || []); } catch (e) {} },
    setProtection: function (p) { try { if (window.SYM_ENGINE && window.SYM_ENGINE.setProtection) window.SYM_ENGINE.setProtection(p || {}); } catch (e) {} },

    // Живые данные аккаунта: тариф/срок/инвайт, баланс, история, рефералы, устройства.
    // Только при наличии токена (иначе аккаунта ещё нет — оболочка покажет онбординг).
    refreshAccount: async function () {
      if (!LIVE || !API.hasToken()) return;
      var status = null, ref = null;
      try { status = await API.billingStatus(); } catch (e) {}
      try { ref = await API.referral(); } catch (e) {}
      window.SYM_DATA.account = _accountFrom(status, ref);
      window.SYM_DATA.balance = _balanceFrom(status);
      if (ref) window.SYM_DATA.referral = ref;
      try { window.SYM_DATA.ledger = _ledgerFrom(await API.billingLedger()); } catch (e) {}
      try { window.SYM_DATA.devices = _devicesFrom(await API.devices()); } catch (e) {}
      try { window.SYM_DATA.wheel = await API.wheel(); } catch (e) {}
      this._wake();
    },

    // После register: реальный код восстановления показывается ОДИН раз на экране онбординга.
    setRecovery: function (code) {
      window.SYM_DATA.account = window.SYM_DATA.account || {};
      window.SYM_DATA.account.recovery = code || '';
      this._wake();
    }
  };

  // Глобальный помощник для гейтинга «живых» действий в оболочке.
  window.SYM_LIVE = function () { return !!(window.SYM_API && window.SYM_API.isLive() && window.SYM_DATA && window.SYM_DATA.manifest); };

  // Фиче-флаги: показывать ли блок. Путь через точку ('download.macos').
  // FAIL-OPEN: если флаги не загружены или путь неизвестен → возвращаем dflt (по умолчанию true),
  // чтобы отсутствие бэкенда/опечатка не гасили витрину. Явный false в манифесте прячет блок.
  window.SYM_FLAG = function (path, dflt) {
    var def = (dflt === undefined) ? true : dflt;
    var f = window.SYM_DATA && window.SYM_DATA.flags;
    if (!f || !path) return def;
    var cur = f;
    var parts = String(path).split('.');
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== 'object' || !(parts[i] in cur)) return def;
      cur = cur[parts[i]];
    }
    return cur === undefined ? def : cur;
  };

  window.SYM_API = API;
  window.SYM_BRIDGE = BRIDGE;
  window.SYM_DATA = window.SYM_DATA || {};
})();
