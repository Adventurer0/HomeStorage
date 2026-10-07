'use strict';

const ICONS = {
  home: '<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  box: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 5v10l9 5 9-5V8M12 13v10M7.5 5.5l9 5"/>',
  pin: '<path d="M20 10c0 6-8 11-8 11S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  history: '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l4 2"/>',
  search: '<circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  up: '<path d="m5 15 7-7 7 7"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  edit: '<path d="m16 3 5 5-12 12H4v-5L16 3Zm-3 3 5 5"/>',
  qr: '<path d="M3 3h6v6H3zm12 0h6v6h-6zM3 15h6v6H3zM15 15h2v2h-2zM19 15h2v6h-6v-2M12 3v2M12 9v3H3M12 16v5M16 12h5"/>',
  logout: '<path d="M9 3H4v18h5m6-14 5 5-5 5m-7-5h12"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-7 5 8"/>',
  leaf: '<path d="M20 3C9 3 3 7 5 14s15 5 15-11ZM5 20l10-10"/>',
  alert: '<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3v.1"/>',
  package: '<path d="M3 7h18v14H3ZM2 3h20v4H2zm7 8h6"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  printer: '<path d="M6 8V3h12v5M6 17H3V8h18v9h-3M6 14h12v7H6m11-10h1"/>',
  shield: '<path d="m12 3 8 3v6c0 6-8 10-8 10S4 18 4 12V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  refresh: '<path d="M20 8a9 9 0 0 0-15-3L3 8m0-5v5h5m-4 8a9 9 0 0 0 15 3l2-3m0 5v-5h-5"/>',
};
const icon = (name, cls = '') => `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.box}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const number = value => Number(value || 0).toLocaleString('zh-CN');
const STATUS = {in_stock: '在库', in_use: '使用中', lent: '已借出', depleted: '已用完', discarded: '已丢弃', lost: '已遗失'};
const ACTION = {create: '登记物品', item_create: '登记物品', update: '编辑档案', item_update: '编辑档案', move: '移动位置', take_out: '取出使用', return: '归还入库', lend: '借出物品', consume: '消耗库存', restock: '补充库存', discard: '丢弃物品', lost: '标记遗失', location_create: '新增位置', location_update: '编辑位置', location_delete: '删除位置', search: '搜索物品'};
const LOCATION_TYPES = {home: '家庭', room: '房间', shelf: '货架 / 柜子', drawer: '抽屉', box: '收纳箱', other: '其他'};
const ROUTES = {dashboard: ['家庭概览', '好好收纳，也好好生活。', 'grid', '概览'], items: ['物品档案', '找得到，用得好。每件物品都有迹可循。', 'box', '物品'], locations: ['收纳空间', '从房间到收纳箱，把家的每个角落串起来。', 'pin', '位置'], expiry: ['到期提醒', '提前看一眼，少一点浪费，多一点安心。', 'clock', '到期'], history: ['操作记录', '每一次取放、移动与补充，都有记录。', 'history', '记录']};
const state = {route: 'dashboard', locations: [], categories: [], items: [], dashboard: null, logs: [], timezone: 'Asia/Shanghai', filters: {q: '', location_id: '', category: '', status: ''}, expiry: 'soon', selectedLocation: null, detail: null, authRequired: false, renderToken: 0};
const $ = id => document.getElementById(id);

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  let response;
  try { response = await fetch(path, {credentials: 'same-origin', ...options, headers}); }
  catch (_) { throw new Error('无法连接 NAS 服务，请检查网络或服务状态。'); }
  if (response.status === 401) showLogin();
  if (!response.ok) {
    let message = '请求未完成，请稍后重试。';
    try { const error = await response.json(); if (typeof error.detail === 'string') message = error.detail; } catch (_) { /* Non-JSON server error. */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}
const post = (path, body) => api(path, {method: 'POST', body: JSON.stringify(body)});
function toast(message, error = false) {
  const element = document.createElement('div');
  element.className = `toast${error ? ' toast-error' : ''}`;
  element.textContent = message;
  $('toasts').append(element);
  setTimeout(() => element.remove(), 4500);
}
function showError(error) { $('page-error').textContent = error.message || '暂时无法连接服务，请重试。'; $('page-error').hidden = false; }
function date(value, withTime = false) {
  if (!value) return '—';
  if (!withTime && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value.replace(/-/g, '.');
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', {timeZone: state.timezone, month: '2-digit', day: '2-digit', ...(withTime ? {hour: '2-digit', minute: '2-digit', hour12: false} : {year: 'numeric'})}).format(parsed);
}
function today() { return new Intl.DateTimeFormat('en-CA', {timeZone: state.timezone, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date()); }
function imageUrl(value) { return typeof value === 'string' && /^\/uploads\/[A-Za-z0-9._-]+$/.test(value) ? value : ''; }
function badge(status) { return `<span class="status-chip status-${esc(status)}">${esc(STATUS[status] || status)}</span>`; }
function expiryBadge(item) {
  if (item.expiry_status === 'expired') return `<span class="chip expiry-expired">${icon('alert')} 已过期 ${number(Math.abs(item.remaining_days))} 天</span>`;
  if (item.expiry_status === 'soon') return `<span class="chip expiry-soon">${icon('clock')} ${item.remaining_days === 0 ? '今日到期' : `${number(item.remaining_days)} 天后到期`}</span>`;
  if (item.expiry_status === 'safe') return `<span class="chip expiry-safe">${icon('check')} ${number(item.remaining_days)} 天后到期</span>`;
  return '';
}
function option(value, label, selected) { return `<option value="${esc(value)}"${String(selected ?? '') === String(value) ? ' selected' : ''}>${esc(label)}</option>`; }
function locationOptions(selected, includeEmpty = true) { return `${includeEmpty ? option('', '请选择收纳位置', selected) : ''}${state.locations.map(loc => option(loc.id, loc.path, selected)).join('')}`; }
function empty(title, subtitle, action = 'add-item', label = '登记第一件物品', symbol = 'box') { return `<div class="empty-state"><div class="empty-icon">${icon(symbol)}</div><h3>${esc(title)}</h3><p>${esc(subtitle)}</p>${action ? `<button class="button primary" data-action="${action}">${icon('plus')}${esc(label)}</button>` : ''}</div>`; }
function loading() { return `<div class="loading-state"><span class="spinner"></span><p>正在读取家庭档案…</p></div>`; }
function setNavigation() {
  const links = Object.entries(ROUTES).map(([key, info]) => `<a href="#${key}" class="nav-link${state.route === key ? ' active' : ''}"${state.route === key ? ' aria-current="page"' : ''}>${icon(info[2], 'nav-icon')}<span>${esc(info[0])}</span>${key === 'expiry' && state.dashboard && state.dashboard.soon_count + state.dashboard.expired_count > 0 ? `<span class="nav-count">${number(state.dashboard.soon_count + state.dashboard.expired_count)}</span>` : ''}</a>`).join('');
  $('desktop-nav').innerHTML = links;
  $('mobile-nav').innerHTML = Object.entries(ROUTES).map(([key, info]) => `<a href="#${key}" class="nav-link${state.route === key ? ' active' : ''}"${state.route === key ? ' aria-current="page"' : ''}>${icon(info[2])}<span>${info[3]}</span></a>`).join('');
  $('page-title').textContent = ROUTES[state.route][0];
  $('page-subtitle').textContent = ROUTES[state.route][1];
  $('page-eyebrow').textContent = state.route === 'dashboard' ? 'YOUR HOME, ORGANIZED' : 'A PLACE FOR EVERYTHING';
  $('export-button').innerHTML = `${icon('download')}<span>导出数据</span>`;
  $('add-item-button').innerHTML = `${icon('plus')}<span>登记物品</span>`;
  $('logout-button').innerHTML = icon('logout');
  $('logout-button').hidden = !state.authRequired;
}

const illustration = `<svg class="storage-illustration" viewBox="0 0 400 240" fill="none" aria-hidden="true"><ellipse cx="217" cy="218" rx="154" ry="9" fill="#bacac0" opacity=".23"/><path d="M119 64h216v153H119z" fill="#edf0e5"/><path d="M113 62h228v8H113zM113 130h228v7H113zM113 208h228v9H113z" fill="#718b78"/><path d="M121 70h7v138h-7zm204 0h7v138h-7z" fill="#809981"/><rect x="142" y="89" width="71" height="40" rx="4" fill="#d8c3a0"/><path d="M138 87h79v8h-79z" fill="#e8d8b9"/><rect x="164" y="101" width="24" height="11" rx="2" fill="#fbf7ed"/><path d="M171 106h10" stroke="#b1a087" stroke-width="2"/><rect x="230" y="96" width="72" height="33" rx="4" fill="#adc2ad"/><path d="M226 94h80v7h-80z" fill="#c6d3bc"/><rect x="253" y="107" width="25" height="10" rx="2" fill="#f7f9ef"/><path d="M261 112h10" stroke="#849b80" stroke-width="2"/><rect x="143" y="157" width="73" height="50" rx="4" fill="#b2c0b7"/><path d="M138 154h83v8h-83z" fill="#c8d2c9"/><path d="M168 174h23v10h-23z" fill="#f3f4ea"/><rect x="233" y="163" width="67" height="44" rx="4" fill="#d8bc8e"/><path d="M229 159h76v8h-76z" fill="#e5d0a9"/><rect x="256" y="178" width="21" height="10" rx="2" fill="#fbf5e6"/><path d="M87 177c-15-21-22-42-7-53 1 19 14 25 11 48 8-25 9-44 28-44 4 18-11 29-21 48" fill="#7c9b77"/><path d="M91 179v20" stroke="#667f63" stroke-width="3"/><path d="M70 192h41l-6 25H76z" fill="#d7b992"/><path d="m278 53 10-10 10 10" stroke="#99b195" stroke-width="2"/><circle cx="77" cy="70" r="6" fill="#d2bb88"/><path d="m356 152 5-5 5 5-5 5-5-5Z" fill="#b3c1ab"/><path d="M188 43v-7m-3 3h6" stroke="#acbda8" stroke-width="2"/></svg>`;
function logRow(log, compact = false) {
  const quantity = log.quantity_delta ? `<span class="log-quantity">${log.quantity_delta > 0 ? '+' : ''}${number(log.quantity_delta)}</span>` : '';
  const transition = log.from_location && log.to_location && log.from_location !== log.to_location ? `${log.from_location} → ${log.to_location}` : log.to_location || log.from_location || '';
  const details = [transition, log.note].filter(Boolean).join(' · ');
  return `<div class="${compact ? 'list-row' : 'log-row'}"><div class="list-icon">${icon(log.action.startsWith('location_') ? 'pin' : log.action === 'search' ? 'search' : 'box')}</div><div class="list-content"><div class="list-title">${log.item_id ? `<button class="text-link" data-action="detail" data-id="${log.item_id}">${esc(log.item_name || '物品')}</button>` : `<span>${esc(log.item_name || (log.action === 'search' ? '物品查询' : '收纳位置'))}</span>`}<span class="log-action">${esc(ACTION[log.action] || log.action)}</span>${quantity}</div>${details ? `<p class="list-subtitle">${esc(details)}</p>` : ''}</div><time class="log-time" datetime="${esc(log.created_at)}">${date(log.created_at, true)}</time></div>`;
}
function expiryRow(item) { return `<button class="list-row expiry-row" data-action="detail" data-id="${item.id}"><span class="list-icon ${item.expiry_status === 'expired' ? 'expiry-expired' : 'expiry-soon'}">${icon('clock')}</span><span class="list-content"><span class="list-title">${esc(item.name)}</span><span class="list-subtitle">${esc(item.location_path)}</span></span><span class="list-end">${expiryBadge(item)}<span class="expiry-date">${date(item.expires_on)}</span></span></button>`; }
function renderDashboard(data) {
  const metrics = [
    ['box', data.total_items, '物品种类', '每一件，都有记录', 'green'],
    ['pin', data.location_count, '收纳位置', '家的空间，一目了然', 'sand'],
    ['refresh', data.in_use_count, '正在使用', '取放状态，随时追踪', 'blue'],
    ['clock', data.soon_count + data.expired_count, '需要关注', data.expired_count ? `${data.expired_count} 件已过期` : '留意物品有效期', 'orange'],
  ];
  $('page-view').innerHTML = `<div class="hero-card"><div class="hero-copy"><span class="kicker">A LITTLE ORDER. A BETTER LIFE.</span><h2>每一件物品，<br>都有自己的位置。</h2><p>记录、寻找、取放。<br>把生活的琐碎，收纳成家的秩序。</p><div class="hero-tags"><span>${icon('shield')} 数据留在家中</span><span>${icon('history')} 每次取放可追溯</span></div></div><div class="hero-visual">${illustration}</div></div><div class="metric-grid">${metrics.map(([symbol, value, label, sub, color]) => `<div class="metric-card"><span class="metric-icon metric-${color}">${icon(symbol)}</span><span class="metric-value">${number(value)}<span>${symbol === 'pin' ? '处' : '种'}</span></span><span class="metric-label">${label}</span><span class="metric-trend">${esc(sub)}</span></div>`).join('')}</div><div class="dashboard-grid"><section class="panel"><div class="panel-header"><div><h2 class="panel-title">${icon('clock')} 到期提醒</h2><p class="panel-subtitle">留意保质期，让每一份物品物尽其用</p></div><a class="text-link" href="#expiry">查看全部 ${icon('arrow')}</a></div><div class="panel-body">${data.expiring_items.length ? data.expiring_items.slice(0, 4).map(expiryRow).join('') : empty('暂时没有到期提醒', '填写物品有效期，临近到期时会在这里提醒你。', null, '', 'leaf')}</div></section><section class="panel"><div class="panel-header"><div><h2 class="panel-title">${icon('history')} 最近动态</h2><p class="panel-subtitle">看看家里物品的新变化</p></div><a class="text-link" href="#history">全部记录 ${icon('arrow')}</a></div><div class="panel-body">${data.recent_logs.length ? data.recent_logs.slice(0, 5).map(log => logRow(log, true)).join('') : empty('你的家庭档案，从这里开始', '登记第一件物品，它的每一次变化都会被记住。')}</div></section></div>${data.total_items === 0 ? `<section class="getting-started"><div>${icon('pin')}<span><strong>给物品一个准确的地址</strong><span>先添加房间、柜子或收纳箱，再开始登记物品。</span></span></div><button class="button secondary" data-action="add-location">${icon('plus')} 添加收纳位置</button></section>` : ''}`;
}
function itemCard(item) {
  const photo = imageUrl(item.image_url);
  return `<button class="item-card" data-action="detail" data-id="${item.id}"><span class="item-photo">${photo ? `<img src="${esc(photo)}" alt="${esc(item.name)}的照片" loading="lazy">` : `<span class="item-photo-placeholder">${icon('box')}<span>未添加照片</span></span>`}<span class="item-photo-status">${badge(item.status)}</span></span><span class="item-body"><span class="item-meta">${esc(item.category)}</span><span class="item-title">${esc(item.name)}</span><span class="item-path">${icon('pin')}${esc(item.location_path)}</span>${expiryBadge(item)}<span class="item-bottom"><span class="item-quantity"><strong>${number(item.quantity)}</strong> ${esc(item.unit)}</span><span class="item-date">${date(item.stored_date)} 入库</span></span></span></button>`;
}
function itemFilters() {
  const f = state.filters;
  return `<div class="toolbar"><form class="search-form" id="search-form"><div class="search-wrap">${icon('search')}<input class="search-input" id="item-search" name="query" type="search" placeholder="搜索名称、品牌、型号或标签…" maxlength="200" aria-label="搜索物品" value="${esc(f.q)}"><button class="button subtle button-small" type="submit">搜索</button></div></form><div class="filter-row"><select class="form-control" data-filter="location_id" aria-label="筛选位置">${option('', '全部位置', f.location_id)}${locationOptions(f.location_id, false)}</select><select class="form-control" data-filter="category" aria-label="筛选分类">${option('', '全部分类', f.category)}${state.categories.map(cat => option(cat, cat, f.category)).join('')}</select><select class="form-control" data-filter="status" aria-label="筛选状态">${option('', '全部状态', f.status)}${Object.entries(STATUS).map(([val, name]) => option(val, name, f.status)).join('')}</select>${Object.values(f).some(Boolean) ? `<button class="button subtle button-small" data-action="clear-filters">清除筛选</button>` : ''}</div></div>`;
}
function renderItems(items) {
  const filtering = Object.values(state.filters).some(Boolean);
  $('page-view').innerHTML = `${itemFilters()}<div class="collection-heading"><span>共 <strong>${number(items.length)}</strong> 种物品</span><span class="muted">清晰的档案，安心的日常</span></div>${items.length ? `<div class="item-grid">${items.map(itemCard).join('')}</div>` : empty(filtering ? '没有找到匹配的物品' : '家里的宝贝，值得好好记住', filtering ? '换个关键词或清除筛选，再试一次。' : '从一件常用物品开始，拍张照，选个位置，建立你的家庭物品档案。', filtering ? 'clear-filters' : 'add-item', filtering ? '清除筛选' : '登记第一件物品')}`;
}
function renderExpiry(items) {
  $('page-view').innerHTML = `<div class="tabs" role="tablist" aria-label="到期筛选"><button class="tab${state.expiry === 'soon' ? ' active' : ''}" role="tab" aria-selected="${state.expiry === 'soon'}" data-action="expiry-tab" data-value="soon">${icon('clock')} 即将到期</button><button class="tab${state.expiry === 'expired' ? ' active' : ''}" role="tab" aria-selected="${state.expiry === 'expired'}" data-action="expiry-tab" data-value="expired">${icon('alert')} 已经过期</button></div><p class="expiry-explanation">${state.expiry === 'soon' ? '按每件物品设置的提前提醒天数显示，包含今天到期的物品。' : '有效期早于今天的物品。请及时检查并处理。'} · 日期按 ${esc(state.timezone)} 计算</p>${items.length ? `<div class="item-grid">${items.map(itemCard).join('')}</div>` : empty(state.expiry === 'soon' ? '最近没有物品即将到期' : '目前没有已过期的物品', '填写食品、药品等物品的有效期，我们会在这里帮你留意。', null, '', 'leaf')}`;
}
function locationBranch(parentId, depth = 0) {
  return state.locations.filter(loc => loc.parent_id === parentId).map(loc => `<div class="location-node"><button class="location-row${state.selectedLocation === loc.id ? ' active' : ''}" data-action="select-location" data-id="${loc.id}" style="--depth:${Math.min(depth, 12)}"><span class="location-icon">${icon(loc.type === 'home' ? 'home' : loc.type === 'room' ? 'pin' : 'package')}</span><span class="location-info"><strong>${esc(loc.name)}</strong><span>${esc(loc.code || LOCATION_TYPES[loc.type])}</span></span><span class="location-count">${number(loc.item_count)}</span>${icon('chevron')}</button>${locationBranch(loc.id, depth + 1)}</div>`).join('');
}
function renderLocations() {
  let loc = state.locations.find(value => value.id === state.selectedLocation) || state.locations[0];
  if (loc) state.selectedLocation = loc.id;
  $('page-view').innerHTML = `<div class="location-layout"><section class="panel location-tree"><div class="panel-header"><h2 class="panel-title">所有位置 <span class="chip">${state.locations.length}</span></h2><button class="icon-button" data-action="add-location" title="添加位置" aria-label="添加位置">${icon('plus')}</button></div><div class="tree-hint">房间 → 柜子 → 抽屉 → 收纳箱</div>${state.locations.length ? locationBranch(null) : empty('还没有收纳位置', '先为你的家建立一个位置。', 'add-location', '添加位置', 'pin')}</section><section class="panel location-detail">${loc ? `<div class="location-overview"><span class="location-detail-icon">${icon(loc.type === 'home' ? 'home' : 'package')}</span><span class="eyebrow">${esc(LOCATION_TYPES[loc.type])}</span><h2>${esc(loc.name)}</h2><p class="location-path">${esc(loc.path)}</p>${loc.code ? `<span class="location-code">位置编号 · ${esc(loc.code)}</span>` : ''}<div class="location-stat"><strong>${number(loc.item_count)}</strong><span>种物品存放于此位置及下级位置</span></div><div class="location-actions"><button class="button primary" data-action="view-location-items" data-id="${loc.id}">${icon('box')} 查看物品</button><button class="button secondary" data-action="qr" data-id="${loc.id}">${icon('qr')} 位置二维码</button></div></div><div class="location-detail-footer"><button class="button subtle" data-action="add-location" data-parent="${loc.id}">${icon('plus')} 添加下级位置</button><button class="button subtle" data-action="edit-location" data-id="${loc.id}">${icon('edit')} 编辑</button><button class="icon-button muted" data-action="delete-location" data-id="${loc.id}" title="删除位置" aria-label="删除位置">${icon('trash')}</button></div><p class="location-tip">${icon('qr')} 打印二维码贴在柜子或箱子上，扫码就能查看里面的物品。</p>` : empty('给物品一个位置', '位置可以层层关联，形成清晰的家庭收纳地图。', 'add-location', '添加位置', 'pin')}</section></div>`;
}
function renderHistory(logs) {
  $('page-view').innerHTML = `<div class="history-heading"><span>最近 <strong>${number(logs.length)}</strong> 条记录</span><span class="chip">${icon('shield')} 操作自动留痕</span></div><section class="panel log-table">${logs.length ? logs.map(log => logRow(log)).join('') : empty('尚无操作记录', '登记物品、取放或调整位置后，记录会自动出现在这里。', 'add-item', '登记第一件物品', 'history')}</section><p class="help-text history-note">展示最近 200 条操作。导出数据可获取完整记录。</p>`;
}
async function refreshPage(showLoading = true) {
  const token = ++state.renderToken;
  $('page-error').hidden = true;
  setNavigation();
  if (showLoading) $('page-view').innerHTML = loading();
  try {
    if (state.route === 'dashboard') { const data = await api('/api/dashboard'); if (token !== state.renderToken) return; state.dashboard = data; renderDashboard(data); setNavigation(); }
    else if (state.route === 'items') { const params = new URLSearchParams(Object.entries(state.filters).filter(([,value]) => value)); const items = await api(`/api/items?${params}`); if (token !== state.renderToken) return; state.items = items; renderItems(items); }
    else if (state.route === 'expiry') { const items = await api(`/api/items?expiry=${state.expiry}`); if (token !== state.renderToken) return; renderExpiry(items); }
    else if (state.route === 'locations') { state.locations = await api('/api/locations'); if (token !== state.renderToken) return; renderLocations(); }
    else if (state.route === 'history') { const logs = await api('/api/logs?limit=200'); if (token !== state.renderToken) return; renderHistory(logs); }
  } catch (error) { if (token !== state.renderToken) return; showError(error); $('page-view').innerHTML = empty('暂时无法读取数据', '请检查 NAS 服务是否运行，稍后重新连接。', 'retry', '重新连接', 'refresh'); }
}
function navigate(route) { if (location.hash === `#${route}`) { state.route = route; refreshPage(); } else location.hash = route; }
async function refreshAfterMutation() { state.locations = await api('/api/locations'); state.dashboard = await api('/api/dashboard'); await refreshPage(false); }
function modalHeader(title, id, subtitle = '') { return `<div class="modal-header"><div><h2 class="modal-title" id="${id}">${esc(title)}</h2>${subtitle ? `<p class="panel-subtitle">${esc(subtitle)}</p>` : ''}</div><button type="button" class="icon-button modal-close" data-action="close-modal" aria-label="关闭">${icon('close')}</button></div>`; }
function openModal(id, content) { const dialog = $(id); dialog.innerHTML = content; if (!dialog.open) dialog.showModal(); return dialog; }
function field(label, name, value = '', type = 'text', extra = '', wide = false) { return `<label class="form-field${wide ? ' span-2' : ''}"><span class="field-label">${esc(label)}</span><input class="form-control" name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`; }
function selectField(label, name, values, value = '', extra = '') { return `<label class="form-field"><span class="field-label">${esc(label)}</span><select class="form-control" name="${name}" ${extra}>${values.map(([key, text]) => option(key, text, value)).join('')}</select></label>`; }

async function openItemForm(id = null) {
  try {
    const item = id ? await api(`/api/items/${id}`) : {quantity: 1, unit: '件', warning_days: 7, category: '其他', stored_date: today(), location_id: state.filters.location_id || state.selectedLocation || state.locations[0]?.id || ''};
    const photo = imageUrl(item.image_url);
    openModal('item-modal', `<form id="item-form" data-id="${id || ''}" data-image="${esc(item.image_url || '')}">${modalHeader(id ? '编辑物品档案' : '登记一件物品', 'item-modal-title', id ? '库存和位置变化请在物品详情中操作，会自动留痕。' : '拍张照、选个位置，让物品有迹可循。')}<div class="modal-body"><div class="image-upload"><div class="image-preview" id="image-preview">${photo ? `<img src="${esc(photo)}" alt="物品照片">` : icon('image')}</div><div><label class="button secondary button-small" for="item-photo">${icon('image')} ${photo ? '更换照片' : '添加照片'}</label><input id="item-photo" type="file" name="photo" accept="image/jpeg,image/png,image/webp" class="screen-reader"><p class="help-text">JPG、PNG 或 WebP，最大 10 MB</p>${photo ? '<button type="button" class="text-link" data-action="remove-photo">移除照片</button>' : ''}</div></div><div class="form-grid">${field('物品名称 *', 'name', item.name, 'text', 'required maxlength="200" placeholder="例如：便携电动打气筒"', true)}${selectField('物品分类', 'category', state.categories.map(cat => [cat, cat]), item.category)}${id ? `<div class="form-field"><span class="field-label">当前收纳位置</span><div class="form-control static-field">${esc(item.location_path)}</div></div>` : `<label class="form-field"><span class="field-label">收纳位置 *</span><select name="location_id" class="form-control" required>${locationOptions(item.location_id)}</select></label>`}${id ? `<div class="form-field"><span class="field-label">当前库存</span><div class="form-control static-field">${number(item.quantity)} ${esc(item.unit)} · ${esc(STATUS[item.status])}</div></div>` : field('数量 *', 'quantity', item.quantity, 'number', 'required min="0" max="1000000000" step="1"')}${field('计量单位', 'unit', item.unit, 'text', 'required maxlength="30" placeholder="件、包、瓶…"')}${field('品牌', 'brand', item.brand, 'text', 'maxlength="120"')}${field('型号 / 规格', 'model', item.model, 'text', 'maxlength="120"')}${field('入库日期 *', 'stored_date', item.stored_date, 'date', 'required')}${field('购买日期', 'purchase_date', item.purchase_date, 'date')}${field('购买价格（元）', 'purchase_price', item.purchase_price ?? '', 'number', 'min="0" step="0.01"')}${field('有效期至', 'expires_on', item.expires_on, 'date')}${field('提前提醒（天）', 'warning_days', item.warning_days, 'number', 'required min="0" max="3650" step="1"')}${field('标签', 'tags', item.tags, 'text', 'maxlength="500" placeholder="用逗号分隔，例如：露营,常用"')}<label class="form-field span-2"><span class="field-label">备注</span><textarea class="form-control" name="notes" rows="3" maxlength="4000" placeholder="配件、注意事项，或你想记住的小细节…">${esc(item.notes)}</textarea></label></div><p class="error-banner form-error" hidden role="alert"></p></div><div class="modal-footer"><button type="button" class="button secondary" data-action="close-modal">取消</button><button type="submit" class="button primary">${icon('check')} ${id ? '保存档案' : '登记物品'}</button></div></form>`);
  } catch (error) { toast(error.message, true); }
}
async function submitItem(form) {
  const data = new FormData(form);
  const payload = {};
  ['name', 'category', 'unit', 'brand', 'model', 'tags', 'notes', 'stored_date'].forEach(key => payload[key] = String(data.get(key) || '').trim());
  ['purchase_date', 'expires_on'].forEach(key => payload[key] = data.get(key) || null);
  payload.purchase_price = data.get('purchase_price') === '' ? null : Number(data.get('purchase_price'));
  payload.warning_days = Number(data.get('warning_days'));
  payload.image_url = form.dataset.image || null;
  const photo = data.get('photo');
  if (photo && photo.size) {
    if (photo.size > 10 * 1024 * 1024) throw new Error('照片超过 10 MB，请选择较小的文件。');
    const upload = new FormData(); upload.append('file', photo);
    payload.image_url = (await api('/api/uploads', {method: 'POST', body: upload})).image_url;
    form.dataset.image = payload.image_url;
    form.querySelector('[name="photo"]').value = '';
  }
  const id = form.dataset.id;
  if (!id) { payload.location_id = Number(data.get('location_id')); payload.quantity = Number(data.get('quantity')); }
  const saved = await api(id ? `/api/items/${id}` : '/api/items', {method: id ? 'PATCH' : 'POST', body: JSON.stringify(payload)});
  $('item-modal').close(); toast(id ? '物品档案已更新。' : '物品已登记，家的秩序又多了一点。');
  await refreshAfterMutation();
  if ($('detail-modal').open) await openDetail(saved.id);
}
function datum(label, value) { return `<div class="detail-datum"><span class="detail-label">${esc(label)}</span><span class="detail-value">${esc(value || '—')}</span></div>`; }
async function openDetail(id) {
  try {
    const [item, logs] = await Promise.all([api(`/api/items/${id}`), api(`/api/logs?item_id=${id}&limit=50`)]);
    state.detail = item;
    const actions = {in_stock: ['take_out', 'move', 'lend', 'consume', 'restock', 'discard', 'lost'], in_use: ['return', 'lend', 'discard', 'lost'], lent: ['return', 'discard', 'lost'], depleted: ['restock'], discarded: [], lost: []}[item.status] || [];
    const photo = imageUrl(item.image_url);
    openModal('detail-modal', `${modalHeader('物品详情', 'detail-modal-title')}<div class="modal-body"><div class="detail-hero"><div class="detail-image">${photo ? `<img src="${esc(photo)}" alt="${esc(item.name)}的照片">` : icon('box')}</div><div class="detail-info"><span class="eyebrow">${esc(item.category)}</span><h3 class="detail-title">${esc(item.name)}</h3><div class="detail-badges">${badge(item.status)}${expiryBadge(item)}</div><p class="item-path">${icon('pin')}${esc(item.location_path)}</p><div class="detail-quantity"><strong>${number(item.quantity)}</strong> ${esc(item.unit)}</div></div></div><div class="detail-grid">${datum('品牌 / 型号', [item.brand, item.model].filter(Boolean).join(' / '))}${datum('入库日期', date(item.stored_date))}${datum('购买日期', date(item.purchase_date))}${datum('购买价格', item.purchase_price == null ? '' : `¥ ${number(item.purchase_price)}`)}${datum('有效期至', date(item.expires_on))}${datum('提前提醒', `${item.warning_days} 天`)}${datum('标签', item.tags)}${datum('档案更新', date(item.updated_at, true))}</div>${item.notes ? `<div class="detail-notes"><span class="detail-label">备注</span><p>${esc(item.notes)}</p></div>` : ''}${actions.length ? `<h3 class="section-title">物品操作</h3><div class="action-grid">${actions.map(action => `<button class="button ${['discard', 'lost'].includes(action) ? 'subtle action-muted' : 'secondary'}" data-action="item-action" data-value="${action}" data-id="${item.id}">${icon(action === 'move' ? 'pin' : action === 'discard' ? 'trash' : action === 'restock' ? 'plus' : action === 'lost' ? 'alert' : 'refresh')}${esc(ACTION[action])}</button>`).join('')}</div><p class="help-text">取出、归还与借出会更新整件物品的状态；数量变化请使用消耗或补充。</p>` : `<p class="help-text">此物品已归档，操作记录仍然保留。</p>`}<h3 class="section-title">操作记录</h3><div class="detail-logs">${logs.length ? logs.map(log => logRow(log, true)).join('') : '<p class="muted">暂无记录</p>'}</div></div><div class="modal-footer"><button class="button secondary" data-action="close-modal">关闭</button><button class="button primary" data-action="edit-item" data-id="${item.id}">${icon('edit')} 编辑档案</button></div>`);
  } catch (error) { toast(error.message, true); }
}
async function openAction(id, action) {
  try {
    const item = state.detail?.id === id ? state.detail : await api(`/api/items/${id}`);
    const description = {take_out: '将整件物品标记为正在使用，保留原来的收纳位置。', return: '将物品重新标记为在库，可同时调整归还位置。', move: '将物品移动到新的收纳位置。', lend: '将整件物品标记为已借出。请在备注中记录借用人。', consume: '减少当前库存数量，用完后自动标记为已用完。', restock: '增加库存数量，已用完的物品将重新标记为在库。', discard: '将物品标记为已丢弃，库存清零。档案和历史记录仍然保留。', lost: '将物品标记为已遗失，从在用库存与到期提醒中移出。'}[action];
    const danger = ['discard', 'lost'].includes(action);
    openModal('action-modal', `<form id="action-form" data-id="${id}" data-value="${action}">${modalHeader(ACTION[action], 'action-modal-title', item.name)}<div class="modal-body"><p class="action-description">${esc(description)}</p><div class="action-current">${icon('box')} 当前：${number(item.quantity)} ${esc(item.unit)} · ${esc(STATUS[item.status])}</div>${['move', 'return'].includes(action) ? `<label class="form-field"><span class="field-label">${action === 'move' ? '目标位置 *' : '归还位置'}</span><select name="location_id" class="form-control" ${action === 'move' ? 'required' : ''}>${action === 'return' ? option('', `原位置：${item.location_path}`, '') : option('', '请选择目标位置', '')}${locationOptions('', false)}</select></label>` : ''}${['consume', 'restock'].includes(action) ? field(`${action === 'consume' ? '消耗' : '补充'}数量（${item.unit}） *`, 'quantity', 1, 'number', `required min="1" step="1" max="${action === 'consume' ? item.quantity : 1000000000}"`) : ''}<label class="form-field"><span class="field-label">${action === 'lend' ? '借用人 / 备注 *' : '操作备注'}</span><textarea class="form-control" name="note" rows="3" maxlength="2000" placeholder="${action === 'lend' ? '例如：借给小李，预计周末归还' : '记录这次操作的原因或细节…'}" ${action === 'lend' ? 'required' : ''}></textarea></label><p class="help-text">确认后会自动保存一条操作记录。</p><p class="error-banner form-error" hidden role="alert"></p></div><div class="modal-footer"><button type="button" class="button secondary" data-action="close-modal">取消</button><button type="submit" class="button ${danger ? 'danger' : 'primary'}">确认${esc(ACTION[action])}</button></div></form>`);
  } catch (error) { toast(error.message, true); }
}
async function submitAction(form) {
  const data = new FormData(form); const payload = {action: form.dataset.value, note: String(data.get('note') || '').trim()};
  if (data.get('location_id')) payload.location_id = Number(data.get('location_id'));
  if (data.has('quantity')) payload.quantity = Number(data.get('quantity'));
  const item = await post(`/api/items/${form.dataset.id}/actions`, payload);
  $('action-modal').close(); toast(`${ACTION[payload.action]}已记录。`);
  await refreshAfterMutation();
  if ($('detail-modal').open) await openDetail(item.id);
}
function openLocationForm(id = null, parent = null) {
  const loc = state.locations.find(value => value.id === id) || {name: '', code: '', type: parent ? 'box' : 'room', parent_id: parent || state.locations.find(value => value.type === 'home')?.id || ''};
  const descendants = new Set(id ? [id] : []);
  let previous;
  do { previous = descendants.size; state.locations.forEach(value => { if (descendants.has(value.parent_id)) descendants.add(value.id); }); } while (descendants.size !== previous);
  openModal('location-modal', `<form id="location-form" data-id="${id || ''}">${modalHeader(id ? '编辑收纳位置' : '添加收纳位置', 'location-modal-title', '从房间、柜子到收纳箱，建立清晰的位置层级。')}<div class="modal-body"><div class="form-grid">${field('位置名称 *', 'name', loc.name, 'text', 'required maxlength="120" placeholder="例如：书房工具柜"', true)}${field('位置编号', 'code', loc.code, 'text', 'maxlength="80" placeholder="例如：S-01"')}${selectField('位置类型', 'type', Object.entries(LOCATION_TYPES), loc.type)}<label class="form-field span-2"><span class="field-label">上级位置</span><select name="parent_id" class="form-control">${option('', '无上级位置（根位置）', loc.parent_id)}${state.locations.filter(value => !descendants.has(value.id)).map(value => option(value.id, value.path, loc.parent_id)).join('')}</select><span class="help-text">例如把“工具箱”放在“书房 / 工具柜”下面。</span></label></div><p class="error-banner form-error" hidden role="alert"></p></div><div class="modal-footer"><button type="button" class="button secondary" data-action="close-modal">取消</button><button type="submit" class="button primary">${icon('check')} 保存位置</button></div></form>`);
}
async function submitLocation(form) {
  const data = new FormData(form); const payload = {name: data.get('name').trim(), code: data.get('code').trim(), type: data.get('type'), parent_id: data.get('parent_id') ? Number(data.get('parent_id')) : null};
  const saved = await api(form.dataset.id ? `/api/locations/${form.dataset.id}` : '/api/locations', {method: form.dataset.id ? 'PATCH' : 'POST', body: JSON.stringify(payload)});
  $('location-modal').close(); state.selectedLocation = saved.id; toast('收纳位置已保存。'); await refreshAfterMutation();
}
function confirmDeleteLocation(id) {
  const loc = state.locations.find(value => value.id === id);
  if (!loc) return;
  openModal('action-modal', `<form id="delete-location-form" data-id="${id}">${modalHeader('删除收纳位置', 'action-modal-title')}<div class="modal-body"><p>确认删除“${esc(loc.path)}”？</p><p class="help-text">有下级位置或关联物品的位置不能删除，历史记录会保留。</p><p class="error-banner form-error" hidden role="alert"></p></div><div class="modal-footer"><button type="button" class="button secondary" data-action="close-modal">取消</button><button class="button danger" type="submit">确认删除</button></div></form>`);
}
async function openQr(id) {
  const loc = state.locations.find(value => value.id === id); if (!loc) return;
  const src = `/api/locations/${id}/qr?base_url=${encodeURIComponent(location.origin)}`;
  openModal('qr-modal', `${modalHeader('位置二维码', 'qr-modal-title', '贴在柜子或收纳箱上，扫码快速查看物品。')}<div class="modal-body qr-print-content"><div class="qr-card"><p class="eyebrow">HOME STORAGE</p><h3>${esc(loc.name)}</h3><p>${esc(loc.path)}</p><img class="qr-image" src="${src}" width="240" height="240" alt="${esc(loc.name)}位置二维码"><span class="location-code">${loc.code ? esc(loc.code) : `位置 #${loc.id}`}</span><p class="help-text">扫码查看此位置及下级位置中的物品</p></div><p class="help-text qr-network-note">二维码使用当前地址 ${esc(location.origin)}。扫码设备需能访问这个 NAS 地址。</p></div><div class="modal-footer"><button class="button secondary" data-action="close-modal">关闭</button><button class="button primary" data-action="print-qr">${icon('printer')} 打印标签</button></div>`);
}
function showLogin() { document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close()); $('app-shell').hidden = true; $('login-view').hidden = false; document.querySelector('#login-form input').focus(); }
async function bootstrap() {
  try {
    const auth = await api('/api/auth'); state.authRequired = auth.auth_required;
    if (!auth.authenticated) { showLogin(); return; }
    $('app-shell').hidden = false; $('login-view').hidden = true;
    const [meta, locations] = await Promise.all([api('/api/meta'), api('/api/locations')]);
    state.categories = meta.categories; state.timezone = meta.timezone; state.locations = locations;
    const deepLink = new URLSearchParams(location.search).get('location');
    if (deepLink && /^\d+$/.test(deepLink)) { state.filters.location_id = deepLink; state.selectedLocation = Number(deepLink); state.route = 'items'; history.replaceState(null, '', `${location.pathname}?location=${encodeURIComponent(deepLink)}#items`); }
    else state.route = ROUTES[location.hash.slice(1)] ? location.hash.slice(1) : 'dashboard';
    await refreshPage();
  } catch (error) { showError(error); setNavigation(); $('page-view').innerHTML = empty('暂时无法连接家庭档案', '请检查服务是否启动，再重新连接。', 'retry', '重新连接', 'refresh'); }
}
window.addEventListener('hashchange', () => { state.route = ROUTES[location.hash.slice(1)] ? location.hash.slice(1) : 'dashboard'; refreshPage(); });
document.addEventListener('click', async event => {
  const target = event.target.closest('[data-action]'); if (!target || target.disabled) return;
  const action = target.dataset.action; const id = Number(target.dataset.id);
  if (action === 'close-modal') { target.closest('dialog')?.close(); return; }
  if (action === 'add-item') { openItemForm(); return; }
  if (action === 'edit-item') { openItemForm(id); return; }
  if (action === 'detail') { openDetail(id); return; }
  if (action === 'item-action') { openAction(id, target.dataset.value); return; }
  if (action === 'add-location') { openLocationForm(null, Number(target.dataset.parent) || null); return; }
  if (action === 'edit-location') { openLocationForm(id); return; }
  if (action === 'delete-location') { confirmDeleteLocation(id); return; }
  if (action === 'select-location') { state.selectedLocation = id; renderLocations(); return; }
  if (action === 'view-location-items') { state.filters = {q: '', category: '', status: '', location_id: String(id)}; navigate('items'); return; }
  if (action === 'qr') { openQr(id); return; }
  if (action === 'print-qr') { window.print(); return; }
  if (action === 'expiry-tab') { state.expiry = target.dataset.value; refreshPage(); return; }
  if (action === 'clear-filters') { state.filters = {q: '', category: '', status: '', location_id: ''}; history.replaceState(null, '', `${location.pathname}${location.hash}`); refreshPage(); return; }
  if (action === 'retry') { bootstrap(); return; }
  if (action === 'remove-photo') { const form = target.closest('form'); form.dataset.image = ''; form.querySelector('[name="photo"]').value = ''; $('image-preview').innerHTML = icon('image'); target.remove(); return; }
  if (action === 'export') {
    target.disabled = true;
    try { const response = await fetch('/api/export', {credentials: 'same-origin'}); if (response.status === 401) { showLogin(); throw new Error('请登录后导出数据。'); } if (!response.ok) throw new Error('导出失败，请稍后重试。'); const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `homestorage-${today()}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast('数据已导出。照片请随 NAS 数据目录一起备份。'); } catch (error) { toast(error.message, true); } finally { target.disabled = false; }
    return;
  }
  if (action === 'logout') { try { await post('/api/logout', {}); document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close()); showLogin(); $('login-form').reset(); } catch (error) { toast(error.message, true); } }
});
document.addEventListener('change', event => {
  const target = event.target;
  if (target.dataset.filter) { state.filters[target.dataset.filter] = target.value; refreshPage(); }
  if (target.id === 'item-photo' && target.files[0]) {
    const file = target.files[0];
    if (file.size > 10 * 1024 * 1024) { target.value = ''; toast('照片超过 10 MB，请选择较小的文件。', true); return; }
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) { target.value = ''; toast('请选择 JPG、PNG 或 WebP 照片。', true); return; }
    const objectUrl = URL.createObjectURL(file); const img = document.createElement('img'); img.alt = '待上传的物品照片'; img.src = objectUrl; img.onload = () => URL.revokeObjectURL(objectUrl); $('image-preview').replaceChildren(img);
  }
});
document.addEventListener('submit', async event => {
  const form = event.target; if (!(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  if (form.id === 'search-form') {
    const query = String(new FormData(form).get('query') || '').trim(); state.filters.q = query;
    try { if (query) await post('/api/search', {query}); await refreshPage(); } catch (error) { toast(error.message, true); }
    return;
  }
  if (form.dataset.saving === 'true') return;
  const button = form.querySelector('[type="submit"]'); const errorElement = form.querySelector('.form-error') || $('login-error');
  form.dataset.saving = 'true'; if (button) button.disabled = true; if (errorElement) errorElement.hidden = true;
  try {
    if (form.id === 'login-form') { await post('/api/login', {password: new FormData(form).get('password')}); form.reset(); await bootstrap(); }
    else if (form.id === 'item-form') await submitItem(form);
    else if (form.id === 'action-form') await submitAction(form);
    else if (form.id === 'location-form') await submitLocation(form);
    else if (form.id === 'delete-location-form') { await api(`/api/locations/${form.dataset.id}`, {method: 'DELETE'}); $('action-modal').close(); state.selectedLocation = null; toast('收纳位置已删除。'); await refreshAfterMutation(); }
  } catch (error) { if (errorElement) { errorElement.textContent = error.message; errorElement.hidden = false; } else toast(error.message, true); }
  finally { form.dataset.saving = 'false'; if (button) button.disabled = false; }
});
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } }));
let refreshingReminders = false;
async function refreshReminders() {
  if (refreshingReminders || document.visibilityState !== 'visible' || $('app-shell').hidden ||
      !['dashboard', 'expiry'].includes(state.route) || document.querySelector('dialog[open]')) return;
  refreshingReminders = true;
  try { await refreshPage(false); }
  finally { refreshingReminders = false; }
}
// Recalculate visible reminders across midnight without disrupting an open form.
setInterval(refreshReminders, 60000);
document.addEventListener('visibilitychange', refreshReminders);
bootstrap();
