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
  chat: '<path d="M3 3h18v14H8l-5 4V3Z"/><path d="M7 7h10M7 11h7"/>',
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
  tags: '<path d="M3 3h8l10 10-8 8L3 11V3Z"/><circle cx="7" cy="7" r="1"/>',
  refresh: '<path d="M20 8a9 9 0 0 0-15-3L3 8m0-5v5h5m-4 8a9 9 0 0 0 15 3l2-3m0 5v-5h-5"/>',
  settings: '<path d="m9 3-1 3-3 1-2 3 2 2v3l3 2 1 4h6l1-4 3-2v-3l2-2-2-3-3-1-1-3H9Z"/><circle cx="12" cy="12" r="3"/>',
  list: '<path d="M9 5h12M9 12h12M9 19h12"/><circle cx="4" cy="5" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="19" r="1"/>',
};
const icon = (name, cls = '') => `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.box}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const number = value => Number(value || 0).toLocaleString('zh-CN');
const STATUS = {in_stock: '在库', in_use: '使用中', lent: '已借出', depleted: '已用完', discarded: '已丢弃', lost: '已遗失'};
const ACTION = {create: '登记物品', item_create: '登记物品', update: '编辑档案', item_update: '编辑档案', move: '移动位置', take_out: '取出使用', return: '归还入库', lend: '借出物品', consume: '消耗库存', restock: '补充库存', discard: '丢弃物品', lost: '标记遗失', location_create: '新增位置', location_update: '编辑位置', location_delete: '删除位置', search: '搜索物品', archive: '归档物品', restore: '恢复物品', category_create: '新增分类', category_update: '编辑分类', category_delete: '删除分类'};
const LOCATION_TYPES = {home: '家庭', room: '房间', shelf: '货架 / 柜子', drawer: '抽屉', box: '收纳箱', other: '其他'};
const ROUTES = {dashboard: ['家庭概览', '好好收纳，也好好生活。', 'grid', '概览'], items: ['物品档案', '找得到，用得好。每件物品都有迹可循。', 'box', '物品'], locations: ['收纳空间', '从房间到收纳箱，把家的每个角落串起来。', 'pin', '位置'], expiry: ['到期提醒', '提前看一眼，少一点浪费，多一点安心。', 'clock', '到期'], categories: ['分类管理', '按家里的习惯分类，让每一件物品都好找。', 'tags', '分类'], history: ['操作记录', '每一次取放、移动与补充，都有记录。', 'history', '记录'], settings: ['应用设置', '配置收纳助手，选择程序更新时间。', 'settings', '设置']};
const state = {route: 'dashboard', locations: [], categories: [], items: [], dashboard: null, logs: [], timezone: 'Asia/Shanghai', appVersion: '', updates: {status: null, preference: null, busy: false, polling: false, generation: 0, sequence: 0, inFlight: false, offline: false, disconnectedSince: null, reauthenticate: false, error: '', savedMessage: '', needsReload: false}, ai: {status: null, messages: [], input: '', generation: 0, busy: false, recording: false, transcribing: false}, filters: {q: '', location_id: '', category: '', category_id: '', status: '', archived: 'false'}, expiry: 'soon', selectedLocation: null, detail: null, authRequired: false, renderToken: 0};
const $ = id => document.getElementById(id);
state.inventory = {view: 'grid', sort: 'recent', visible: 48};
try {
  const preference = JSON.parse(localStorage.getItem('homestorage.inventory-view') || 'null');
  if (['grid', 'list'].includes(preference?.view)) state.inventory.view = preference.view;
  if (['recent', 'name', 'quantity', 'expiry'].includes(preference?.sort)) state.inventory.sort = preference.sort;
} catch (_) { /* View preferences are optional when browser storage is unavailable. */ }
function rememberInventoryView() {
  try { localStorage.setItem('homestorage.inventory-view', JSON.stringify({view: state.inventory.view, sort: state.inventory.sort})); }
  catch (_) { /* Inventory remains usable in private or restricted browsers. */ }
}
state.aiSettings = {status: null, loading: false, busy: false, forbidden: false, generation: 0, sequence: 0, error: '', message: ''};
// Retain the live form when navigating; keys exist only in password inputs.
let aiSettingsPanel = null;

// Motion is decorative: the stylesheet always contains the final, visible state.
// Keep only live animations so replacing a view or changing system preferences
// cannot leave hidden content or retain detached cards.
const motionPreference = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
const activeMotion = new Map();
function cancelMotion(container = null) {
  for (const [animation, element] of activeMotion) {
    if (!container || element === container || container.contains(element)) {
      activeMotion.delete(animation);
      animation.cancel();
    }
  }
}
function enterMotion(element, {delay = 0, modal = false, compact = false} = {}) {
  if (!element?.animate || motionPreference?.matches || document.visibilityState === 'hidden') return;
  cancelMotion(element);
  try {
    const animation = element.animate([
      {opacity: 0, transform: modal ? 'translateY(14px) scale(.985)' : `translateY(${compact ? 6 : 12}px)`},
      {opacity: 1, transform: modal ? 'translateY(0) scale(1)' : 'translateY(0)'},
    ], {duration: compact ? 240 : modal ? 300 : 360, delay, easing: 'cubic-bezier(.22, 1, .36, 1)', fill: 'backwards'});
    activeMotion.set(animation, element);
    const release = () => activeMotion.delete(animation);
    animation.finished.then(release, release);
  } catch (_) { /* Older browsers keep the same fully usable, static interface. */ }
}
function enterCollection(elements, compact = false) {
  [...elements].slice(0, compact ? 8 : 6).forEach((element, index) => enterMotion(element, {delay: index * (compact ? 18 : 24), compact}));
}
function motionPreferenceChanged() { if (motionPreference?.matches) cancelMotion(); }
if (motionPreference?.addEventListener) motionPreference.addEventListener('change', motionPreferenceChanged);
else motionPreference?.addListener?.(motionPreferenceChanged);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') cancelMotion(); });

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
  const container = $('toasts');
  for (const previous of [...container.children]) {
    if (previous.textContent === element.textContent && previous.className === element.className) previous.remove();
  }
  container.append(element);
  const maximum = window.matchMedia('(max-width: 760px)').matches ? 2 : 3;
  while (container.children.length > maximum) container.firstElementChild.remove();
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
function timezoneLabel() { return state.timezone === 'Asia/Shanghai' ? '北京时间' : new Intl.DateTimeFormat('zh-CN', {timeZone: state.timezone, timeZoneName: 'long'}).formatToParts(new Date()).find(part => part.type === 'timeZoneName')?.value || state.timezone; }
function imageUrl(value) { return typeof value === 'string' && /^\/uploads\/[A-Za-z0-9._-]+$/.test(value) ? value : ''; }
function itemVisual(item, caption = false) {
  const photo = imageUrl(item.image_url);
  return `<span class="item-visual">${ItemVisuals.render(item, caption)}${photo ? `<img data-item-photo src="${esc(photo)}" alt="${esc(item.name || '物品')}的照片" loading="lazy" decoding="async">` : ''}</span>`;
}
function itemFormVisual(form) {
  const name = form.elements.name.value;
  return {name, tags: name.trim() ? form.elements.tags.value : '', category: name.trim() ? state.categories.find(category => String(category.id) === form.elements.category_id.value)?.name || '' : '', image_url: form.dataset.image};
}
let itemPhotoPreviewUrl = null;
function releaseItemPhotoPreview() {
  if (itemPhotoPreviewUrl) URL.revokeObjectURL(itemPhotoPreviewUrl);
  itemPhotoPreviewUrl = null;
}
function resetItemPhotoPreview(form) {
  releaseItemPhotoPreview();
  $('image-preview').innerHTML = itemVisual(itemFormVisual(form));
  photoControls(form, Boolean(imageUrl(form.dataset.image)));
}
function refreshItemIcon(form) {
  const art = form.querySelector('#image-preview .item-art');
  if (art) art.outerHTML = ItemVisuals.render(itemFormVisual(form));
}
function photoControls(form, hasPhoto) {
  form.querySelector('label[for="item-photo"]').innerHTML = `${icon('image')} ${hasPhoto ? '更换照片' : '添加照片'}`;
  form.querySelector('[data-action="remove-photo"]').hidden = !hasPhoto;
}
// Image load errors do not bubble. Keep the local illustration underneath each photo
// and hide only the failed image, without changing the saved item or retrying requests.
document.addEventListener('error', event => {
  if (event.target instanceof HTMLImageElement && event.target.hasAttribute('data-item-photo')) event.target.hidden = true;
}, true);

function badge(status) { return `<span class="status-chip status-${esc(status)}">${esc(STATUS[status] || status)}</span>`; }
function archived(item) { return Boolean(item.archived_at); }
function archiveBadge(item) { return archived(item) ? '<span class="status-chip status-archived">已归档</span>' : ''; }
function expiryBadge(item) {
  if (archived(item)) return '';
  if (item.expiry_status === 'expired') return `<span class="chip expiry-expired">${icon('alert')} 已过期 ${number(Math.abs(item.remaining_days))} 天</span>`;
  if (item.expiry_status === 'soon') return `<span class="chip expiry-soon">${icon('clock')} ${item.remaining_days === 0 ? '今日到期' : `${number(item.remaining_days)} 天后到期`}</span>`;
  if (item.expiry_status === 'safe') return `<span class="chip expiry-safe">${icon('check')} ${number(item.remaining_days)} 天后到期</span>`;
  return '';
}
function option(value, label, selected) { return `<option value="${esc(value)}"${String(selected ?? '') === String(value) ? ' selected' : ''}>${esc(label)}</option>`; }
function categoryOptions(selected) { return state.categories.filter(cat => cat.active || String(cat.id) === String(selected)).map(cat => option(cat.id, `${cat.name}${cat.active ? '' : '（已停用）'}`, selected)).join(''); }
function hasFilters() { return Object.entries(state.filters).some(([key, value]) => key !== 'archived' ? Boolean(value) : value !== 'false'); }
function locationOptions(selected, includeEmpty = true) { return `${includeEmpty ? option('', '请选择收纳位置', selected) : ''}${state.locations.map(loc => option(loc.id, loc.path, selected)).join('')}`; }
function empty(title, subtitle, action = 'add-item', label = '登记第一件物品', symbol = 'box') { return `<div class="empty-state"><div class="empty-icon">${icon(symbol)}</div><h3>${esc(title)}</h3><p>${esc(subtitle)}</p>${action ? `<button class="button primary" data-action="${action}">${icon(['retry', 'clear-filters'].includes(action) ? 'refresh' : 'plus')}${esc(label)}</button>` : ''}</div>`; }
function loading() { return `<div class="loading-state" role="status"><span class="spinner" aria-hidden="true"></span><p>正在读取家庭档案…</p></div>`; }
function setNavigation() {
  const links = Object.entries(ROUTES).map(([key, info]) => `<a href="#${key}" class="nav-link${state.route === key ? ' active' : ''}"${state.route === key ? ' aria-current="page"' : ''}>${icon(info[2], 'nav-icon')}<span>${esc(info[0])}</span>${key === 'expiry' && state.dashboard && state.dashboard.soon_count + state.dashboard.expired_count > 0 ? `<span class="nav-count">${number(state.dashboard.soon_count + state.dashboard.expired_count)}</span>` : ''}</a>`).join('');
  $('desktop-nav').innerHTML = links;
  $('mobile-nav').innerHTML = Object.entries(ROUTES).map(([key, info]) => `<a href="#${key}" class="nav-link${state.route === key ? ' active' : ''}"${state.route === key ? ' aria-current="page"' : ''}>${icon(info[2])}<span>${info[3]}</span></a>`).join('');
  $('page-title').textContent = ROUTES[state.route][0];
  $('page-subtitle').textContent = ROUTES[state.route][1];
  $('page-eyebrow').textContent = state.route === 'dashboard' ? 'YOUR HOME, ORGANIZED' : 'A PLACE FOR EVERYTHING';
  $('export-button').innerHTML = `${icon('download')}<span>导出数据</span>`;
  $('add-item-button').innerHTML = `${icon('plus')}<span>登记物品</span>`;
  $('add-item-button').hidden = state.route === 'settings';
  $('logout-button').innerHTML = icon('logout');
  $('logout-button').hidden = !state.authRequired;
  $('mobile-logout-button').innerHTML = icon('logout');
  $('mobile-logout-button').hidden = !state.authRequired;
  renderUpdateNotice();
}

const illustration = `<svg class="storage-illustration" viewBox="0 0 400 240" fill="none" aria-hidden="true"><ellipse cx="217" cy="218" rx="154" ry="9" fill="#aeb9c9" opacity=".23"/><path d="M119 64h216v153H119z" fill="#e9edf3"/><path d="M113 62h228v8H113zM113 130h228v7H113zM113 208h228v9H113z" fill="#8190a5"/><path d="M121 70h7v138h-7zm204 0h7v138h-7z" fill="#96a3b6"/><rect x="142" y="89" width="71" height="40" rx="4" fill="#d8dce4"/><path d="M138 87h79v8h-79z" fill="#e8ebf0"/><rect x="164" y="101" width="24" height="11" rx="2" fill="#fbfcfe"/><path d="M171 106h10" stroke="#9ba7b9" stroke-width="2"/><rect x="230" y="96" width="72" height="33" rx="4" fill="#bdd5ee"/><path d="M226 94h80v7h-80z" fill="#d5e5f7"/><rect x="253" y="107" width="25" height="10" rx="2" fill="#f7fbff"/><path d="M261 112h10" stroke="#8caed3" stroke-width="2"/><rect x="143" y="157" width="73" height="50" rx="4" fill="#b9c8dd"/><path d="M138 154h83v8h-83z" fill="#d5deeb"/><path d="M168 174h23v10h-23z" fill="#f3f7fc"/><rect x="233" y="163" width="67" height="44" rx="4" fill="#c4d8ef"/><path d="M229 159h76v8h-76z" fill="#dceafa"/><rect x="256" y="178" width="21" height="10" rx="2" fill="#f8fbff"/><path d="M87 177c-15-21-22-42-7-53 1 19 14 25 11 48 8-25 9-44 28-44 4 18-11 29-21 48" fill="#849eb4"/><path d="M91 179v20" stroke="#718ca4" stroke-width="3"/><path d="M70 192h41l-6 25H76z" fill="#cbd3de"/><path d="m278 53 10-10 10 10" stroke="#9ab7d7" stroke-width="2"/><circle cx="77" cy="70" r="6" fill="#b2d2f4"/><path d="m356 152 5-5 5 5-5 5-5-5Z" fill="#b6cde8"/><path d="M188 43v-7m-3 3h6" stroke="#a5bdd9" stroke-width="2"/></svg>`;
function logRow(log, compact = false) {
  const quantity = log.quantity_delta ? `<span class="log-quantity">${log.quantity_delta > 0 ? '+' : ''}${number(log.quantity_delta)}</span>` : '';
  const transition = log.from_location && log.to_location && log.from_location !== log.to_location ? `${log.from_location} → ${log.to_location}` : log.to_location || log.from_location || '';
  const details = [transition, log.note].filter(Boolean).join(' · ');
  return `<div class="${compact ? 'list-row' : 'log-row'}"><div class="list-icon">${icon(log.action.startsWith('location_') ? 'pin' : log.action === 'search' ? 'search' : 'box')}</div><div class="list-content"><div class="list-title">${log.item_id ? `<button class="text-link" data-action="detail" data-id="${log.item_id}">${esc(log.item_name || '物品')}</button>` : `<span>${esc(log.item_name || (log.action === 'search' ? '物品查询' : '收纳位置'))}</span>`}<span class="log-action">${esc(ACTION[log.action] || log.action)}</span>${quantity}</div>${details ? `<p class="list-subtitle">${esc(details)}</p>` : ''}</div><time class="log-time" datetime="${esc(log.created_at)}">${date(log.created_at, true)}</time></div>`;
}
function expiryRow(item) { return `<button class="list-row expiry-row" data-action="detail" data-id="${item.id}"><span class="list-icon ${item.expiry_status === 'expired' ? 'expiry-expired' : 'expiry-soon'}">${icon('clock')}</span><span class="list-content"><span class="list-title">${esc(item.name)}</span><span class="list-subtitle">${esc(item.location_path)}</span></span><span class="list-end">${expiryBadge(item)}<span class="expiry-date">${date(item.expires_on)}</span></span></button>`; }
function renderDashboard(data) {
  const metrics = [
    ['box', data.total_items, '物品种类', '每一件，都有记录', 'green', 'all', '查看物品'],
    ['pin', data.location_count, '收纳位置', '家的空间，一目了然', 'sand', 'locations', '查看空间'],
    ['refresh', data.in_use_count, '正在使用', '取放状态，随时追踪', 'blue', 'in_use', '查看物品'],
    ['clock', data.soon_count + data.expired_count, '需要关注', data.expired_count ? `${data.expired_count} 件已过期` : '留意物品有效期', 'orange', 'attention', '查看提醒'],
  ];
  $('page-view').innerHTML = `<section class="hero-card dashboard-hero" aria-labelledby="dashboard-hero-title"><div class="hero-copy"><span class="kicker hero-kicker">A LITTLE ORDER. A BETTER LIFE.</span><h2 id="dashboard-hero-title">每一件物品，<br>都有自己的位置。</h2><p>少一点翻找，多一点从容。<br>记录家中物品，让需要的东西触手可及。</p><div class="hero-actions"><button class="button primary" data-action="browse-items">${icon('search')} 查找家中物品</button><button class="button secondary" data-action="add-item">${icon('plus')} 登记物品</button></div><div class="hero-tags"><span>${icon('shield')} 数据留在家中</span><span>${icon('history')} 每次取放可追溯</span></div></div><div class="hero-visual">${illustration}</div></section><div class="metric-grid">${metrics.map(([symbol, value, label, sub, color, valueFilter, link]) => `<button type="button" class="metric-card stat-card" data-action="stat-filter" data-value="${valueFilter}" aria-label="${label} ${number(value)}${symbol === 'pin' ? '处' : '种'}，${link}"><span class="metric-icon metric-${color}">${icon(symbol)}</span><span class="metric-value">${number(value)}<span>${symbol === 'pin' ? '处' : '种'}</span></span><span class="metric-label">${label}</span><span class="metric-trend">${esc(sub)}</span><span class="metric-link">${link} ${icon('arrow')}</span></button>`).join('')}</div>${aiPanel()}<div class="dashboard-grid"><section class="panel"><div class="panel-header"><div><h2 class="panel-title">${icon('clock')} 到期提醒</h2><p class="panel-subtitle">留意保质期，让每一份物品物尽其用</p></div><a class="text-link" href="#expiry">查看全部 ${icon('arrow')}</a></div><div class="panel-body">${data.expiring_items.length ? data.expiring_items.slice(0, 4).map(expiryRow).join('') : empty('暂时没有到期提醒', '填写物品有效期，临近到期时会在这里提醒你。', null, '', 'leaf')}</div></section><section class="panel"><div class="panel-header"><div><h2 class="panel-title">${icon('history')} 最近动态</h2><p class="panel-subtitle">看看家里物品的新变化</p></div><a class="text-link" href="#history">全部记录 ${icon('arrow')}</a></div><div class="panel-body">${data.recent_logs.length ? data.recent_logs.slice(0, 5).map(log => logRow(log, true)).join('') : empty('你的家庭档案，从这里开始', '登记第一件物品，它的每一次变化都会被记住。')}</div></section></div>${data.total_items === 0 ? `<section class="getting-started"><div>${icon('pin')}<span><strong>给物品一个准确的地址</strong><span>先添加房间、柜子或收纳箱，再开始登记物品。</span></span></div><button class="button secondary" data-action="add-location">${icon('plus')} 添加收纳位置</button></section>` : ''}`;
}
function itemCard(item) {
  const description = [item.brand, item.model].filter(Boolean).join(' · ');
  return `<button class="item-card" data-action="detail" data-id="${item.id}"><span class="item-photo">${itemVisual(item, true)}<span class="item-photo-status">${badge(item.status)}${archiveBadge(item)}</span></span><span class="item-body"><span class="item-meta-wrap"><span class="item-meta">${esc(item.category || '未分类')}</span></span><span class="item-title" title="${esc(item.name)}">${esc(item.name)}</span>${description ? `<span class="item-description" title="${esc(description)}">${esc(description)}</span>` : ''}<span class="item-path" title="${esc(item.location_path)}">${icon('pin')}${esc(item.location_path)}</span>${expiryBadge(item)}<span class="item-bottom"><span class="item-quantity"><strong>${number(item.quantity)}</strong> ${esc(item.unit)}</span><span class="item-date">${date(item.stored_date)} 入库</span></span></span></button>`;
}
function itemFilters() {
  const f = state.filters;
  return `<div class="toolbar"><form class="search-form" id="search-form"><div class="search-wrap">${icon('search')}<input class="search-input" id="item-search" name="query" type="search" placeholder="搜索名称、品牌、型号或标签…" maxlength="200" aria-label="搜索物品" value="${esc(f.q)}"><button class="button subtle button-small" type="submit">搜索</button></div></form><div class="filter-row"><select class="form-control" data-filter="location_id" aria-label="筛选位置">${option('', '全部位置', f.location_id)}${locationOptions(f.location_id, false)}</select><select class="form-control" data-filter="category_id" aria-label="筛选分类">${option('', '全部分类', f.category_id)}${state.categories.map(cat => option(cat.id, `${cat.name}${cat.active ? '' : '（已停用）'}`, f.category_id)).join('')}</select><select class="form-control" data-filter="status" aria-label="筛选状态">${option('', '全部状态', f.status)}${Object.entries(STATUS).map(([val, name]) => option(val, name, f.status)).join('')}</select><select class="form-control" data-filter="archived" aria-label="筛选归档">${option('false', '未归档', f.archived)}${option('true', '已归档', f.archived)}${option('all', '包含归档', f.archived)}</select>${hasFilters() ? `<button class="button subtle button-small" data-action="clear-filters">清除筛选</button>` : ''}</div></div>`;
}
function renderItems(items) {
  cancelMotion($('page-view'));
  const filtering = hasFilters();
  const sorted = [...items].sort((a, b) => {
    if (state.inventory.sort === 'name') return String(a.name).localeCompare(String(b.name), 'zh-CN', {numeric: true}) || b.id - a.id;
    if (state.inventory.sort === 'quantity') return b.quantity - a.quantity || b.id - a.id;
    if (state.inventory.sort === 'expiry') return String(a.expires_on || '9999-12-31').localeCompare(String(b.expires_on || '9999-12-31')) || b.id - a.id;
    return b.id - a.id;
  });
  const visible = sorted.slice(0, state.inventory.visible);
  const more = items.length > 48 ? `<div class="inventory-more"><p class="help-text">已显示 ${number(visible.length)} / ${number(items.length)} 种物品</p>${visible.length < items.length ? `<button class="button secondary" type="button" data-action="inventory-more">${icon('plus')} 再显示 ${number(Math.min(48, items.length - visible.length))} 种物品</button>` : '<span class="help-text">已显示全部物品</span>'}</div>` : '';
  $('page-view').innerHTML = `${itemFilters()}<div class="collection-heading inventory-toolbar"><span>${filtering ? '筛选结果 ·' : '全部档案 ·'} <strong>${number(items.length)}</strong> 种物品</span><div class="inventory-controls"><label class="sort-control"><span class="screen-reader">物品排序</span><select class="form-control sort-select" id="item-sort" aria-label="物品排序">${[['recent', '最近登记'], ['name', '名称排序'], ['quantity', '数量从多到少'], ['expiry', '到期日期从早到晚']].map(([value, label]) => option(value, label, state.inventory.sort)).join('')}</select></label><div class="view-switch" role="group" aria-label="物品显示方式">${[['grid', 'grid', '卡片视图'], ['list', 'list', '列表视图']].map(([value, symbol, label]) => `<button class="icon-button" type="button" data-action="inventory-view" data-value="${value}" aria-label="${label}" title="${label}" aria-pressed="${state.inventory.view === value}">${icon(symbol)}</button>`).join('')}</div></div></div>${items.length ? `<div class="item-grid${state.inventory.view === 'list' ? ' is-list' : ''}">${visible.map(itemCard).join('')}</div>${more}` : empty(filtering ? '没有找到匹配的物品' : '家里的宝贝，值得好好记住', filtering ? '换个关键词或清除筛选，再试一次。' : '从一件常用物品开始，拍张照，选个位置，建立你的家庭物品档案。', filtering ? 'clear-filters' : 'add-item', filtering ? '清除筛选' : '登记第一件物品')}`;
}
function renderExpiry(items) {
  const sorted = [...items].sort((a, b) => String(a.expires_on || '').localeCompare(String(b.expires_on || '')) || b.id - a.id);
  $('page-view').innerHTML = `<div class="tabs" role="tablist" aria-label="到期筛选"><button id="expiry-tab-soon" class="tab${state.expiry === 'soon' ? ' active' : ''}" role="tab" aria-controls="expiry-results" tabindex="${state.expiry === 'soon' ? '0' : '-1'}" aria-selected="${state.expiry === 'soon'}" data-action="expiry-tab" data-value="soon">${icon('clock')} 即将到期</button><button id="expiry-tab-expired" class="tab${state.expiry === 'expired' ? ' active' : ''}" role="tab" aria-controls="expiry-results" tabindex="${state.expiry === 'expired' ? '0' : '-1'}" aria-selected="${state.expiry === 'expired'}" data-action="expiry-tab" data-value="expired">${icon('alert')} 已经过期</button></div><div id="expiry-results" role="tabpanel" aria-labelledby="expiry-tab-${state.expiry}"><p class="expiry-explanation">${state.expiry === 'soon' ? '按每件物品设置的提前提醒天数显示，包含今天到期的物品。' : '有效期早于今天的物品。请及时检查并处理。'} · 日期按 ${esc(state.timezone)} 计算</p>${items.length ? `<div class="item-grid">${sorted.map(itemCard).join('')}</div>` : empty(state.expiry === 'soon' ? '最近没有物品即将到期' : '目前没有已过期的物品', '填写食品、药品等物品的有效期，我们会在这里帮你留意。', null, '', 'leaf')}</div>`;
}
function locationAdjacency() {
  const children = new Map();
  for (const loc of state.locations) {
    if (!children.has(loc.parent_id)) children.set(loc.parent_id, []);
    children.get(loc.parent_id).push(loc);
  }
  return children;
}
function locationBranch(parentId, depth = 0) {
  const children = locationAdjacency(), visited = new Set(), rows = [];
  const stack = (children.get(parentId) || []).map(loc => ({loc, depth})).reverse();
  while (stack.length) {
    const current = stack.pop(), loc = current.loc;
    if (visited.has(loc.id)) continue;
    visited.add(loc.id);
    rows.push(`<div class="location-node"><button class="location-row${state.selectedLocation === loc.id ? ' active' : ''}" data-action="select-location" data-id="${loc.id}" aria-pressed="${state.selectedLocation === loc.id}" style="--depth:${Math.min(current.depth, 12)}"><span class="location-icon">${icon(loc.type === 'home' ? 'home' : loc.type === 'room' ? 'pin' : 'package')}</span><span class="location-info"><strong>${esc(loc.name)}</strong><span>${esc(loc.code || LOCATION_TYPES[loc.type])}</span></span><span class="location-count">${number(loc.item_count)}</span>${icon('chevron')}</button></div>`);
    const next = children.get(loc.id) || [];
    for (let index = next.length - 1; index >= 0; index--) stack.push({loc: next[index], depth: current.depth + 1});
  }
  return rows.join('');
}
function renderLocations() {
  let loc = state.locations.find(value => value.id === state.selectedLocation) || state.locations[0];
  if (loc) state.selectedLocation = loc.id;
  $('page-view').innerHTML = `<div class="location-layout"><section class="panel location-tree"><div class="panel-header"><h2 class="panel-title">所有位置 <span class="chip">${state.locations.length}</span></h2><button class="icon-button" data-action="add-location" title="添加位置" aria-label="添加位置">${icon('plus')}</button></div><div class="tree-hint">房间 → 柜子 → 抽屉 → 收纳箱</div>${state.locations.length ? locationBranch(null) : empty('还没有收纳位置', '先为你的家建立一个位置。', 'add-location', '添加位置', 'pin')}</section><section class="panel location-detail">${loc ? `<div class="location-overview"><span class="location-detail-icon">${icon(loc.type === 'home' ? 'home' : 'package')}</span><span class="eyebrow">${esc(LOCATION_TYPES[loc.type])}</span><h2>${esc(loc.name)}</h2><p class="location-path">${esc(loc.path)}</p>${loc.code ? `<span class="location-code">位置编号 · ${esc(loc.code)}</span>` : ''}<div class="location-stat"><strong>${number(loc.item_count)}</strong><span>种物品存放于此位置及下级位置</span></div><div class="location-actions"><button class="button primary" data-action="view-location-items" data-id="${loc.id}">${icon('box')} 查看物品</button><button class="button secondary" data-action="qr" data-id="${loc.id}">${icon('qr')} 位置二维码</button></div></div><div class="location-detail-footer"><button class="button subtle" data-action="add-location" data-parent="${loc.id}">${icon('plus')} 添加下级位置</button><button class="button subtle" data-action="edit-location" data-id="${loc.id}">${icon('edit')} 编辑</button><button class="icon-button muted" data-action="delete-location" data-id="${loc.id}" title="删除位置" aria-label="删除位置">${icon('trash')}</button></div><p class="location-tip">${icon('qr')} 打印二维码贴在柜子或箱子上，扫码就能查看里面的物品。</p>` : empty('给物品一个位置', '位置可以层层关联，形成清晰的家庭收纳地图。', 'add-location', '添加位置', 'pin')}</section></div>`;
}
function renderHistory(logs) {
  $('page-view').innerHTML = `<div class="history-heading"><span>最近 <strong>${number(logs.length)}</strong> 条记录</span><span class="chip">${icon('shield')} 操作自动留痕</span></div><section class="panel log-table">${logs.length ? logs.map(log => logRow(log)).join('') : empty('尚无操作记录', '登记物品、取放或调整位置后，记录会自动出现在这里。', 'add-item', '登记第一件物品', 'history')}</section><p class="help-text history-note">展示最近 200 条操作。导出数据可获取完整记录。</p>`;
}

const UPDATE_PHASES = {idle: '等待检查', checking: '正在检查新版', queued: '更新已安排', downloading: '正在下载新版', loading: '正在准备新版', stopping: '正在暂停服务', backing_up: '正在备份家庭档案', applying: '正在安装新版', verifying: '正在检查新版是否正常', completed: '更新已完成', failed: '更新未完成', rolled_back: '已恢复原版本'};
const UPDATE_RUNNING = new Set(['queued', 'downloading', 'loading', 'stopping', 'backing_up', 'applying', 'verifying']);
const updateRunning = () => state.updates.inFlight || UPDATE_RUNNING.has(state.updates.status?.phase);
const updateBusy = () => updateRunning() || state.updates.status?.phase === 'checking';
let updatePollTimer = null;

function safeReleaseUrl(value) {
  try { const url = new URL(value); return url.protocol === 'https:' && url.hostname === 'github.com' && url.pathname.startsWith('/Adventurer0/HomeStorage/releases/') ? url.href : ''; }
  catch (_) { return ''; }
}
function updatePhaseText() {
  if (state.updates.offline && updateRunning()) return '服务正在重新连接，更新结果会在恢复后显示。';
  const status = state.updates.status;
  return status?.message || UPDATE_PHASES[status?.phase] || '更新状态暂时不可用。';
}
function renderUpdateNotice() {
  const notice = $('update-notice');
  notice.hidden = state.route === 'settings' || (!updateRunning() && !state.updates.needsReload);
  if (notice.hidden) return;
  notice.innerHTML = `<span>${icon(state.updates.needsReload ? 'check' : 'refresh')}<span>${esc(state.updates.needsReload ? '新版已就绪，刷新页面即可使用。' : updatePhaseText())}</span></span><button class="button secondary button-small" data-action="${state.updates.needsReload ? 'update-reload' : 'update-settings'}">${state.updates.needsReload ? '刷新使用新版' : '查看更新'}</button>`;
}
const AI_SETTINGS_FIELDS = ['api_base_url', 'model', 'transcription_model', 'transcription_base_url'];
function aiConfigurationBusy() { return state.ai.busy || state.ai.transcribing || Boolean(state.ai.recording); }
function aiSettingsForm() { return aiSettingsPanel?.querySelector('#ai-settings-form'); }
function aiSettingsDirty() {
  const form = aiSettingsForm(), status = state.aiSettings.status;
  if (!form || !status) return false;
  return AI_SETTINGS_FIELDS.some(name => form.elements[name].value.trim() !== String(status[name] || '')) ||
    Boolean(form.elements.api_key.value || form.elements.transcription_api_key.value || form.elements.clear_api_key.checked || form.elements.clear_transcription_api_key.checked);
}
function acceptAISettings(status) {
  state.aiSettings.status = {
    ...Object.fromEntries(AI_SETTINGS_FIELDS.map(name => [name, typeof status[name] === 'string' ? status[name] : ''])),
    api_key_set: status.api_key_set === true, transcription_api_key_set: status.transcription_api_key_set === true,
    enabled: status.enabled === true, voice_enabled: status.voice_enabled === true,
    source: status.source === 'web' ? 'web' : 'environment',
  };
}
function setAISettingsValues() {
  const form = aiSettingsForm(), status = state.aiSettings.status;
  if (!form || !status) return;
  AI_SETTINGS_FIELDS.forEach(name => { form.elements[name].value = status[name]; });
}
function renderAISettings() {
  if (state.route !== 'settings' || $('app-shell').hidden || !$('ai-settings-mount')) return;
  const settings = state.aiSettings;
  if (!aiSettingsPanel) {
    aiSettingsPanel = document.createElement('section');
    aiSettingsPanel.className = 'panel ai-settings-panel';
    aiSettingsPanel.setAttribute('aria-labelledby', 'ai-settings-title');
    aiSettingsPanel.innerHTML = `<div class="panel-header"><div><h2 class="panel-title" id="ai-settings-title">${icon('chat')} AI 与语音</h2><p class="panel-subtitle">填写供应商的地址、密钥和模型，保存后立即生效。</p></div><span class="status-chip" id="ai-settings-connection">正在读取…</span></div><div class="panel-body"><div class="ai-settings-unavailable update-unavailable" id="ai-settings-unavailable" hidden role="status"><strong>请先设置登录密码</strong><p>为了保护 API 密钥，网页配置需要启用 HomeStorage 登录密码。在 NAS 的 Compose 中设置一次登录密码后，即可在这里配置 AI；已有的 AI 配置仍可使用。</p></div><p class="error-banner" id="ai-settings-error" hidden role="alert"></p><form id="ai-settings-form" autocomplete="off"><fieldset id="ai-settings-fields"><div class="ai-settings-columns"><section class="ai-settings-group" aria-labelledby="ai-chat-settings-title"><h3 id="ai-chat-settings-title">文字聊天</h3><p>选择支持工具调用的 OpenAI 兼容模型。</p><label class="form-field"><span class="field-label">聊天 API 地址</span><input class="form-control" name="api_base_url" type="url" inputmode="url" spellcheck="false" maxlength="2048" placeholder="https://api.example.com/v1"><span class="help-text">通常以 /v1 结尾，以供应商文档为准。</span></label><label class="form-field"><span class="field-label">聊天模型名称</span><input class="form-control" name="model" type="text" spellcheck="false" maxlength="256" placeholder="供应商提供的模型名称"><span class="help-text">模型需要支持工具调用；留空可停用助手。</span></label><label class="form-field"><span class="field-label">聊天 API 密钥</span><input class="form-control" name="api_key" type="password" autocomplete="new-password" spellcheck="false" maxlength="4096" placeholder="填写供应商提供的密钥"><span class="help-text" id="ai-chat-key-status"></span></label><label class="ai-key-clear"><input type="checkbox" name="clear_api_key"><span>保存时移除聊天密钥，停用助手及录音</span></label></section><section class="ai-settings-group" aria-labelledby="ai-voice-settings-title"><h3 id="ai-voice-settings-title">语音识别 <span>可选</span></h3><p>识别后先核对文字，再发送给助手。</p><label class="form-field"><span class="field-label">语音识别模型名称</span><input class="form-control" name="transcription_model" type="text" spellcheck="false" maxlength="256" placeholder="供应商提供的语音模型名称"><span class="help-text">留空关闭录音，仍可使用文字聊天。</span></label><label class="form-field"><span class="field-label">语音 API 地址</span><input class="form-control" name="transcription_base_url" type="url" inputmode="url" spellcheck="false" maxlength="2048" placeholder="留空使用聊天 API 地址"><span class="help-text">单独使用语音服务时填写，否则留空。</span></label><label class="form-field"><span class="field-label">语音 API 密钥</span><input class="form-control" name="transcription_api_key" type="password" autocomplete="new-password" spellcheck="false" maxlength="4096" placeholder="首次留空使用聊天密钥"><span class="help-text" id="ai-voice-key-status"></span></label><label class="ai-key-clear"><input type="checkbox" name="clear_transcription_api_key"><span>保存时移除独立语音密钥，改用聊天密钥</span></label></section></div><div class="ai-settings-privacy">${icon('shield')}<p>密钥保存在 NAS，保存后不会在网页显示。对话、录音及查询所需的档案信息会发送到你配置的 API 服务。AI 写入和归档物品仍需你确认。</p></div><p class="help-text ai-settings-microphone">网页录音需要 HTTPS 和麦克风权限；也可使用手机键盘的语音输入。</p><div class="ai-settings-footer"><span id="ai-settings-save-status" role="status"></span><button class="button primary" type="submit">${icon('check')} 保存配置</button></div></fieldset></form><button class="button subtle button-small ai-settings-retry" type="button" data-action="ai-settings-retry" hidden>重新读取配置</button></div>`;
    setAISettingsValues();
  }
  const mount = $('ai-settings-mount');
  if (aiSettingsPanel.parentElement !== mount) mount.replaceChildren(aiSettingsPanel);
  const form = aiSettingsForm(), status = settings.status;
  const find = selector => aiSettingsPanel.querySelector(selector);
  const pending = settings.loading && !status || settings.busy;
  const assistantBusy = aiConfigurationBusy();
  find('#ai-settings-connection').textContent = status?.enabled ? status.voice_enabled ? '聊天与语音已配置' : '文字聊天已配置' : status ? '助手未启用' : settings.forbidden ? '需要登录密码' : '正在读取…';
  find('#ai-settings-unavailable').hidden = !settings.forbidden;
  find('#ai-settings-error').hidden = !settings.error || settings.forbidden;
  find('#ai-settings-error').textContent = settings.error;
  form.hidden = settings.forbidden;
  find('#ai-settings-fields').disabled = !status || pending;
  const chatKey = form.elements.api_key, voiceKey = form.elements.transcription_api_key;
  chatKey.placeholder = status?.api_key_set ? '已设置 · 留空保留，输入新密钥替换' : '填写供应商提供的密钥';
  voiceKey.placeholder = status?.transcription_api_key_set ? '已设置 · 留空保留，输入新密钥替换' : '留空使用聊天密钥';
  find('#ai-chat-key-status').textContent = status?.api_key_set ? '已有密钥。留空不会清除，也不会显示原密钥。' : '尚未设置聊天密钥。';
  find('#ai-voice-key-status').textContent = status?.transcription_api_key_set ? '已有独立密钥。留空保留，勾选下方选项才会移除。' : '没有独立密钥，使用聊天密钥。';
  form.elements.clear_api_key.disabled = pending || !status?.api_key_set;
  form.elements.clear_transcription_api_key.disabled = pending || !status?.transcription_api_key_set;
  const dirty = aiSettingsDirty();
  find('#ai-settings-save-status').textContent = settings.busy ? '正在保存…' : assistantBusy ? '助手正在处理，请完成后再保存配置。' : settings.message || (!status ? '正在读取 AI 配置…' : dirty ? '有未保存的修改，保存后立即生效。' : status.source === 'web' ? '已使用网页保存的配置。' : '当前使用 NAS 部署配置，可以在这里修改。');
  const save = form.querySelector('[type="submit"]');
  save.disabled = !status || pending || assistantBusy || !dirty;
  save.innerHTML = settings.busy ? '<span class="spinner" aria-hidden="true"></span> 正在保存…' : `${icon('check')} 保存配置`;
  const retry = find('[data-action="ai-settings-retry"]');
  retry.hidden = !settings.error || settings.forbidden;
  retry.disabled = settings.loading || settings.busy;
}
async function aiSettingsRequest(path, options = {}) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {credentials: 'same-origin', ...options, headers: {'Content-Type': 'application/json'}, cache: 'no-store', signal: controller.signal});
    if (!response.ok) {
      let message = response.status === 403 ? '网页配置需要先设置 HomeStorage 登录密码。' : response.status === 401 ? '登录已失效，请重新登录。' : 'AI 配置暂时无法读取或保存，请稍后重试。';
      try { const result = await response.json(); if (typeof result.detail === 'string') message = result.detail; } catch (_) { /* Keep the safe fallback. */ }
      const error = new Error(message); error.status = response.status; throw error;
    }
    const result = await response.json();
    if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('AI 配置返回异常，请稍后重试。');
    return result;
  } catch (error) {
    if (error.status) throw error;
    throw new Error('无法连接 NAS，请检查网络后重试。');
  } finally { clearTimeout(timer); }
}
async function loadAISettings(pageToken = state.renderToken) {
  const settings = state.aiSettings;
  if (settings.busy) return;
  const generation = settings.generation, sequence = ++settings.sequence;
  settings.loading = true; settings.error = ''; renderAISettings();
  try {
    const status = await aiSettingsRequest('/api/settings/ai');
    if (generation !== settings.generation || sequence !== settings.sequence || pageToken !== state.renderToken || state.route !== 'settings') return;
    const preserveDraft = aiSettingsDirty();
    acceptAISettings(status); settings.forbidden = false;
    if (!preserveDraft) setAISettingsValues();
  } catch (error) {
    if (generation !== settings.generation || sequence !== settings.sequence || pageToken !== state.renderToken || state.route !== 'settings') return;
    if (error.status === 401) { showLogin(); return; }
    settings.forbidden = error.status === 403; settings.error = error.message;
  } finally {
    if (generation === settings.generation && sequence === settings.sequence) { settings.loading = false; renderAISettings(); }
  }
}
async function saveAISettings(form) {
  const settings = state.aiSettings;
  if (settings.busy || !settings.status || settings.forbidden || aiConfigurationBusy() || !aiSettingsDirty()) return;
  const generation = settings.generation, pageToken = state.renderToken;
  ++settings.sequence;
  const body = Object.fromEntries(AI_SETTINGS_FIELDS.filter(name => form.elements[name].value.trim() !== settings.status[name]).map(name => [name, form.elements[name].value.trim()]));
  for (const name of ['api_key', 'transcription_api_key']) {
    if (form.elements[`clear_${name}`].checked) body[name] = null;
    else if (form.elements[name].value) body[name] = form.elements[name].value;
  }
  settings.busy = true; settings.loading = false; settings.error = ''; settings.message = ''; renderAISettings();
  try {
    const status = await aiSettingsRequest('/api/settings/ai', {method: 'PATCH', body: JSON.stringify(body)});
    if (generation !== settings.generation) return;
    acceptAISettings(status); settings.forbidden = false;
    for (const name of ['api_key', 'transcription_api_key']) { form.elements[name].value = ''; form.elements[`clear_${name}`].checked = false; }
    setAISettingsValues();
    state.ai.status = {...state.ai.status, enabled: settings.status.enabled, voice_enabled: settings.status.voice_enabled, model: settings.status.model};
    settings.message = '已保存，配置立即生效，无需重建容器。';
    if (pageToken === state.renderToken && state.route === 'settings') toast('AI 配置已保存，立即生效。');
    try {
      const aiStatus = await aiSettingsRequest('/api/ai/status');
      if (generation === settings.generation) { state.ai.status = aiStatus; renderAI(); }
    } catch (_) { /* The save succeeded; the next login refreshes capability status. */ }
  } catch (error) {
    if (generation !== settings.generation) return;
    if (error.status === 401) { showLogin(); return; }
    settings.forbidden = error.status === 403; settings.error = error.message;
  } finally {
    for (const name of ['api_key', 'transcription_api_key']) delete body[name];
    if (generation === settings.generation) { settings.busy = false; renderAISettings(); }
  }
}
function clearAISettings() {
  state.aiSettings.generation++; state.aiSettings.sequence++;
  state.aiSettings.status = null; state.aiSettings.loading = false; state.aiSettings.busy = false;
  state.aiSettings.forbidden = false; state.aiSettings.error = ''; state.aiSettings.message = '';
  if (aiSettingsPanel) {
    aiSettingsPanel.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
    aiSettingsPanel.remove(); aiSettingsPanel = null;
  }
}

function renderSettings() {
  if (state.route !== 'settings' || $('app-shell').hidden) return;
  if (!$('settings-update-panels')) $('page-view').innerHTML = '<div class="settings-layout"><div id="ai-settings-mount"></div><div id="settings-update-panels"></div></div>';
  renderAISettings();
  const updates = state.updates;
  const status = updates.status;
  const available = Boolean(status?.available);
  const busy = updates.busy || updateBusy();
  const phase = status?.phase || 'idle';
  const releaseUrl = safeReleaseUrl(status?.release_url);
  const auto = updates.preference ?? Boolean(status?.auto_update);
  const dirty = status && auto !== Boolean(status.auto_update);
  const total = Number(status?.progress?.total_bytes);
  const downloaded = Number(status?.progress?.downloaded_bytes);
  const progress = phase === 'downloading' ? `<div class="update-download"><progress aria-label="新版下载进度"${Number.isFinite(total) && total > 0 && Number.isFinite(downloaded) && downloaded >= 0 ? ` max="${total}" value="${Math.min(downloaded, total)}"` : ''}></progress><span>${Number.isFinite(downloaded) && downloaded > 0 ? `${(downloaded / 1048576).toFixed(1)} MB${Number.isFinite(total) && total > 0 ? ` / ${(total / 1048576).toFixed(1)} MB` : ''}` : '正在连接下载服务…'}</span></div>` : '';
  const phaseCard = status && (phase !== 'idle' || updates.offline || updates.error) ? `<div class="update-state update-state-${['failed', 'rolled_back'].includes(phase) ? 'warning' : 'normal'}" role="status"><strong>${updates.offline && updateRunning() ? '正在等待服务恢复' : esc(UPDATE_PHASES[phase] || '更新状态')}</strong><p>${esc(updatePhaseText())}</p>${progress}${updateRunning() ? '<p class="help-text">备份和安装期间会短暂停用物品录入。请保持 NAS 开机，连接中断后本页会自动重试。</p>' : ''}${updates.needsReload ? '<button class="button primary button-small" data-action="update-reload">刷新使用新版</button>' : ''}</div>` : '';
  $('settings-update-panels').innerHTML = `<div class="settings-grid"><section class="panel update-version-panel"><div class="panel-header"><div><h2 class="panel-title">${icon('refresh')} 程序更新</h2><p class="panel-subtitle">查看新版，选择适合自己的更新时间。</p></div><span class="status-chip">当前 ${esc(status?.current_version || state.appVersion || '版本未知')}</span></div><div class="panel-body"><div class="update-version-summary"><div><span>最新稳定版本</span><strong>${esc(status?.latest_version || '尚未检查')}</strong></div><div><span>上次检查</span><strong>${date(status?.last_checked_at, true)}</strong></div></div>${!available ? `<div class="update-unavailable" role="status"><strong>更新服务尚未就绪</strong><p>${esc(status?.message || updates.error || '正在读取更新配置…')}</p><p class="help-text">首次启用需要按新版部署说明安装一次更新服务，保留原数据、密码和 AI 配置。如果已经配置，请稍后重新检测服务。</p></div>` : ''}${updates.error && available ? `<p class="error-banner update-error" role="alert">${esc(updates.error)}</p>` : ''}${phaseCard}${status?.update_available ? `<p class="update-available">${icon('check')} 发现新版 ${esc(status.latest_version)}</p>` : available && status?.last_checked_at && status?.latest_version && ['idle', 'completed'].includes(phase) && !busy && !updates.offline ? '<p class="update-current">已检查，目前没有可安装的新版。</p>' : ''}<div class="update-buttons"><button class="button secondary" data-action="update-check"${!available || busy || updates.offline ? ' disabled' : ''}>${icon('refresh')}${phase === 'checking' ? '正在检查…' : '检查更新'}</button><button class="button primary" data-action="update-install"${!available || busy || updates.offline || !status?.update_available || !status?.latest_version ? ' disabled' : ''}>${icon('download')} 更新到新版</button>${!available || updates.error || updates.offline ? `<button class="button subtle" data-action="update-reconnect">${available ? '重新连接' : '重新检测更新服务'}</button>` : ''}${updates.reauthenticate ? '<button class="button secondary" data-action="update-login">重新登录</button>' : ''}</div>${status?.release_notes ? `<div class="update-release-notes"><h3>新版说明${releaseUrl ? `<a class="text-link" href="${esc(releaseUrl)}" target="_blank" rel="noopener noreferrer">查看发布页面 ${icon('arrow')}</a>` : ''}</h3><div>${esc(status.release_notes)}</div></div>` : ''}</div></section><section class="panel update-preference-panel"><div class="panel-header"><div><h2 class="panel-title">${icon('settings')} 自动更新</h2><p class="panel-subtitle">默认关闭，只有保存后才会生效。</p></div></div><div class="panel-body"><form id="update-preference-form"><label class="update-toggle" for="auto-update"><span><strong>允许自动安装稳定新版</strong><span>开启后每天凌晨 03:00（${esc(timezoneLabel())}）检查，有新版时先备份数据再更新。</span></span><span class="switch-control"><input type="checkbox" id="auto-update" name="auto_update" role="switch"${auto ? ' checked' : ''}${!available || updates.busy || updates.offline ? ' disabled' : ''}><span class="switch-track" aria-hidden="true"></span></span></label><p class="help-text">关闭后只会检查新版，安装由你点击确认。已经开始的一次更新会继续完成，避免中途损坏数据。</p><div class="update-preference-footer"><span id="update-save-status" role="status">${esc(updates.savedMessage || (dirty ? '已更改，等待保存。' : status?.auto_update ? '当前已开启自动更新。' : '当前已关闭自动更新。'))}</span><button class="button primary button-small" type="submit"${!available || updates.busy || updates.offline || !dirty ? ' disabled' : ''}>保存选择</button></div></form><div class="update-backup-info"><h3>${icon('shield')} 更新前自动备份</h3><p>物品资料、照片和历史记录会一起备份，保留最近 3 次成功备份。新版运行失败时，更新服务会尝试恢复原版本和数据。</p><dl><div><dt>上次备份</dt><dd>${date(status?.last_backup_at, true)}</dd></div><div><dt>已保留备份</dt><dd>${number(status?.backup_count)} 份</dd></div><div><dt>下次检查</dt><dd>${date(status?.next_check_at, true)}</dd></div></dl><p class="help-text">系统备份也需要保留。程序更新独立于 AI 助手，AI 无法开启或执行更新。</p></div></div></section></div>`;
  renderUpdateNotice();
}
function acceptUpdateStatus(status) {
  const updates = state.updates;
  if (!status.available && updateRunning()) {
    updates.status = {...updates.status, available: false, current_version: status.current_version || updates.status?.current_version, message: status.message || '更新服务暂时未就绪，正在重新连接。'};
    updates.inFlight = true; updates.offline = true; updates.error = status.message || '正在等待更新服务恢复。';
    renderUpdateNotice(); return;
  }
  const draftChanged = updates.status && updates.preference !== null && updates.preference !== Boolean(updates.status.auto_update);
  updates.status = status;
  if (!draftChanged) updates.preference = Boolean(status.auto_update);
  updates.offline = false; updates.disconnectedSince = null; updates.reauthenticate = false; updates.error = '';
  updates.inFlight = UPDATE_RUNNING.has(status.phase);
  if (state.appVersion && status.current_version && status.current_version !== state.appVersion) updates.needsReload = true;
  renderUpdateNotice();
}
async function updateRequest(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {credentials: 'same-origin', ...options, headers: {'Content-Type': 'application/json'}, signal: controller.signal, cache: 'no-store'});
    if (!response.ok) {
      let message = response.status === 401 ? '登录状态已失效，请重新登录。' : response.status === 404 ? '当前版本还没有更新服务，请先按新版部署说明升级一次。' : '更新服务暂时无法连接，请稍后重新检查。';
      try { const data = await response.json(); if (typeof data.detail === 'string') message = data.detail; } catch (_) { /* Safe fallback for a restarting service. */ }
      const error = new Error(message); error.status = response.status; throw error;
    }
    const data = await response.json();
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('更新服务返回异常，请稍后重试。');
    return data;
  } catch (error) {
    if (error.status) throw error;
    const offline = new Error('无法连接更新服务，请检查 NAS 网络。'); offline.offline = true; throw offline;
  } finally { clearTimeout(timer); }
}
function handleUpdateError(error) {
  const updates = state.updates;
  if (error.status === 401 && !updateRunning()) { showLogin(); return; }
  updates.offline = Boolean(error.offline || error.status === 401 || error.status === 503);
  if (updates.offline && !updates.disconnectedSince) updates.disconnectedSince = Date.now();
  updates.reauthenticate = error.status === 401 && Date.now() - updates.disconnectedSince >= 30000;
  updates.error = updates.offline && updateRunning() ? '更新期间连接暂时中断，正在等待服务恢复。' : error.message;
  if (updates.reauthenticate) updates.error = '服务正在恢复或登录已失效。可以继续等待，或重新登录查看更新结果。';
  renderUpdateNotice();
}
function scheduleUpdatePoll() {
  clearTimeout(updatePollTimer); updatePollTimer = null;
  if ($('app-shell').hidden || (state.route !== 'settings' && !updateRunning() && !state.updates.status?.auto_update)) return;
  updatePollTimer = setTimeout(async () => { await loadUpdateStatus(); renderSettings(); }, updateBusy() || state.updates.offline ? 5000 : state.route === 'settings' ? 30000 : 60000);
}
async function loadUpdateStatus() {
  const updates = state.updates;
  if (updates.polling || updates.busy) { scheduleUpdatePoll(); return; }
  const generation = updates.generation;
  const sequence = ++updates.sequence;
  updates.polling = true;
  try { const status = await updateRequest('/api/settings/updates'); if (generation === updates.generation && sequence === updates.sequence) acceptUpdateStatus(status); }
  catch (error) { if (generation === updates.generation && sequence === updates.sequence) handleUpdateError(error); }
  finally { if (generation === updates.generation) { updates.polling = false; scheduleUpdatePoll(); } }
}
async function checkUpdates() {
  const updates = state.updates;
  if (updates.busy || updateBusy() || !updates.status?.available) return;
  const generation = updates.generation;
  ++updates.sequence;
  updates.busy = true; updates.savedMessage = ''; renderSettings();
  try { const status = await updateRequest('/api/settings/updates/check', {method: 'POST', body: '{}'}); if (generation === updates.generation) acceptUpdateStatus(status); }
  catch (error) { if (generation === updates.generation) handleUpdateError(error); }
  finally { if (generation === updates.generation) { updates.busy = false; renderSettings(); scheduleUpdatePoll(); } }
}
async function saveUpdatePreference() {
  const updates = state.updates;
  if (updates.busy || !updates.status?.available) return;
  const generation = updates.generation;
  ++updates.sequence;
  const choice = Boolean(updates.preference);
  updates.busy = true; updates.savedMessage = ''; renderSettings();
  try {
    const status = await updateRequest('/api/settings/updates', {method: 'PATCH', body: JSON.stringify({auto_update: choice})});
    if (generation !== updates.generation) return;
    acceptUpdateStatus(status); updates.preference = Boolean(status.auto_update);
    updates.savedMessage = status.auto_update ? '已保存，自动更新已开启。' : '已保存，自动更新已关闭。';
    toast(updates.savedMessage);
  } catch (error) { if (generation === updates.generation) handleUpdateError(error); }
  finally { if (generation === updates.generation) { updates.busy = false; renderSettings(); scheduleUpdatePoll(); } }
}
function confirmUpdateInstall() {
  const status = state.updates.status;
  if (!status?.available || !status.update_available || state.updates.busy || updateBusy() || !/^\d+\.\d+\.\d+$/.test(status.latest_version || '')) return;
  openModal('update-modal', `<form id="update-install-form" data-version="${esc(status.latest_version)}">${modalHeader('确认更新程序', 'update-modal-title')}<div class="modal-body"><div class="update-confirm-version"><span>当前 ${esc(status.current_version)}</span>${icon('arrow')}<strong>新版 ${esc(status.latest_version)}</strong></div><p>开始后会下载新版，自动备份家庭档案，再安装并检查服务。物品录入会短暂停用，页面会自动等待重新连接。</p><p>密码、AI 配置和数据会保留。如果新版运行失败，更新服务会尝试恢复原版本和数据。</p><p class="help-text">请保持 NAS 开机。开始后的这次更新需要完成，关闭自动更新只影响之后的更新。</p><p class="error-banner form-error" hidden role="alert"></p></div><div class="modal-footer"><button class="button secondary" type="button" data-action="close-modal">暂不更新</button><button class="button primary" type="submit">确认更新到 ${esc(status.latest_version)}</button></div></form>`);
}
async function installUpdate(form) {
  const updates = state.updates;
  if (updates.busy || updateBusy()) return;
  if (form.dataset.version !== updates.status?.latest_version || !updates.status?.update_available) { $('update-modal').close(); toast('可安装版本已变化，请重新检查更新。', true); await loadUpdateStatus(); renderSettings(); return; }
  const generation = updates.generation;
  ++updates.sequence;
  updates.busy = true; updates.inFlight = true;
  const button = form.querySelector('[type="submit"]'); button.disabled = true;
  const errorElement = form.querySelector('.form-error'); errorElement.hidden = true;
  try {
    const status = await updateRequest('/api/settings/updates/install', {method: 'POST', body: JSON.stringify({confirmed: true, version: form.dataset.version})});
    if (generation !== updates.generation) return;
    acceptUpdateStatus(status); $('update-modal').close(); toast('更新已安排，可在设置页查看进度。');
  } catch (error) {
    if (generation !== updates.generation) return;
    if (error.status && error.status !== 503 && error.status !== 401) updates.inFlight = UPDATE_RUNNING.has(updates.status?.phase);
    handleUpdateError(error);
    if (updates.offline) { $('update-modal').close(); updates.error = '暂未收到更新结果，正在重新核对进度。请勿重复提交更新。'; }
    else { errorElement.textContent = error.message; errorElement.hidden = false; }
  } finally { if (generation === updates.generation) { updates.busy = false; button.disabled = false; renderSettings(); scheduleUpdatePoll(); } }
}
async function refreshPage(showLoading = true) {
  const token = ++state.renderToken;
  cancelMotion($('page-view'));
  $('page-error').hidden = true;
  $('page-view').setAttribute('aria-busy', 'true');
  setNavigation();
  if (showLoading && !(state.route === 'settings' && $('ai-settings-mount'))) $('page-view').innerHTML = loading();
  try {
    if (state.route === 'dashboard') { const data = await api('/api/dashboard'); if (token !== state.renderToken) return; state.dashboard = data; renderDashboard(data); setNavigation(); }
    else if (state.route === 'items') { const params = new URLSearchParams(Object.entries(state.filters).filter(([,value]) => value)); const items = await api(`/api/items?${params}`); if (token !== state.renderToken) return; state.items = items; state.inventory.visible = 48; renderItems(items); }
    else if (state.route === 'expiry') { const items = await api(`/api/items?expiry=${state.expiry}`); if (token !== state.renderToken) return; renderExpiry(items); }
    else if (state.route === 'locations') { state.locations = await api('/api/locations'); if (token !== state.renderToken) return; renderLocations(); }
    else if (state.route === 'categories') { state.categories = await api('/api/categories'); if (token !== state.renderToken) return; renderCategories(); }
    else if (state.route === 'history') { const logs = await api('/api/logs?limit=200'); if (token !== state.renderToken) return; renderHistory(logs); }
    else if (state.route === 'settings') { renderSettings(); await Promise.all([loadUpdateStatus(), loadAISettings(token)]); if (token !== state.renderToken) return; renderSettings(); }
  } catch (error) { if (token !== state.renderToken) return; showError(error); $('page-view').innerHTML = empty('暂时无法读取数据', '请检查 NAS 服务是否运行，稍后重新连接。', 'retry', '重新连接', 'refresh'); }
  finally {
    if (token === state.renderToken) {
      $('page-view').setAttribute('aria-busy', 'false');
      if (showLoading && !$('app-shell').hidden && !$('page-view').contains(document.activeElement)) enterCollection($('page-view').children);
    }
  }
}
function navigate(route) { if (location.hash === `#${route}`) { state.route = route; refreshPage(); } else location.hash = route; }
async function refreshAfterMutation() { [state.locations, state.categories, state.dashboard] = await Promise.all([api('/api/locations'), api('/api/categories'), api('/api/dashboard')]); await refreshPage(false); }
function modalHeader(title, id, subtitle = '') { return `<div class="modal-header"><div><h2 class="modal-title" id="${id}" tabindex="-1">${esc(title)}</h2>${subtitle ? `<p class="panel-subtitle">${esc(subtitle)}</p>` : ''}</div><button type="button" class="icon-button modal-close" data-action="close-modal" aria-label="关闭">${icon('close')}</button></div>`; }
function openModal(id, content) {
  const dialog = $(id);
  const opening = !dialog.open;
  cancelMotion(dialog);
  dialog.innerHTML = content;
  if (opening) dialog.showModal();
  dialog.scrollTop = 0;
  dialog.querySelector('.modal-title')?.focus({preventScroll: true});
  if (opening) enterMotion(dialog, {modal: true});
  return dialog;
}
function field(label, name, value = '', type = 'text', extra = '', wide = false) { return `<label class="form-field${wide ? ' span-2' : ''}"><span class="field-label">${esc(label)}</span><input class="form-control" name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`; }
function selectField(label, name, values, value = '', extra = '') { return `<label class="form-field"><span class="field-label">${esc(label)}</span><select class="form-control" name="${name}" ${extra}>${values.map(([key, text]) => option(key, text, value)).join('')}</select></label>`; }

async function openItemForm(id = null) {
  try {
    const item = id ? await api(`/api/items/${id}`) : {quantity: 1, unit: '件', warning_days: 7, category_id: state.categories.find(cat => cat.active && cat.name === '其他')?.id || state.categories.find(cat => cat.active)?.id || '', stored_date: today(), location_id: state.filters.location_id || state.selectedLocation || state.locations[0]?.id || ''};
    const photo = imageUrl(item.image_url);
    releaseItemPhotoPreview();
    openModal('item-modal', `<form id="item-form" data-id="${id || ''}" data-image="${esc(item.image_url || '')}">${modalHeader(id ? '编辑物品档案' : '登记一件物品', 'item-modal-title', id ? '库存和位置变化请在物品详情中操作，会自动留痕。' : '拍张照、选个位置，让物品有迹可循。')}<div class="modal-body"><div class="image-upload"><div class="image-preview" id="image-preview">${itemVisual(item)}</div><div><label class="button secondary button-small" for="item-photo">${icon('image')} ${photo ? '更换照片' : '添加照片'}</label><input id="item-photo" type="file" name="photo" accept="image/jpeg,image/png,image/webp" class="screen-reader"><p class="help-text">JPG、PNG 或 WebP，最大 10 MB</p><p class="help-text image-auto-hint">没有照片？会按名称和分类自动搭配图标。</p><button type="button" class="text-link" data-action="remove-photo"${photo ? '' : ' hidden'}>移除照片</button></div></div><div class="form-grid">${field('物品名称 *', 'name', item.name, 'text', 'required maxlength="200" placeholder="例如：便携电动打气筒"', true)}<label class="form-field"><span class="field-label">物品分类 *</span><select class="form-control" name="category_id" required>${option('', '请选择分类', item.category_id)}${categoryOptions(item.category_id)}</select><span class="help-text">在“分类管理”中自定义分类。</span></label>${id ? `<div class="form-field"><span class="field-label">当前收纳位置</span><div class="form-control static-field">${esc(item.location_path)}</div></div>` : `<label class="form-field"><span class="field-label">收纳位置 *</span><select name="location_id" class="form-control" required>${locationOptions(item.location_id)}</select></label>`}${id ? `<div class="form-field"><span class="field-label">当前库存</span><div class="form-control static-field">${number(item.quantity)} ${esc(item.unit)} · ${esc(STATUS[item.status])}</div></div>` : field('数量 *', 'quantity', item.quantity, 'number', 'required min="0" max="1000000000" step="1"')}${field('计量单位', 'unit', item.unit, 'text', 'required maxlength="30" placeholder="件、包、瓶…"')}${field('品牌', 'brand', item.brand, 'text', 'maxlength="120"')}${field('型号 / 规格', 'model', item.model, 'text', 'maxlength="120"')}${field('入库日期 *', 'stored_date', item.stored_date, 'date', 'required')}${field('购买日期', 'purchase_date', item.purchase_date, 'date')}${field('购买价格（元）', 'purchase_price', item.purchase_price ?? '', 'number', 'min="0" step="0.01"')}${field('有效期至', 'expires_on', item.expires_on, 'date')}${field('提前提醒（天）', 'warning_days', item.warning_days, 'number', 'required min="0" max="3650" step="1"')}${field('标签', 'tags', item.tags, 'text', 'maxlength="500" placeholder="用逗号分隔，例如：露营,常用"')}<label class="form-field span-2"><span class="field-label">备注</span><textarea class="form-control" name="notes" rows="3" maxlength="4000" placeholder="配件、注意事项，或你想记住的小细节…">${esc(item.notes)}</textarea></label></div><p class="error-banner form-error" hidden role="alert"></p></div><div class="modal-footer"><button type="button" class="button secondary" data-action="close-modal">取消</button><button type="submit" class="button primary">${icon('check')} ${id ? '保存档案' : '登记物品'}</button></div></form>`);
    refreshItemIcon($('item-form'));
  } catch (error) { toast(error.message, true); }
}
async function submitItem(form) {
  const data = new FormData(form);
  const payload = {};
  ['name', 'unit', 'brand', 'model', 'tags', 'notes', 'stored_date'].forEach(key => payload[key] = String(data.get(key) || '').trim());
  ['purchase_date', 'expires_on'].forEach(key => payload[key] = data.get(key) || null);
  payload.purchase_price = data.get('purchase_price') === '' ? null : Number(data.get('purchase_price'));
  payload.category_id = Number(data.get('category_id'));
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
    const actions = archived(item) ? [] : {in_stock: ['take_out', 'move', 'lend', 'consume', 'restock', 'discard', 'lost'], in_use: ['return', 'lend', 'discard', 'lost'], lent: ['return', 'discard', 'lost'], depleted: ['restock'], discarded: [], lost: []}[item.status] || [];
    openModal('detail-modal', `${modalHeader('物品详情', 'detail-modal-title')}<div class="modal-body"><div class="detail-hero"><div class="detail-image">${itemVisual(item)}</div><div class="detail-info"><span class="eyebrow">${esc(item.category)}</span><h3 class="detail-title">${esc(item.name)}</h3><div class="detail-badges">${badge(item.status)}${archiveBadge(item)}${expiryBadge(item)}</div><p class="item-path">${icon('pin')}${esc(item.location_path)}</p><div class="detail-quantity"><strong>${number(item.quantity)}</strong> ${esc(item.unit)}</div></div></div><div class="detail-grid">${datum('品牌 / 型号', [item.brand, item.model].filter(Boolean).join(' / '))}${datum('入库日期', date(item.stored_date))}${datum('购买日期', date(item.purchase_date))}${datum('购买价格', item.purchase_price == null ? '' : `¥ ${number(item.purchase_price)}`)}${datum('有效期至', date(item.expires_on))}${datum('提前提醒', `${item.warning_days} 天`)}${datum('标签', item.tags)}${datum('档案更新', date(item.updated_at, true))}${archived(item) ? datum('归档时间', date(item.archived_at, true)) : ''}</div>${item.notes ? `<div class="detail-notes"><span class="detail-label">备注</span><p>${esc(item.notes)}</p></div>` : ''}${actions.length ? `<h3 class="section-title">物品操作</h3><div class="action-grid">${actions.map(action => `<button class="button ${['discard', 'lost'].includes(action) ? 'subtle action-muted' : 'secondary'}" data-action="item-action" data-value="${action}" data-id="${item.id}">${icon(action === 'move' ? 'pin' : action === 'discard' ? 'trash' : action === 'restock' ? 'plus' : action === 'lost' ? 'alert' : 'refresh')}${esc(ACTION[action])}</button>`).join('')}</div><p class="help-text">取出、归还与借出会更新整件物品的状态；数量变化请使用消耗或补充。</p>` : `<p class="help-text">${archived(item) ? '物品已归档，保留库存、位置和历史。恢复后可继续取放。' : '当前状态没有可用的库存操作。档案和历史仍然保留。'}</p>`}<h3 class="section-title">操作记录</h3><div class="detail-logs">${logs.length ? logs.map(log => logRow(log, true)).join('') : '<p class="muted">暂无记录</p>'}</div></div><div class="modal-footer"><button class="button secondary" data-action="close-modal">关闭</button><button class="button secondary" data-action="${archived(item) ? 'restore-item' : 'archive-item'}" data-id="${item.id}">${icon(archived(item) ? 'refresh' : 'package')} ${archived(item) ? '恢复物品' : '归档物品'}</button><button class="button primary" data-action="edit-item" data-id="${item.id}">${icon('edit')} 编辑档案</button></div>`);
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
  const children = locationAdjacency(), stack = id ? [id] : [];
  while (stack.length) {
    for (const child of children.get(stack.pop()) || []) {
      if (!descendants.has(child.id)) { descendants.add(child.id); stack.push(child.id); }
    }
  }
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
function renderCategories() {
  $('page-view').innerHTML = `<section class="panel category-panel"><div class="panel-header"><div><h2 class="panel-title">${icon('tags')} 我的分类</h2><p class="panel-subtitle">名称随时可改，已有物品会继续关联同一个分类。</p></div><button class="button primary" data-action="add-category">${icon('plus')} 新增分类</button></div><div class="panel-body category-list">${state.categories.length ? state.categories.map(cat => `<div class="category-row"><div class="category-info"><strong>${esc(cat.name)}</strong><span>排序 ${number(cat.sort_order)} · ${number(cat.item_count)} 件档案${cat.active ? '' : ' · 已停用'}</span></div><div class="category-actions"><button class="button secondary button-small" data-action="edit-category" data-id="${cat.id}">${icon('edit')} 编辑</button><button class="button subtle button-small" data-action="toggle-category" data-id="${cat.id}" aria-label="${cat.active ? '停用' : '启用'}分类 ${esc(cat.name)}">${cat.active ? '停用' : '启用'}</button><button class="icon-button" data-action="delete-category" data-id="${cat.id}" title="删除分类" aria-label="删除分类 ${esc(cat.name)}">${icon('trash')}</button></div></div>`).join('') : empty('分类由你来定', '创建适合自己家的分类，例如：健身器材、露营装备。', 'add-category', '新增分类', 'tags')}</div></section><p class="help-text">排序数值越小越靠前。停用后无法用于新物品，已有档案继续保留；关联物品（含归档物品）的分类不能删除。</p>`;
}
function openCategoryForm(id = null) {
  const category = state.categories.find(cat => cat.id === id) || {name: '', sort_order: state.categories.length ? Math.max(...state.categories.map(cat => Number(cat.sort_order))) + 10 : 0};
  openModal('category-modal', `<form id="category-form" data-id="${id || ''}">${modalHeader(id ? '编辑分类' : '新增分类', 'category-modal-title')}<div class="modal-body"><div class="form-grid">${field('分类名称 *', 'name', category.name, 'text', 'required maxlength="80" placeholder="例如：健身器材"', true)}${field('排序', 'sort_order', category.sort_order, 'number', 'required min="-100000" max="100000" step="1"')}</div><p class="help-text">改名后已有物品会显示新名称，历史记录仍然保留。</p><p class="error-banner form-error" hidden role="alert"></p></div><div class="modal-footer"><button class="button secondary" type="button" data-action="close-modal">取消</button><button class="button primary" type="submit">保存分类</button></div></form>`);
}
async function submitCategory(form) {
  const data = new FormData(form);
  await api(form.dataset.id ? `/api/categories/${form.dataset.id}` : '/api/categories', {method: form.dataset.id ? 'PATCH' : 'POST', body: JSON.stringify({name: String(data.get('name') || '').trim(), sort_order: Number(data.get('sort_order'))})});
  $('category-modal').close(); toast('分类已保存。'); await refreshAfterMutation();
}
function confirmDeleteCategory(id) {
  const category = state.categories.find(cat => cat.id === id); if (!category) return;
  openModal('category-modal', `<form id="delete-category-form" data-id="${id}">${modalHeader('删除分类', 'category-modal-title')}<div class="modal-body"><p>确认删除“${esc(category.name)}”？</p><p class="help-text">${category.item_count ? `此分类关联 ${number(category.item_count)} 件物品，请先修改这些物品的分类再删除。` : '删除后无法恢复。已有物品不会被删除。'}</p><p class="error-banner form-error" hidden role="alert"></p></div><div class="modal-footer"><button class="button secondary" type="button" data-action="close-modal">取消</button><button class="button danger" type="submit"${category.item_count ? ' disabled' : ''}>确认删除</button></div></form>`);
}
async function confirmArchive(id, restoring = false) {
  try {
    const item = await api(`/api/items/${id}`);
    openModal('action-modal', `<form id="archive-form" data-id="${id}" data-restoring="${restoring}">${modalHeader(restoring ? '恢复物品' : '归档物品', 'action-modal-title')}<div class="modal-body"><p class="action-description">${restoring ? '恢复后，物品重新出现在普通库存中，并恢复对应的到期提醒。' : '归档后，物品从普通库存和到期提醒中隐藏。照片、数量、位置及历史记录保留，可以随时恢复。'}</p><div class="archive-preview"><strong>${esc(item.name)}</strong><span>物品 #${item.id} · ${number(item.quantity)} ${esc(item.unit)} · ${esc(STATUS[item.status])}</span><span>${esc(item.location_path)}</span></div><label class="form-field"><span class="field-label">${restoring ? '恢复备注' : '归档原因'}</span><textarea class="form-control" name="note" rows="2" maxlength="2000" placeholder="可选，记录这次操作的原因"></textarea></label><p class="help-text">点击下方按钮才会${restoring ? '恢复' : '归档'}这一件物品。</p><p class="error-banner form-error" hidden role="alert"></p></div><div class="modal-footer"><button class="button secondary" type="button" data-action="close-modal">取消</button><button class="button primary" type="submit">确认${restoring ? '恢复' : '归档'}</button></div></form>`);
  } catch (error) { toast(error.message, true); }
}
async function submitArchive(form) {
  const restoring = form.dataset.restoring === 'true';
  const item = await post(`/api/items/${form.dataset.id}/${restoring ? 'restore' : 'archive'}`, {confirmed: true, note: String(new FormData(form).get('note') || '').trim()});
  $('action-modal').close(); toast(restoring ? '物品已恢复。' : '物品已归档，可在“已归档”筛选中查看。'); await refreshAfterMutation();
  if ($('detail-modal').open) await openDetail(item.id);
}
const DRAFT_LABELS = {name: '物品名称', category: '分类', location_path: '目标位置', quantity: '数量', unit: '单位', brand: '品牌', model: '型号', stored_date: '入库日期', purchase_date: '购买日期', purchase_price: '购买价格（元）', expires_on: '有效期至', warning_days: '提前提醒（天）', tags: '标签', notes: '物品备注', note: '操作备注', status: '状态'};
function draftFields(draft) {
  const payload = draft.payload || {};
  return Object.entries(payload).filter(([key, value]) => DRAFT_LABELS[key] && value !== null && value !== '').map(([key, value]) => `<div><dt>${esc(key === 'quantity' && draft.action === 'restock' ? '补充数量' : DRAFT_LABELS[key])}</dt><dd>${esc(Array.isArray(value) ? value.join('、') : value)}</dd></div>`).join('');
}
function aiItemCard(item) {
  return `<button type="button" class="ai-item-result" data-action="detail" data-id="${Number(item.id)}"><span class="ai-item-visual">${itemVisual(item)}</span><span class="ai-item-copy"><strong>${esc(item.name)}</strong><span>物品 #${number(item.id)} · ${number(item.quantity)} ${esc(item.unit)} · ${esc(STATUS[item.status] || item.status)}${archived(item) ? ' · 已归档' : ''}</span><span>${esc(item.location_path)}</span></span>${icon('chevron')}</button>`;
}
function aiDraftCard(draft) {
  const titles = {create: '待确认登记', restock: '待确认补充', move: '待确认移库', archive: '待确认归档'};
  const expired = new Date(draft.expires_at).getTime() <= Date.now();
  const done = draft.state === 'confirmed' || draft.state === 'cancelled';
  return `<div class="ai-draft${done ? ' ai-draft-done' : ''}"><div class="ai-draft-heading"><strong>${esc(titles[draft.action] || '待确认操作')}</strong><span>${done ? draft.state === 'confirmed' ? '已执行' : '已取消' : expired ? '已过期' : '尚未写入'}</span></div><p class="ai-plain">${esc(draft.summary)}</p>${(draft.items || []).map(aiItemCard).join('')}<dl class="ai-draft-fields">${draftFields(draft)}</dl>${draft.action === 'archive' ? '<p class="help-text">仅归档上方列出的物品。数量、位置和历史保留，之后可以恢复。</p>' : ''}${!done ? `<p class="help-text">${expired ? '草稿已过期，请重新向助手描述需求。' : '请检查这些信息。点击确认后才会写入，草稿 15 分钟内有效。'}</p><div class="ai-draft-actions"><button type="button" class="button primary button-small" data-action="ai-confirm" data-draft="${esc(draft.id)}"${expired || draft.pending ? ' disabled' : ''}>${draft.pending ? '正在处理…' : `确认${draft.action === 'archive' ? '归档' : draft.action === 'move' ? '移库' : draft.action === 'restock' ? '补充' : '登记'}`}</button><button type="button" class="button secondary button-small" data-action="ai-cancel" data-draft="${esc(draft.id)}"${draft.pending ? ' disabled' : ''}>取消</button></div>` : ''}</div>`;
}
function aiMessages() {
  if (!state.ai.messages.length) return '<div class="ai-welcome"><strong>告诉我你收好了什么，或想找什么。</strong><p>例如：“我把两根数据线放进书房工具箱。”<br>或：“我的打气筒在哪里？”</p></div>';
  return state.ai.messages.map(message => `<div class="ai-message ai-message-${message.role === 'user' ? 'user' : 'assistant'}"><span class="ai-speaker">${message.role === 'user' ? '你' : '收纳助手'}</span><p class="ai-plain">${esc(message.content)}</p>${message.matches?.length ? `<div class="ai-matches">${message.matches.map(aiItemCard).join('')}</div>` : ''}${message.drafts?.map(aiDraftCard).join('') || ''}</div>`).join('');
}
function aiPanel() {
  const enabled = Boolean(state.ai.status?.enabled);
  const recording = state.ai.recording === true;
  const waiting = state.ai.busy || state.ai.transcribing || state.ai.recording === 'starting';
  const microphone = window.isSecureContext && navigator.mediaDevices?.getUserMedia && window.MediaRecorder;
  const voiceAvailable = enabled && state.ai.status?.voice_enabled && microphone;
  const voiceHint = !window.isSecureContext ? '手机录音需要 HTTPS。当前地址可用手机键盘的语音输入，转成文字后发送。' : !microphone ? '当前浏览器不支持网页录音，请使用手机键盘的语音输入。' : !state.ai.status?.voice_enabled ? '配置语音识别模型后可使用录音；现在可以打字或用手机键盘语音输入。' : '录音后先核对识别文字，再点击发送。最多录音 2 分钟，文件不超过 10 MB。';
  return `<section class="panel ai-panel" id="ai-panel" aria-labelledby="ai-panel-title"><div class="panel-header"><div><h2 class="panel-title" id="ai-panel-title">${icon('chat')} AI 收纳助手</h2><p class="panel-subtitle">问位置、记物品。每次写入，都由你确认。</p></div><button class="button subtle button-small" type="button" data-action="${enabled ? 'ai-help' : 'ai-settings'}">${enabled ? '接入与隐私' : '配置助手'}</button></div><div class="ai-panel-body">${!enabled ? '<div class="ai-config-notice"><strong>助手尚未启用</strong><span>打开“设置 → AI 与语音”，填写 API 和模型，保存后立即生效。</span></div>' : `<p class="ai-connection"><span class="connection-dot"></span> 已配置 · ${esc(state.ai.status.model || 'AI 模型')}</p>`}<div class="ai-messages" id="ai-messages" role="log" aria-label="与收纳助手的对话" aria-live="polite">${aiMessages()}</div>${waiting || recording ? `<p class="ai-progress" role="status">${recording ? `${icon('mic')} 正在录音，点击“停止并识别”结束…` : state.ai.transcribing ? '正在识别语音，完成后请核对文字…' : state.ai.recording === 'starting' ? '正在请求麦克风权限…' : '助手正在查询和整理…'}</p>` : ''}<form id="ai-form"><label class="screen-reader" for="ai-message">给 AI 收纳助手的消息</label><textarea class="form-control ai-input" id="ai-message" name="message" rows="2" maxlength="4000" placeholder="说说物品名称、数量和位置，或者问我东西在哪里…"${!enabled || waiting || recording ? ' disabled' : ''}>${esc(state.ai.input)}</textarea><div class="ai-compose-actions"><button class="button secondary${recording ? ' is-recording' : ''}" type="button" data-action="ai-record" aria-pressed="${recording}"${(!voiceAvailable || waiting) && !recording ? ' disabled' : ''}>${icon('mic')}${recording ? '停止并识别' : '语音输入'}</button><button class="button primary" type="submit"${!enabled || waiting || recording || !state.ai.input.trim() ? ' disabled' : ''}>${icon('arrow')} 发送</button></div></form><p class="help-text ai-voice-hint">${esc(voiceHint)}</p><p class="help-text ai-privacy-note">AI 接口会接收本次对话、录音及完成任务所需的档案信息；密钥留在 NAS 后端。助手只能读取档案和提交待确认操作，不能修改程序。</p></div></section>`;
}
function renderAI(focus = false) {
  renderAISettings();
  if (state.route !== 'dashboard' || !$('ai-panel')) return;
  const previousScroll = $('ai-messages')?.scrollTop || 0;
  const previousCount = $('ai-messages')?.querySelectorAll('.ai-message').length || 0;
  cancelMotion($('ai-panel'));
  $('ai-panel').outerHTML = aiPanel();
  const messages = $('ai-messages'); if (messages) messages.scrollTop = focus ? messages.scrollHeight : previousScroll;
  if (messages) enterCollection([...messages.querySelectorAll('.ai-message')].slice(previousCount), true);
  if (focus && !state.ai.busy && !state.ai.recording && !state.ai.transcribing) $('ai-message')?.focus({preventScroll: true});
}
function openAIHelp() {
  openModal('ai-help-modal', `${modalHeader('AI 接入与使用', 'ai-help-modal-title')}<div class="modal-body ai-help"><p>打开“设置 → AI 与语音”，填写供应商的聊天配置，保存后立即生效，无需重建容器。</p><dl><div><dt>聊天 API 地址</dt><dd>你的 OpenAI 兼容 API 地址，通常以 /v1 结尾</dd></div><div><dt>聊天 API 密钥</dt><dd>供应商提供的密钥，保存在 NAS，保存后不会在网页显示</dd></div><div><dt>聊天模型名称</dt><dd>需要支持工具调用，以供应商文档为准</dd></div><div><dt>语音识别模型名称</dt><dd>可选，配置后启用录音；留空使用文字聊天</dd></div></dl><p class="help-text">语音可单独配置地址和密钥。首次不填写时使用聊天配置，已有独立密钥留空会保留；移除密钥需要勾选后保存。</p><h3>手机语音</h3><p>网页调用麦克风需要 HTTPS 和浏览器录音支持。可以为飞牛 NAS 配置 HTTPS 反向代理；在 HTTP 页面，使用手机键盘自带的语音输入也可以。</p><h3>每次写入由你确认</h3><p>登记、补充、移库和归档只生成草稿。检查列出的具体物品和信息，点击“确认”才保存；取消不会改变数据。助手不提供删除、代码修改、数据库命令或 NAS 操作权限。</p><h3>发送哪些信息</h3><p>对话、录音以及本次查询所需的物品和位置数据会发送到你配置的 API 服务。聊天只保存在当前页面内存中，退出登录或刷新页面会清空。</p></div><div class="modal-footer"><button class="button secondary" data-action="close-modal">知道了</button><button class="button primary" data-action="ai-settings">打开 AI 配置</button></div>`);
}
async function submitAI(form) {
  const message = String(new FormData(form).get('message') || '').trim();
  if (!message || state.ai.busy || !state.ai.status?.enabled) return;
  const generation = state.ai.generation;
  const history = []; let remaining = 20000 - message.length;
  for (const entry of state.ai.messages.filter(value => ['user', 'assistant'].includes(value.role)).slice(-12).reverse()) {
    const content = String(entry.content).slice(0, Math.min(3000, remaining));
    if (!content) break; history.unshift({role: entry.role, content}); remaining -= content.length;
  }
  state.ai.messages.push({role: 'user', content: message}); state.ai.input = ''; state.ai.busy = true; renderAI(true);
  try {
    const response = await post('/api/ai/chat', {message, history});
    if (generation !== state.ai.generation) return;
    state.ai.messages.push({role: 'assistant', content: response.reply || '已整理本次信息，请查看下方结果。', matches: Array.isArray(response.matches) ? response.matches : [], drafts: Array.isArray(response.drafts) ? response.drafts : []});
  } catch (error) { if (generation === state.ai.generation) state.ai.messages.push({role: 'assistant', content: error.message}); }
  finally { if (generation === state.ai.generation) { state.ai.busy = false; renderAI(true); } }
}
async function handleAIDraft(id, confirm) {
  const draft = state.ai.messages.flatMap(message => message.drafts || []).find(value => value.id === id);
  if (!draft || draft.pending || draft.state) return;
  const generation = state.ai.generation;
  draft.pending = true; renderAI();
  try {
    const result = await post(`/api/ai/drafts/${encodeURIComponent(id)}/${confirm ? 'confirm' : 'cancel'}`, confirm ? {confirmed: true} : {});
    if (generation !== state.ai.generation) return;
    draft.state = confirm ? 'confirmed' : 'cancelled';
    if (confirm) { state.ai.messages.push({role: 'assistant', content: result.message || '已按你确认的信息保存。', matches: result.items || []}); await refreshAfterMutation(); }
    else toast('草稿已取消，物品信息没有改变。');
  } catch (error) { if (generation === state.ai.generation) { toast(error.message, true); state.ai.messages.push({role: 'assistant', content: error.message}); } }
  finally { draft.pending = false; if (generation === state.ai.generation) renderAI(true); }
}
let aiRecorder = null;
let aiRecordingTimer = null;
let aiRecordingCancelled = false;
function stopRecording(cancel = false) {
  aiRecordingCancelled = aiRecordingCancelled || cancel;
  if (aiRecorder?.state === 'recording') aiRecorder.stop();
  else if (cancel) state.ai.recording = false;
}
async function toggleRecording() {
  if (state.ai.recording === true) { stopRecording(); return; }
  if (state.ai.recording || state.ai.busy || state.ai.transcribing) return;
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { toast('网页录音需要 HTTPS。请先使用手机键盘的语音输入。', true); return; }
  if (!state.ai.status?.voice_enabled) { openAIHelp(); return; }
  const generation = state.ai.generation;
  state.ai.recording = 'starting'; aiRecordingCancelled = false; renderAI();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: true});
    if (generation !== state.ai.generation || state.route !== 'dashboard' || aiRecordingCancelled || $('app-shell').hidden) { stream.getTracks().forEach(track => track.stop()); state.ai.recording = false; return; }
    const type = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm', 'audio/ogg;codecs=opus'].find(value => MediaRecorder.isTypeSupported(value));
    const recorder = new MediaRecorder(stream, type ? {mimeType: type} : undefined); aiRecorder = recorder;
    const chunks = []; let size = 0; let oversized = false;
    recorder.addEventListener('dataavailable', event => {
      if (!event.data.size) return;
      size += event.data.size;
      if (size > 10 * 1024 * 1024) { oversized = true; stopRecording(true); return; }
      chunks.push(event.data);
    });
    recorder.addEventListener('error', () => { aiRecordingCancelled = true; toast('录音中断，请重新录音或使用键盘语音输入。', true); stopRecording(true); });
    recorder.addEventListener('stop', async () => {
      clearTimeout(aiRecordingTimer); aiRecordingTimer = null; stream.getTracks().forEach(track => track.stop()); aiRecorder = null; state.ai.recording = false;
      if (oversized) toast('录音超过 10 MB，已取消，请缩短录音再试。', true);
      if (generation !== state.ai.generation || aiRecordingCancelled || !chunks.length) { renderAI(); return; }
      state.ai.transcribing = true; renderAI();
      try {
        const mime = recorder.mimeType || chunks[0].type || 'audio/webm'; const extension = mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm';
        const body = new FormData(); body.append('file', new Blob(chunks, {type: mime}), `recording.${extension}`);
        const result = await api('/api/ai/transcribe', {method: 'POST', body});
        if (generation !== state.ai.generation) return;
        const transcript = String(result.text || '').trim(); if (!transcript) throw new Error('没有识别到文字，请重新录音。');
        const combined = `${state.ai.input}${state.ai.input ? '\n' : ''}${transcript}`;
        state.ai.input = combined.slice(0, 4000);
        toast(combined.length > 4000 ? '识别文字超过 4000 字，已截断。请检查并分次发送。' : '已识别语音。请核对或修改文字，再点击发送。');
      } catch (error) { if (generation === state.ai.generation) toast(error.message, true); }
      finally { if (generation === state.ai.generation) { state.ai.transcribing = false; renderAI(true); } }
    });
    recorder.start(1000); state.ai.recording = true; aiRecordingTimer = setTimeout(() => stopRecording(), 120000); renderAI();
  } catch (error) { stream?.getTracks().forEach(track => track.stop()); state.ai.recording = false; toast(error.name === 'NotAllowedError' ? '麦克风权限未开启。请允许录音，或使用手机键盘语音输入。' : '无法开始录音，请检查麦克风或使用手机键盘语音输入。', true); renderAI(); }
}

function showLogin() { clearAISettings(); clearTimeout(updatePollTimer); updatePollTimer = null; state.updates.generation++; state.updates.status = null; state.updates.preference = null; state.updates.polling = false; state.updates.busy = false; state.updates.inFlight = false; state.updates.offline = false; state.updates.error = ''; state.updates.savedMessage = ''; state.updates.needsReload = false; state.updates.reauthenticate = false; state.updates.disconnectedSince = null; $('update-notice').hidden = true; stopRecording(true); state.ai.generation++; state.ai.busy = false; state.ai.transcribing = false; state.ai.messages = []; state.ai.input = ''; document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close()); $('app-shell').hidden = true; $('login-view').hidden = false; document.querySelector('#login-form input').focus(); }
async function bootstrap() {
  try {
    const auth = await api('/api/auth'); state.authRequired = auth.auth_required;
    if (!auth.authenticated) { showLogin(); return; }
    $('app-shell').hidden = false; $('login-view').hidden = true;
    const [meta, locations, categories, aiStatus] = await Promise.all([api('/api/meta'), api('/api/locations'), api('/api/categories'), api('/api/ai/status').catch(() => ({enabled: false, voice_enabled: false}))]);
    state.categories = categories; state.timezone = meta.timezone; if (!state.appVersion) state.appVersion = meta.version || ''; state.locations = locations; state.ai.status = aiStatus;
    const deepLink = new URLSearchParams(location.search).get('location');
    if (deepLink && /^\d+$/.test(deepLink)) { state.filters.location_id = deepLink; state.selectedLocation = Number(deepLink); state.route = 'items'; history.replaceState(null, '', `${location.pathname}?location=${encodeURIComponent(deepLink)}#items`); }
    else state.route = ROUTES[location.hash.slice(1)] ? location.hash.slice(1) : 'dashboard';
    await refreshPage();
    if (state.route !== 'settings') await loadUpdateStatus();
  } catch (error) { showError(error); setNavigation(); $('page-view').innerHTML = empty('暂时无法连接家庭档案', '请检查服务是否启动，再重新连接。', 'retry', '重新连接', 'refresh'); }
}
window.addEventListener('hashchange', () => { if (state.ai.recording) stopRecording(true); state.route = ROUTES[location.hash.slice(1)] ? location.hash.slice(1) : 'dashboard'; if ($('app-shell').hidden) return; window.scrollTo({top: 0, left: 0, behavior: 'instant'}); $('main-content').focus({preventScroll: true}); refreshPage(); scheduleUpdatePoll(); });
document.addEventListener('click', async event => {
  const target = event.target.closest('[data-action]'); if (!target || target.disabled) return;
  const action = target.dataset.action; const id = Number(target.dataset.id);
  if (action === 'focus-main') { ($('app-shell').hidden ? document.querySelector('#login-form input') : $('main-content')).focus(); return; }
  if (action === 'browse-items' || action === 'stat-filter') {
    const value = target.dataset.value || 'all';
    if (value === 'locations') { navigate('locations'); return; }
    if (value === 'attention') { state.expiry = state.dashboard?.expired_count ? 'expired' : 'soon'; navigate('expiry'); return; }
    state.filters = {q: '', category: '', category_id: '', status: value === 'in_use' ? 'in_use' : '', location_id: '', archived: 'false'};
    history.replaceState(null, '', `${location.pathname}${location.hash}`);
    navigate('items'); return;
  }
  if (action === 'inventory-view') {
    if (!['grid', 'list'].includes(target.dataset.value) || state.route !== 'items') return;
    state.inventory.view = target.dataset.value; state.inventory.visible = 48; rememberInventoryView(); renderItems(state.items);
    document.querySelector(`.view-switch [data-value="${state.inventory.view}"]`)?.focus({preventScroll: true}); return;
  }
  if (action === 'inventory-more') {
    if (state.route !== 'items') return;
    const previousVisible = state.inventory.visible;
    state.inventory.visible += 48; renderItems(state.items);
    enterCollection([...document.querySelectorAll('.item-grid .item-card')].slice(previousVisible), true);
    document.querySelectorAll('.item-grid .item-card')[previousVisible]?.focus({preventScroll: true}); return;
  }
  if (action === 'close-modal') { target.closest('dialog')?.close(); return; }
  if (action === 'update-settings' || action === 'ai-settings') { target.closest('dialog')?.close(); navigate('settings'); return; }
  if (action === 'ai-settings-retry') { await loadAISettings(); return; }
  if (action === 'update-check') { await checkUpdates(); return; }
  if (action === 'update-reconnect') { await loadUpdateStatus(); renderSettings(); return; }
  if (action === 'update-install') { confirmUpdateInstall(); return; }
  if (action === 'update-reload') { location.reload(); return; }
  if (action === 'update-login') { showLogin(); return; }
  if (action === 'add-category') { openCategoryForm(); return; }
  if (action === 'edit-category') { openCategoryForm(id); return; }
  if (action === 'delete-category') { confirmDeleteCategory(id); return; }
  if (action === 'toggle-category') { const category = state.categories.find(cat => cat.id === id); if (!category) return; target.disabled = true; try { await api(`/api/categories/${id}`, {method: 'PATCH', body: JSON.stringify({active: !category.active})}); toast(category.active ? '分类已停用，已有物品保留。' : '分类已启用。'); await refreshAfterMutation(); } catch (error) { toast(error.message, true); } finally { target.disabled = false; } return; }
  if (action === 'archive-item') { confirmArchive(id); return; }
  if (action === 'restore-item') { confirmArchive(id, true); return; }
  if (action === 'ai-help') { openAIHelp(); return; }
  if (action === 'ai-record') { toggleRecording(); return; }
  if (action === 'ai-confirm' || action === 'ai-cancel') { handleAIDraft(target.dataset.draft, action === 'ai-confirm'); return; }
  if (action === 'add-item') { openItemForm(); return; }
  if (action === 'edit-item') { openItemForm(id); return; }
  if (action === 'detail') { openDetail(id); return; }
  if (action === 'item-action') { openAction(id, target.dataset.value); return; }
  if (action === 'add-location') { openLocationForm(null, Number(target.dataset.parent) || null); return; }
  if (action === 'edit-location') { openLocationForm(id); return; }
  if (action === 'delete-location') { confirmDeleteLocation(id); return; }
  if (action === 'select-location') { state.selectedLocation = id; renderLocations(); document.querySelector(`.location-row[data-id="${id}"]`)?.focus({preventScroll: true}); return; }
  if (action === 'view-location-items') { state.filters = {q: '', category: '', category_id: '', status: '', location_id: String(id), archived: 'false'}; navigate('items'); return; }
  if (action === 'qr') { openQr(id); return; }
  if (action === 'print-qr') { window.print(); return; }
  if (action === 'expiry-tab') { state.expiry = target.dataset.value; await refreshPage(); if (state.route === 'expiry') $(`expiry-tab-${state.expiry}`)?.focus({preventScroll: true}); return; }
  if (action === 'clear-filters') { state.filters = {q: '', category: '', category_id: '', status: '', location_id: '', archived: 'false'}; history.replaceState(null, '', `${location.pathname}${location.hash}`); refreshPage(); return; }
  if (action === 'retry') { bootstrap(); return; }
  if (action === 'remove-photo') { const form = target.closest('form'); form.dataset.image = ''; form.elements.photo.value = ''; resetItemPhotoPreview(form); return; }
  if (action === 'export') {
    target.disabled = true;
    try { const response = await fetch('/api/export', {credentials: 'same-origin'}); if (response.status === 401) { showLogin(); throw new Error('请登录后导出数据。'); } if (!response.ok) throw new Error('导出失败，请稍后重试。'); const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `homestorage-${today()}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast('数据已导出。照片请随 NAS 数据目录一起备份。'); } catch (error) { toast(error.message, true); } finally { target.disabled = false; }
    return;
  }
  if (action === 'logout') { try { await post('/api/logout', {}); document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close()); showLogin(); $('login-form').reset(); } catch (error) { toast(error.message, true); } }
});
document.addEventListener('input', event => {
  if (event.target.closest('#item-form') && ['name', 'tags'].includes(event.target.name)) refreshItemIcon(event.target.form);
  if (event.target.closest('#ai-settings-form')) {
    state.aiSettings.message = '';
    if (['api_key', 'transcription_api_key'].includes(event.target.name) && event.target.value) aiSettingsForm().elements[`clear_${event.target.name}`].checked = false;
    renderAISettings(); return;
  }
  if (event.target.id === 'ai-message') { state.ai.input = event.target.value; const send = $('ai-form')?.querySelector('[type="submit"]'); if (send) send.disabled = !state.ai.input.trim() || state.ai.busy; }
});
window.addEventListener('pagehide', () => stopRecording(true));
document.addEventListener('change', event => {
  const target = event.target;
  if (target.id === 'item-sort') {
    if (!['recent', 'name', 'quantity', 'expiry'].includes(target.value) || state.route !== 'items') return;
    state.inventory.sort = target.value; state.inventory.visible = 48; rememberInventoryView(); renderItems(state.items); $('item-sort')?.focus({preventScroll: true}); return;
  }
  if (target.closest('#ai-settings-form')) {
    state.aiSettings.message = '';
    if (target.name.startsWith('clear_') && target.checked) aiSettingsForm().elements[target.name.slice(6)].value = '';
    renderAISettings(); return;
  }
  if (target.closest('#item-form') && target.name === 'category_id') refreshItemIcon(target.form);
  if (target.id === 'auto-update') { state.updates.preference = target.checked; state.updates.savedMessage = ''; renderSettings(); $('auto-update')?.focus({preventScroll: true}); return; }
  if (target.dataset.filter) { state.filters[target.dataset.filter] = target.value; if (target.dataset.filter === 'category_id') state.filters.category = ''; refreshPage(); }
  if (target.id === 'item-photo') {
    const form = target.form, file = target.files[0];
    if (!file) { resetItemPhotoPreview(form); return; }
    if (file.size > 10 * 1024 * 1024) { target.value = ''; resetItemPhotoPreview(form); toast('照片超过 10 MB，请选择较小的文件。', true); return; }
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) { target.value = ''; resetItemPhotoPreview(form); toast('请选择 JPG、PNG 或 WebP 照片。', true); return; }
    releaseItemPhotoPreview();
    const objectUrl = URL.createObjectURL(file); itemPhotoPreviewUrl = objectUrl;
    const img = document.createElement('img'); img.alt = '待上传的物品照片'; img.setAttribute('data-item-photo', '');
    img.onload = () => { URL.revokeObjectURL(objectUrl); if (itemPhotoPreviewUrl === objectUrl) itemPhotoPreviewUrl = null; };
    img.onerror = () => { URL.revokeObjectURL(objectUrl); if (!img.isConnected) return; target.value = ''; resetItemPhotoPreview(form); toast('这张照片无法读取，请重新选择。', true); };
    $('image-preview').innerHTML = itemVisual({...itemFormVisual(form), image_url: ''});
    $('image-preview').querySelector('.item-visual').append(img); img.src = objectUrl;
    photoControls(form, true);
  }
});
document.addEventListener('submit', async event => {
  const form = event.target; if (!(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  if (form.id === 'ai-form') { await submitAI(form); return; }
  if (form.id === 'ai-settings-form') { await saveAISettings(form); return; }
  if (form.id === 'update-preference-form') { await saveUpdatePreference(); return; }
  if (form.id === 'update-install-form') { await installUpdate(form); return; }
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
    else if (form.id === 'category-form') await submitCategory(form);
    else if (form.id === 'archive-form') await submitArchive(form);
    else if (form.id === 'delete-category-form') { await api(`/api/categories/${form.dataset.id}`, {method: 'DELETE'}); $('category-modal').close(); toast('分类已删除。'); await refreshAfterMutation(); }
    else if (form.id === 'item-form') await submitItem(form);
    else if (form.id === 'action-form') await submitAction(form);
    else if (form.id === 'location-form') await submitLocation(form);
    else if (form.id === 'delete-location-form') { await api(`/api/locations/${form.dataset.id}`, {method: 'DELETE'}); $('action-modal').close(); state.selectedLocation = null; toast('收纳位置已删除。'); await refreshAfterMutation(); }
  } catch (error) { if (errorElement) { errorElement.textContent = error.message; errorElement.hidden = false; } else toast(error.message, true); }
  finally { form.dataset.saving = 'false'; if (button) button.disabled = false; }
});
document.addEventListener('keydown', event => {
  const tab = event.target.closest('[data-action="expiry-tab"]');
  if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const value = event.key === 'Home' ? 'soon' : event.key === 'End' ? 'expired' : tab.dataset.value === 'soon' ? 'expired' : 'soon';
  $(`expiry-tab-${value}`)?.click();
});
document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('close', () => { cancelMotion(dialog); if (dialog.id === 'item-modal') releaseItemPhotoPreview(); });
  dialog.addEventListener('click', event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } });
});
let refreshingReminders = false;
async function refreshReminders() {
  if (refreshingReminders || document.visibilityState !== 'visible' || $('app-shell').hidden ||
      !['dashboard', 'expiry'].includes(state.route) || document.querySelector('dialog[open]') || state.ai.busy || state.ai.recording || state.ai.transcribing || document.activeElement?.id === 'ai-message') return;
  refreshingReminders = true;
  try { await refreshPage(false); }
  finally { refreshingReminders = false; }
}
// Recalculate visible reminders across midnight without disrupting an open form.
setInterval(refreshReminders, 60000);
document.addEventListener('visibilitychange', refreshReminders);
bootstrap();
