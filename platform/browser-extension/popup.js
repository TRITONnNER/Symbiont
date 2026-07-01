/* Компаньон-попап: статус защиты, быстрый выбор узла, ссылки на портал.
   Данные — через window.SYM_API (тот же бэкенд). Управление движком — через
   background.js → native-messaging хост установленного приложения. */
'use strict';

var API = window.SYM_API;
var EXT = window.SYM_EXT;
var state = { conn: 'idle', nodes: [], sel: 0 };

function $(id) { return document.getElementById(id); }
function flagUrl(code) { return 'vendor/flags/' + String(code || '').toLowerCase() + '.png'; }

function send(msg) {
  return new Promise(function (res) {
    try { chrome.runtime.sendMessage(msg, function (r) { res(r || {}); }); }
    catch (e) { res({ error: String(e) }); }
  });
}

function renderStatus() {
  var on = state.conn === 'connected' || state.conn === 'blocking';
  $('pill').className = 'pill ' + (on ? 'on' : 'off');
  var map = { idle: 'Отключено', measuring: 'Проверка сети…', connecting: 'Подключение…',
              connected: 'Защищено', blocking: 'Обходим блокировку…', error: 'Ошибка сети' };
  $('status').textContent = map[state.conn] || state.conn;
  var n = state.nodes[state.sel];
  $('statusSub').textContent = on && n ? (n.country + ' · ' + n.host) : 'движок в приложении';
  var btn = $('toggle');
  if (on) { btn.textContent = 'Отключиться'; btn.className = 'btn off'; }
  else { btn.textContent = 'Подключиться'; btn.className = 'btn'; }
}

function renderNodes() {
  var box = $('nodes');
  box.innerHTML = '';
  var top = state.nodes.slice().sort(function (a, b) { return a.ping - b.ping; }).slice(0, 4);
  top.forEach(function (n) {
    var idx = state.nodes.indexOf(n);
    var el = document.createElement('div');
    el.className = 'node' + (idx === state.sel ? ' sel' : '');
    el.innerHTML =
      '<span class="flag" style="background-image:url(' + flagUrl(n.code) + ')"></span>' +
      '<span class="nm">' + n.country + '</span>' +
      '<span class="png">' + n.ping + ' мс</span>';
    el.onclick = function () { state.sel = idx; renderNodes(); renderStatus(); };
    box.appendChild(el);
  });
}

// Страна по коду (короткий список; для остального — сам код).
var CC = { NL:'Нидерланды', DE:'Германия', FI:'Финляндия', SE:'Швеция', FR:'Франция',
           TR:'Турция', US:'США', JP:'Япония', RU:'Россия', GB:'Британия' };

async function loadNodes() {
  try {
    var man = await API.manifest();
    state.nodes = (man.nodes || [])
      .filter(function (n) { var r = n.roles || []; return !(r.length === 1 && r[0] === 'relay'); })
      .map(function (n) {
        return { code: (n.code || '').toUpperCase(),
                 country: CC[(n.code || '').toUpperCase()] || n.country || n.code,
                 host: (n.id || n.code) + '.symbiont.net',
                 ping: Math.max(18, Math.round(24 + (n.loadPct || 0) * 0.9)) };
      });
    renderNodes();
  } catch (e) {
    $('nodes').innerHTML = '<div class="muted" style="font-size:12px;padding:6px">Бэкенд недоступен</div>';
  }
}

async function loadPlan() {
  try {
    if (!API.hasToken()) { $('plan').textContent = 'гость'; return; }
    var s = await API.billingStatus();
    var tier = (s.subscription && s.subscription.tier) || 'free';
    $('plan').textContent = tier;
  } catch (e) { $('plan').textContent = ''; }
}

async function refreshConn() {
  var r = await send({ type: 'status' });
  if (r && r.conn) { state.conn = r.conn; renderStatus(); }
}

$('toggle').onclick = async function () {
  var on = state.conn === 'connected' || state.conn === 'blocking';
  if (on) { state.conn = 'idle'; renderStatus(); await send({ type: 'disconnect' }); }
  else {
    state.conn = 'connecting'; renderStatus();
    var n = state.nodes[state.sel];
    var r = await send({ type: 'connect', node: n ? (n.host) : null });
    state.conn = (r && r.conn) || (r && r.ok ? 'connected' : 'error');
    renderStatus();
  }
};
$('portal').onclick = function () { chrome.tabs.create({ url: EXT.portalUrl }); };
$('rules').onclick = function () { chrome.tabs.create({ url: EXT.portalUrl + '?screen=routing' }); };

// Живые события от native-хоста → обновляем статус.
try {
  chrome.runtime.onMessage.addListener(function (msg) {
    if (msg && msg.type === 'engine-event' && msg.conn) { state.conn = msg.conn; renderStatus(); }
  });
} catch (e) {}

renderStatus();
loadNodes();
loadPlan();
refreshConn();
