'use strict';
// Household tools remain independent of the inventory renderer. Credentials live
// only in password inputs; a failing tool never prevents reading inventory.
window.HSV3 = (() => {
  const roles = {admin: '管理员', member: '家庭成员', viewer: '只读成员'};
  const writes = new Set(['add-item', 'edit-item', 'item-action', 'archive-item', 'restore-item', 'add-location', 'edit-location', 'delete-location', 'add-category', 'edit-category', 'delete-category', 'toggle-category', 'ai-confirm', 'ai-cancel']);
  const admins = new Set(['ai-settings', 'ai-settings-retry', 'update-settings', 'update-check', 'update-reconnect', 'update-install', 'update-login']);
  const forms = new Set(['item-form', 'action-form', 'archive-form', 'location-form', 'delete-location-form', 'category-form', 'delete-category-form']);
  const adminForms = new Set(['ai-settings-form', 'update-preference-form', 'update-proxy-form', 'update-install-form']);
  const isAdmin = () => state.user?.role === 'admin' || (!state.authRequired && !state.user);
  const canWrite = () => isAdmin() || state.user?.role === 'member';
  let generation = 0, timer = null, analytics = null, metric = 'movements', scan = null, csv = null, scanLookup = 0, csvPreview = 0;
  const el = id => document.getElementById(id);
  const bytes = value => Number(value) > 1048576 ? `${(Number(value) / 1048576).toFixed(1)} MB` : `${(Number(value || 0) / 1024).toFixed(1)} KB`;
  // randomUUID is restricted to secure contexts, while NAS users often open a
  // private HTTP address. getRandomValues remains available in that situation.
  const requestId = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : [...crypto.getRandomValues(new Uint8Array(16))].map(value=>value.toString(16).padStart(2,'0')).join('');
  function allowedAction(action) { return (!writes.has(action) || canWrite()) && (!admins.has(action) || isAdmin()); }
  function allowedForm(id) { return (!forms.has(id) || canWrite()) && (!adminForms.has(id) || isAdmin()); }
  function permissions(root = document) {
    root.querySelectorAll('[data-action]').forEach(node => {
      if (!allowedAction(node.dataset.action)) { node.dataset.permissionHidden = 'true'; node.hidden = true; }
      else if (node.dataset.permissionHidden === 'true') { delete node.dataset.permissionHidden; node.hidden = node.id === 'add-item-button' && state.route === 'settings'; }
    });
    root.querySelectorAll('[data-permission]').forEach(node => { node.hidden = node.dataset.permission === 'admin' ? !isAdmin() : !canWrite(); });
    document.body.dataset.role = state.user?.role || (isAdmin() ? 'admin' : 'viewer');
  }
  function clear() { generation++; clearTimeout(timer); timer = null; stopCamera(); scan = null; csv = null; analytics = null; const modal = el('v3-modal'); if (modal) { if (modal.open) modal.close(); modal.replaceChildren(); } }
  function pageChanging() { /* Tool dialogs intentionally survive inventory refresh. */ }
  function stopCamera() { window.HomeStorageScanner?.stop(); if (el('scanner-video')) el('scanner-video').hidden = true; }
  function errorHTML(message) { return `<p class="error-banner" role="alert">${esc(message)}</p>`; }
  function failure(error) { const mount = el('v3-error'); if (mount) { mount.textContent = error.message; mount.hidden = false; } else toast(error.message, true); }
  function panel(title, subtitle, body, actions = '') { return `<section class="panel"><div class="panel-header"><div><h2 class="panel-title">${esc(title)}</h2><p class="panel-subtitle">${esc(subtitle)}</p></div>${actions}</div><div class="panel-body">${body}</div></section>`; }
  function modal(view, title, subtitle, body) {
    generation++; clearTimeout(timer); stopCamera();
    const dialog = openModal('v3-modal', `${modalHeader(title, 'v3-modal-title', subtitle)}<div class="modal-body v3-body"><p id="v3-error" class="error-banner" role="alert" hidden></p>${body}</div><div class="modal-footer"><button type="button" class="button secondary" data-action="close-modal">关闭</button></div>`);
    dialog.dataset.view = view; return generation;
  }
  const current = token => token === generation && el('v3-modal')?.open;
  function settings() {
    let mount = el('v3-settings-mount');
    if (!mount) { el('page-view').innerHTML = '<div class="settings-layout"><div id="v3-settings-mount"></div></div>'; mount = el('v3-settings-mount'); }
    if (mount.dataset.loaded === 'true') return;
    const adminEntries = [['users','家庭成员','为每个人设置账号与权限','shield'],['backups','备份与网盘','下载完整备份，自动上传 WebDAV','download'],['organize','AI 分类整理','先预览，确认后生效，可安全回档','tags'],['integrations','连接其他 AI','创建可撤销的只读 API 令牌','chat']];
    mount.innerHTML = `<section class="v3-account"><span class="metric-icon metric-blue">${icon('home')}</span><div><strong>${esc(state.user?.display_name || state.user?.username || '本地开发模式')}</strong><p>${esc(roles[state.user?.role] || '管理员')} · ${canWrite() ? isAdmin() ? '可管理家庭数据和系统设置' : '可登记、调动物品；系统设置由管理员管理' : '可查找、扫码查询和查看物品；不能修改数据'}</p></div><button class="button secondary button-small" data-v3="password"${!state.user ? ' hidden' : ''}>修改密码</button></section><div class="v3-management-grid">${isAdmin() ? adminEntries.map(([action,title,sub,symbol])=>`<button class="v3-management-card" data-v3="${action}"><span class="metric-icon metric-blue">${icon(symbol)}</span><strong>${title}</strong><span>${sub}</span>${icon('chevron')}</button>`).join('') : ''}<button class="v3-management-card" data-v3="handover"><span class="metric-icon metric-blue">${icon('history')}</span><strong>本版交接文档</strong><span>查看模型、技术、实现方案和部署说明</span>${icon('chevron')}</button><button class="v3-management-card" data-v3="tools"><span class="metric-icon metric-green">${icon('qr')}</span><strong>扫码与 CSV 工具</strong><span>${canWrite() ? '扫码取放、批量导入和导出' : '扫码查看物品、导出 CSV'}</span>${icon('chevron')}</button></div>`;
    mount.dataset.loaded = 'true';
  }
  function afterPage() {
    permissions();
    if (state.route === 'items' && !el('v3-tools-strip')) {
      const strip = document.createElement('div'); strip.id = 'v3-tools-strip'; strip.className = 'v3-tools-strip';
      strip.innerHTML = `<span>${icon('qr')} 快速取放与批量管理</span><div><button class="button secondary button-small" data-v3="scan">扫码 / 扫码枪</button><button class="button secondary button-small" data-v3="csv">CSV 导入导出</button></div>`;
      el('page-view').prepend(strip);
    }
    if (state.route === 'dashboard') loadAnalytics();
  }
  async function loadAnalytics() {
    let mount = el('activity-panel');
    if (!mount) { mount = document.createElement('section'); mount.className = 'panel activity-panel'; mount.id = 'activity-panel'; const ai = el('ai-panel'); if (ai) ai.after(mount); else el('page-view').append(mount); }
    mount.innerHTML = `<div class="panel-header"><div><h2 class="panel-title">${icon('history')} 家庭活动</h2><p class="panel-subtitle">最近 90 天的物品调动与查找</p></div></div><div class="panel-body"><p class="help-text">正在读取活动…</p></div>`;
    const renderToken = state.renderToken;
    try { const data = await api('/api/analytics/activity?days=90'); if (state.route !== 'dashboard' || renderToken !== state.renderToken) return; analytics = data; drawAnalytics(); }
    catch (error) { if (mount.isConnected && renderToken === state.renderToken) mount.querySelector('.panel-body').innerHTML = `${errorHTML(error.message)}<button class="button secondary button-small" data-v3="activity-retry">重新读取活动</button>`; }
  }
  function drawAnalytics() {
    const mount = el('activity-panel'); if (!mount || !analytics) return;
    const daily = analytics.daily || []; const values = daily.map(d=>Number(d[metric] || 0)); const maximum = Math.max(1,...values); const total = values.reduce((a,b)=>a+b,0);
    const leading = daily.length ? (new Date(`${daily[0].date}T00:00:00Z`).getUTCDay() + 6) % 7 : 0;
    const cells = Array.from({length:leading},()=>'<span class="activity-spacer" aria-hidden="true"></span>').join('') + daily.map(day=> { const count = Number(day[metric] || 0); const level = count ? Math.max(1,Math.ceil(count / maximum * 4)) : 0; const label = `${day.date}：${count} 次${metric === 'movements' ? '物品调动' : '搜索'}`; return `<button type="button" class="activity-cell level-${level}" aria-label="${esc(label)}" title="${esc(label)}" tabindex="${day === daily[0] ? '0' : '-1'}" data-v3="activity-day" data-label="${esc(label)}"></button>`; }).join('');
    const top = metric === 'movements' ? (analytics.top_items || []).slice(0,5) : (analytics.top_queries || []).slice(0,5);
    const sub = metric === 'movements' ? '经常被搜索的物品' : '常用搜索词';
    mount.innerHTML = `<div class="panel-header"><div><h2 class="panel-title">${icon('history')} 家庭活动</h2><p class="panel-subtitle">最近 90 天 · ${esc(state.timezone)} · ${number(total)} 次${metric === 'movements' ? '调动' : '搜索'}</p></div><div class="view-switch" role="group" aria-label="活动类型">${[['movements','物品调动'],['searches','搜索频率']].map(([value,label])=>`<button type="button" class="button subtle button-small" aria-pressed="${metric === value}" data-v3="activity-type" data-value="${value}">${label}</button>`).join('')}</div></div><div class="panel-body"><div class="activity-chart" role="group" aria-label="最近90天每天活动，按上下左右方向键切换日期"><div class="activity-weekdays" aria-hidden="true"><span>一</span><span>三</span><span>五</span><span>日</span></div><div class="activity-grid">${cells}</div></div><div class="activity-caption"><span id="activity-day-label" role="status">点按或聚焦日期查看次数</span><span class="activity-legend">少 ${[0,1,2,3,4].map(n=>`<i class="level-${n}" aria-hidden="true"></i>`).join('')} 多</span></div><div class="activity-top"><h3>${sub}</h3>${top.length ? top.map(row=>metric === 'movements' ? `<button class="activity-top-item" data-action="detail" data-id="${Number(row.id)}"><span>${esc(row.name)}</span><span>${number(row.search_count)} 次查询 ${icon('chevron')}</span></button>` : `<div class="activity-top-item"><span>${esc(row.query)}</span><span>${number(row.count)} 次</span></div>`).join('') : '<p class="help-text">还没有搜索记录。查找物品后会逐渐显示。</p>'}</div><p class="help-text">调动统计取出、归还、移动、借出、消耗和补充等操作次数；搜索统计提交的查找请求。物品查询次数从本版起记录其出现在搜索结果中的次数，不是点击次数。</p></div>`;
  }
  function tools() { modal('tools','扫码与数据工具','扫码识别后由你选择操作并确认；CSV 导入先校验再写入。',`<div class="v3-management-grid"><button class="v3-management-card" data-v3="scan">${icon('qr')}<strong>扫码取放</strong><span>摄像头、扫码枪或手动输入条码 / 二维码</span></button><button class="v3-management-card" data-v3="csv">${icon('download')}<strong>CSV 导入导出</strong><span>下载模板、检查数据、确认导入</span></button></div>`); }
  function openScan(prefill = '') {
    scan = null;
    modal('scan','扫码取放','支持物品二维码和商品条码。识别本身不会改变库存。', `<form id="scan-lookup-form" class="v3-stack"><label class="form-field"><span class="field-label">条码 / 二维码内容</span><input class="form-control" name="code" value="${esc(prefill)}" required maxlength="2048" autocomplete="off" placeholder="扫码枪扫描后回车，或手动输入"></label><div class="v3-button-row"><button class="button primary" type="submit">${icon('search')} 查找物品</button><button class="button secondary" type="button" data-v3="camera-start">${icon('qr')} 开启摄像头</button><button class="button subtle" type="button" data-v3="camera-stop">停止摄像头</button></div><p class="help-text">摄像头需要 HTTPS 或本机 localhost；局域网 HTTP 可使用扫码枪或手动输入。</p><video id="scanner-video" class="scanner-video" playsinline muted hidden></video><p id="camera-status" class="help-text" role="status"></p><p class="error-banner form-error" hidden role="alert"></p></form><div id="scan-result" class="v3-stack"></div>`);
    el('scan-lookup-form').elements.code.focus();
    if (prefill) el('scan-lookup-form').requestSubmit();
  }
  async function cameraStart() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('当前地址不支持摄像头，请使用 HTTPS、扫码枪或手动输入。');
    if (!window.HomeStorageScanner) throw new Error('扫码组件未加载，请刷新页面重试。');
    const token = generation; const video = el('scanner-video'); video.hidden = false; el('camera-status').textContent = '正在请求摄像头权限…';
    await window.HomeStorageScanner.start(video, code=> { if (!current(token)) return; stopCamera(); const form = el('scan-lookup-form'); if (!form) return; form.elements.code.value = code; el('camera-status').textContent = '已识别。请核对物品，再确认操作。'; form.requestSubmit(); }, error=> { if (current(token)) { el('camera-status').textContent = error?.message || '无法识别，请调整距离或手动输入。'; } });
    if (current(token) && !video.hidden) el('camera-status').textContent = '对准条码或二维码，识别后摄像头会自动停止。';
  }
  async function lookupScan(form) {
    const code = form.elements.code.value.trim(); const token = generation, lookup = ++scanLookup;
    scan = null; el('scan-result').replaceChildren();
    const result = await api(`/api/scan/lookup?code=${encodeURIComponent(code)}`); if (!current(token) || lookup !== scanLookup || form.elements.code.value.trim() !== code) return;
    scan = {code,items:result.items || [],requestId:requestId()}; stopCamera();
    if (result.kind === 'location') { const loc = result.location; el('scan-result').innerHTML = `<div class="v3-notice"><strong>${esc(loc.path || loc.name)}</strong><p>已识别收纳位置</p><button class="button secondary" data-action="view-location-items" data-id="${Number(loc.id)}">查看这里的物品</button></div>`; return; }
    if (!scan.items.length) { el('scan-result').innerHTML = `<div class="v3-notice"><strong>还没有登记这个条码</strong><p>编码：${esc(code)}</p>${canWrite() ? '<button class="button primary" data-v3="scan-create">以此条码登记物品</button>' : '<p>请让家庭成员或管理员登记物品。</p>'}</div>`; return; }
    el('scan-result').innerHTML = `<form id="scan-commit-form" class="v3-stack"><label class="form-field"><span class="field-label">识别到的物品</span><select name="item_id" class="form-control">${scan.items.map(item=>option(item.id,`${item.name} · ${item.location_path} · ${item.quantity} ${item.unit}`,scan.items[0].id)).join('')}</select></label><button class="button secondary" type="button" data-v3="scan-detail">查看物品详情</button>${canWrite() ? `<div class="form-grid">${selectField('选择操作','operation',[['inbound','扫入：增加数量'],['outbound','扫出：减少数量'],['take_out','取出：整件标记使用中'],['return','归还：整件标记在库']],'inbound')}${field('数量','quantity',1,'number','required min="1" max="1000000000" step="1"')}</div><p id="scan-operation-hint" class="help-text">扫入增加库存，扫出减少库存；取出和归还只改变整件物品的状态。</p><label class="v3-checkbox"><input name="confirmed" type="checkbox" required>我已核对物品、操作与数量，确认记录本次取放</label><button class="button primary" type="submit">确认执行本次操作</button>` : '<p class="help-text">当前为只读账号，可以查询物品，无法更改库存。</p>'}<p class="error-banner form-error" hidden role="alert"></p></form>`;
  }
  async function commitScan(form) {
    if (!scan || !canWrite()) throw new Error('请先识别物品，并使用有写入权限的账号。');
    const operation = form.elements.operation.value; const token = generation;
    const result = await post('/api/scan/commit',{code:scan.code,item_id:Number(form.elements.item_id.value),operation,quantity:['take_out','return'].includes(operation) ? 1 : Number(form.elements.quantity.value),request_id:scan.requestId,confirmed:form.elements.confirmed.checked});
    if (!current(token)) return;
    el('scan-result').innerHTML = `<div class="v3-notice"><strong>${result.replayed ? '本次操作已经记录，未重复执行' : '本次操作已记录'}</strong><p>${esc(result.item.name)} · ${number(result.item.quantity)} ${esc(result.item.unit)} · ${esc(STATUS[result.item.status] || result.item.status)}</p><button class="button primary" data-v3="scan-next">继续扫描下一件</button><button class="button secondary" data-action="detail" data-id="${Number(result.item.id)}">查看物品</button></div>`;
    scan = null; await refreshAfterMutation();
  }
  function openCSV() {
    csv = null;
    modal('csv','CSV 导入导出','适合批量登记。照片不包含在 CSV 中；完整数据请使用备份功能。',`<div class="v3-button-row"><a class="button secondary" href="/api/transfer/template.csv" download>下载导入模板</a><a class="button secondary" href="/api/transfer/export.csv?include_archived=true" download>导出全部物品 CSV</a></div><p class="help-text">请使用 UTF-8 CSV。分类和位置需预先建立，数量及日期必须有效；按模板填写。相同条码与位置的重复行会提示并跳过。</p>${canWrite() ? `<form id="csv-preview-form" class="v3-stack"><label class="form-field"><span class="field-label">选择 CSV 文件</span><input class="form-control" type="file" name="file" accept=".csv,text/csv" required></label><button class="button primary" type="submit">校验并预览，不立即写入</button><p class="error-banner form-error" hidden role="alert"></p></form><div id="csv-preview" class="v3-stack"></div>` : '<p class="help-text">只读账号可导出数据；导入需要成员或管理员权限。</p>'}`);
  }
  async function previewCSV(form) {
    const file = form.elements.file.files[0]; if (!file) throw new Error('请选择 CSV 文件。');
    const data = new FormData(); data.append('file',file); const token = generation, preview = ++csvPreview;
    csv = null; el('csv-preview').replaceChildren();
    const result = await api('/api/transfer/import/preview',{method:'POST',body:data}); if (!current(token) || preview !== csvPreview || form.elements.file.files[0] !== file) return; csv = result;
    el('csv-preview').innerHTML = `<div class="v3-notice"><strong>${number(result.rows_count)} 行 · 可导入 ${number(result.valid_count)} 行 · 跳过 ${number(result.skipped_count)} 行</strong><p>预览有效至 ${date(result.expires_at,true)}，确认前还不会录入任何物品。</p></div>${result.errors?.length ? `<div class="error-banner" role="alert"><strong>请修正以下错误后重新上传</strong><ul>${result.errors.map(row=>`<li>第 ${number(row.row)} 行 · ${esc(row.field)}：${esc(row.message)}</li>`).join('')}</ul></div>` : ''}${result.warnings?.length ? `<ul class="v3-warning-list">${result.warnings.map(row=>`<li>第 ${number(row.row)} 行：${esc(row.message)}</li>`).join('')}</ul>` : ''}<div class="v3-table-scroll"><table class="v3-table"><caption>前 100 行预览</caption><thead><tr><th>行</th><th>名称</th><th>分类</th><th>位置</th><th>数量</th></tr></thead><tbody>${(result.items || []).map(row=>`<tr><td>${number(row.row)}</td><td>${esc(row.payload.name)}</td><td>${esc(row.payload.category)}</td><td>${esc(row.payload.location_path)}</td><td>${number(row.payload.quantity)}</td></tr>`).join('')}</tbody></table></div>${result.can_confirm ? '<form id="csv-confirm-form" class="v3-stack"><label class="v3-checkbox"><input name="confirmed" type="checkbox" required>已核对预览，确认批量登记这些物品</label><button class="button primary" type="submit">确认导入</button><p class="error-banner form-error" hidden role="alert"></p></form>' : '<p class="help-text">有错误或没有可导入的数据，不能确认导入。</p>'}`;
  }
  async function confirmCSV(form) {
    if (!csv || !form.elements.confirmed.checked) throw new Error('请先校验文件并核对预览。'); const token = generation;
    const result = await post(`/api/transfer/import/${encodeURIComponent(csv.id)}/confirm`,{confirmed:true}); if (!current(token)) return;
    csv = null; el('csv-preview').innerHTML = `<div class="v3-notice"><strong>已登记 ${number(result.created_count)} 件物品</strong><p>跳过 ${number(result.skipped_count)} 行。可以继续上传其他文件。</p></div>`; await refreshAfterMutation();
  }
  function password() { modal('password','修改自己的密码','保存后所有设备上的当前账号会话都会失效，需要重新登录。',`<form id="account-password-form" class="v3-stack">${field('当前密码','current_password','','password','required autocomplete="current-password" maxlength="4096"')}${field('新密码（至少 8 位）','password','','password','required minlength="8" maxlength="4096" autocomplete="new-password"')}${field('再次输入新密码','repeat_password','','password','required minlength="8" maxlength="4096" autocomplete="new-password"')}<button type="submit" class="button primary">保存并重新登录</button><p class="error-banner form-error" hidden role="alert"></p></form>`); }
  async function users() {
    const token = modal('users','家庭成员','管理员管理账号；成员可录入与取放；只读成员只能查找和查看。',`<div id="users-list"><p class="help-text">正在读取成员…</p></div><details class="v3-details"><summary>添加家庭账号</summary><form id="user-create-form" class="v3-stack">${field('用户名（英文字母、数字、点、下划线或短横线）','username','','text','required maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" autocomplete="off"')}${field('显示名称','display_name','','text','maxlength="120"')}${field('初始密码（至少 8 位）','password','','password','required minlength="8" maxlength="4096" autocomplete="new-password"')}${selectField('角色','role',[['member','家庭成员：可管理物品'],['viewer','只读成员：仅查询'],['admin','管理员：可管理系统']],'member')}<button class="button primary" type="submit">创建账号</button><p class="error-banner form-error" hidden role="alert"></p></form></details>`);
    await loadUsers(token);
  }
  async function loadUsers(token = generation) { const rows = await api('/api/users'); if (!current(token)) return; el('users-list').innerHTML = rows.map(user=>`<div class="v3-member-row"><span class="v3-avatar">${esc((user.display_name || user.username || '?').slice(0,1))}</span><div><strong>${esc(user.display_name || user.username)}${Number(user.id) === Number(state.user?.id) ? '（你）' : ''}</strong><p>${esc(user.username)} · ${esc(roles[user.role])} · ${user.active ? '启用中' : '已停用'}</p></div><button class="button secondary button-small" data-v3="user-edit" data-id="${Number(user.id)}">管理</button></div>`).join(''); }
  async function editUser(id) {
    const token = generation;
    const rows = await api('/api/users'); if (!current(token)) return; const user = rows.find(row=>Number(row.id) === id); if (!user) throw new Error('账号不存在。');
    modal('user-edit','管理家庭账号',`账号：${user.username}。修改密码、角色或启用状态会使该账号全部会话失效。`,`<form id="user-edit-form" data-id="${Number(user.id)}" class="v3-stack">${field('显示名称','display_name',user.display_name,'text','required maxlength="120"')}${selectField('角色','role',[['member','家庭成员'],['viewer','只读成员'],['admin','管理员']],user.role)}${field('重设密码（留空保留原密码）','password','','password','minlength="8" maxlength="4096" autocomplete="new-password"')}<label class="v3-checkbox"><input type="checkbox" name="active"${user.active ? ' checked' : ''}>允许此账号登录</label><p class="help-text">系统会保护最后一个启用中的管理员，不能停用或降级。</p><button class="button primary" type="submit">保存账号</button><button class="button secondary" type="button" data-v3="users">返回成员列表</button><p class="error-banner form-error" hidden role="alert"></p></form>`);
  }
  async function openBackups() {
    const token = modal('backups','备份与网盘','完整备份含物品、照片、历史、家庭账号、AI 密钥、WebDAV 凭据和会话密钥。请保存到自己信任的位置。',`<div class="v3-button-row"><button class="button primary" data-v3="backup-create">创建本地完整备份</button><button class="button secondary" data-v3="backup-create-upload">创建并上传网盘</button></div><div id="backup-job" role="status"></div><div id="backup-list"><p class="help-text">正在读取备份…</p></div><div id="backup-settings-mount"><p class="help-text">正在读取 WebDAV 设置…</p></div><p class="help-text">网盘通过你提供的 OpenList / AList WebDAV 目录上传，例如 http://NAS地址:5244/dav/备份/。这里不接收夸克订阅链接或网页登录 Cookie。整库恢复需要先停止服务，按交接文档操作，避免覆盖新数据。</p>`);
    const results = await Promise.allSettled([api('/api/backups/settings'),loadBackups(token)]);
    if (!current(token)) return;
    if (results[0].status === 'fulfilled') backupSettings(results[0].value);
    else el('backup-settings-mount').innerHTML = errorHTML(results[0].reason.message);
    if (results[1].status === 'rejected') el('backup-list').innerHTML = errorHTML(results[1].reason.message);
  }
  function backupSettings(settings) {
    el('backup-settings-mount').innerHTML = `<details class="v3-details" open><summary>WebDAV 与每日自动备份</summary><form id="backup-settings-form" class="v3-stack"><label class="v3-checkbox"><input name="auto_backup" type="checkbox"${settings.auto_backup ? ' checked' : ''}>每天自动创建完整备份</label><label class="v3-checkbox"><input name="auto_upload" type="checkbox"${settings.auto_upload ? ' checked' : ''}>自动备份成功后上传 WebDAV</label><div class="form-grid">${field('每天执行时间','time',settings.time || '03:30','time','required')}${field('保留本地备份份数','keep_count',settings.keep_count || 7,'number','required min="1" max="30" step="1"')}</div>${field('WebDAV 目标目录','webdav_url',settings.webdav_url,'url','maxlength="2048" placeholder="https://网盘网关/dav/备份/"')}${field('WebDAV 用户名','username',settings.username,'text','maxlength="256" autocomplete="off"')}${field(settings.password_set ? 'WebDAV 密码（已保存，留空保留）' : 'WebDAV 密码','password','','password','maxlength="4096" autocomplete="new-password"')}<label class="v3-checkbox"><input name="clear_password" type="checkbox">清除已保存的 WebDAV 密码</label><p class="help-text">时间按 ${esc(settings.timezone || state.timezone)}。保存后密码输入框会清空；改变目录或用户名时，请重新填写密码。自动上传需要同时开启自动备份。</p><p class="v3-http-warning"${settings.http_warning ? '' : ' hidden'}>当前使用 HTTP：WebDAV 密码和备份将在网络中明文传输，建议使用 HTTPS 或可信局域网。</p><button class="button primary" type="submit">保存备份设置</button><p class="error-banner form-error" hidden role="alert"></p><p class="help-text" id="backup-settings-message" role="status"></p></form></details>`;
  }
  async function loadBackups(token = generation) {
    const data = await api('/api/backups'); if (!current(token) || el('v3-modal').dataset.view !== 'backups') return;
    el('backup-list').innerHTML = `<h3 class="section-title">本地备份</h3>${data.backups?.length ? data.backups.map(row=>`<div class="v3-backup-row"><div><strong>${date(row.created_at,true)}</strong><p>${esc(row.filename)} · ${bytes(row.size)}</p><span class="help-text">${row.uploaded_at ? `已上传 ${date(row.uploaded_at,true)}` : '尚未上传网盘'}</span></div><div class="v3-button-row"><a class="button secondary button-small" href="/api/backups/${encodeURIComponent(row.id)}/download" download>下载</a><button class="button secondary button-small" data-v3="backup-upload" data-backup="${esc(row.id)}">上传网盘</button><button class="button subtle button-small" data-v3="backup-delete" data-backup="${esc(row.id)}">删除</button></div></div>`).join('') : '<p class="help-text">还没有本地备份。点击上方按钮即可创建。</p>'}`;
    if (data.active_job || data.last_job) renderBackupJob(data.active_job || data.last_job);
    if (data.active_job && ['pending','queued','running'].includes(data.active_job.status)) scheduleBackups(token);
  }
  function renderBackupJob(job) {
    const mount = el('backup-job'); if (!mount) return;
    const status = {pending:'任务排队中',queued:'任务排队中',running:'正在执行',completed:'备份任务已完成',failed:'备份任务未完成',succeeded:'备份任务已完成'}[job.status] || job.status;
    const phase = {complete:'已完成',queued:'等待执行',snapshot:'正在生成一致性快照',archive:'正在打包备份',upload:'正在上传 WebDAV',completed:'已完成',backup:'正在创建本地备份',uploading:'正在上传 WebDAV'}[job.phase] || job.phase;
    const done = Number(job.progress?.completed_bytes || 0), total = Number(job.progress?.total_bytes || 0);
    mount.innerHTML = `<div class="v3-notice"><strong>${esc(status)}</strong><p>${esc(phase || '')}${total > 0 ? ` · ${bytes(done)} / ${bytes(total)}` : ''}</p>${total > 0 ? `<progress max="${total}" value="${Math.min(done,total)}" aria-label="备份上传进度"></progress>` : ''}${job.error ? errorHTML(job.error) : ''}</div>`;
  }
  function scheduleBackups(token) { clearTimeout(timer); timer = setTimeout(()=> { if (current(token)) loadBackups(token).catch(failure); },2500); }
  async function startBackup(upload = false, backupId = null) {
    const token = generation;
    const job = backupId ? await post(`/api/backups/${encodeURIComponent(backupId)}/upload`,{confirmed:true}) : await post('/api/backups',{confirmed:true,upload});
    if (!current(token)) return; renderBackupJob(job); await loadBackups(token); scheduleBackups(token);
  }
  async function saveBackupSettings(form) {
    const payload = {auto_backup:form.elements.auto_backup.checked,auto_upload:form.elements.auto_upload.checked,webdav_url:form.elements.webdav_url.value.trim(),username:form.elements.username.value.trim(),time:form.elements.time.value,keep_count:Number(form.elements.keep_count.value)};
    if (form.elements.clear_password.checked) payload.password = null;
    else if (form.elements.password.value) payload.password = form.elements.password.value;
    let settings;
    try { settings = await api('/api/backups/settings',{method:'PATCH',body:JSON.stringify(payload)}); }
    finally { form.elements.password.value = ''; }
    if (!form.isConnected) return; backupSettings(settings); el('backup-settings-message').textContent = '备份设置已保存。可点击“创建并上传网盘”验证实际上传。';
  }
  const organizeStates = {generating:'AI 正在生成方案',ready:'等待你确认',applied:'已应用',cancelled:'已取消',failed:'生成失败',interrupted:'任务已中断',rolled_back:'已回档',expired:'预览已过期'};
  async function openOrganize() {
    const token = modal('organize','AI 分类整理','AI 只提出分类方案。确认前不会修改物品，应用后保留原分类快照。',`<form id="organize-create-form" class="v3-stack"><p class="v3-notice">使用当前设置中的模型：<strong>${esc(state.ai.status?.model || '尚未配置模型')}</strong>。会处理全部未归档物品，分批生成并汇总，不只整理当前一页。</p><label class="v3-checkbox"><input name="include_archived" type="checkbox">同时整理已归档物品</label><label class="v3-checkbox"><input name="confirmed" type="checkbox" required>允许将所选物品的名称、标签与分类等必要信息发送给已配置的 AI，生成整理预览</label><button class="button primary" type="submit">让 AI 生成分类方案</button><p class="error-banner form-error" hidden role="alert"></p></form><div id="organize-list"><p class="help-text">正在读取整理历史…</p></div>`);
    await loadOrganize(token);
  }
  async function loadOrganize(token = generation) {
    const data = await api('/api/organize/batches'); if (!current(token) || el('v3-modal').dataset.view !== 'organize') return;
    el('organize-list').innerHTML = `<h3 class="section-title">整理历史与回档</h3>${data.batches?.length ? data.batches.map(batch=>`<div class="v3-backup-row"><div><strong>${esc(organizeStates[batch.state] || batch.state)}</strong><p>${date(batch.created_at,true)} · ${esc(batch.model || '未指定模型')}</p><span class="help-text">${number(batch.processed_items)} / ${number(batch.total_items)} 件 · ${number(batch.completed_chunks)} / ${number(batch.total_chunks)} 批${batch.changed_count != null ? ` · ${number(batch.changed_count)} 件调整` : ''}</span>${batch.error ? `<p class="error-banner">${esc(batch.error)}</p>` : ''}</div><button class="button secondary button-small" data-v3="organize-detail" data-batch="${esc(batch.id)}">${batch.state === 'generating' ? '查看进度' : batch.state === 'applied' ? '查看与回档' : '查看方案'}</button></div>`).join('') : '<p class="help-text">暂无整理历史。生成方案后会在这里留存。</p>'}`;
    if (data.batches?.some(batch=>batch.state === 'generating')) { clearTimeout(timer); timer = setTimeout(()=> { if (current(token)) loadOrganize(token).catch(failure); },2500); }
  }
  async function organizeDetail(id) {
    const token = modal('organize-detail','分类整理方案','请核对每件物品的原分类、新分类及原因。确认后只调整分类。','<div id="organize-detail"><p class="help-text">正在读取方案…</p></div>');
    el('v3-modal').dataset.batch = id; await refreshOrganizeDetail(id,token);
  }
  async function refreshOrganizeDetail(id,token = generation) {
    const batch = await api(`/api/organize/batches/${encodeURIComponent(id)}`); if (!current(token)) return;
    const changes = batch.changes || [];
    el('organize-detail').innerHTML = `<div class="v3-notice"><strong>${esc(organizeStates[batch.state] || batch.state)}</strong><p>实际使用模型：${esc(batch.model || '—')}</p><p>${number(batch.processed_items)} / ${number(batch.total_items)} 件 · ${number(batch.completed_chunks)} / ${number(batch.total_chunks)} 批 · 计划调整 ${number(batch.changed_count)} 件</p>${batch.expires_at && batch.state === 'ready' ? `<p>请在 ${date(batch.expires_at,true)} 前审阅确认。</p>` : ''}${batch.error ? errorHTML(batch.error) : ''}</div>${batch.new_categories?.length ? `<p class="help-text">将新增分类：${batch.new_categories.map(esc).join('、')}</p>` : ''}${changes.length ? `<div class="v3-table-scroll"><table class="v3-table"><caption>完整整理方案 · 共 ${number(changes.length)} 件</caption><thead><tr><th>物品</th><th>原分类</th><th>新分类</th><th>理由</th></tr></thead><tbody>${changes.map(row=>`<tr${row.changed ? ' class="changed-row"' : ''}><td>${esc(row.name)}</td><td>${esc(row.before_category)}</td><td>${esc(row.after_category)}</td><td>${esc(row.reason)}</td></tr>`).join('')}</tbody></table></div>` : ''}${batch.state === 'ready' ? `<form id="organize-confirm-form" data-batch="${esc(id)}" class="v3-stack"><label class="v3-checkbox"><input name="confirmed" type="checkbox" required>我已核对完整方案，允许按这些分类调整物品</label><button class="button primary" type="submit">确认应用分类方案</button><p class="error-banner form-error" hidden role="alert"></p></form>` : ''}${['ready','generating'].includes(batch.state) ? `<button class="button secondary" data-v3="organize-cancel" data-batch="${esc(id)}">取消本次整理</button>` : ''}${batch.state === 'applied' ? `<p class="help-text">回档会先检查此后是否发生过编辑。发现冲突会整批拒绝，保留现在的数据。</p><button class="button secondary" data-v3="organize-rollback-preview" data-batch="${esc(id)}">检查并预览安全回档</button><div id="organize-rollback" class="v3-stack"></div>` : ''}<button class="button subtle" data-v3="organize">返回整理历史</button>`;
    if (batch.state === 'generating') { clearTimeout(timer); timer = setTimeout(()=> { if (current(token)) refreshOrganizeDetail(id,token).catch(failure); },2500); }
  }
  async function rollbackPreview(id) {
    const token = generation; const result = await api(`/api/organize/batches/${encodeURIComponent(id)}/rollback-preview`); if (!current(token)) return;
    el('organize-rollback').innerHTML = result.can_rollback ? `<form id="organize-rollback-form" data-batch="${esc(id)}" class="v3-stack"><p class="v3-notice">检查通过。将把 ${number(result.changed_count)} 件物品恢复到本次整理前的分类，保留其他数据。</p><label class="v3-checkbox"><input name="confirmed" type="checkbox" required>确认恢复这批物品原来的分类</label><button class="button danger" type="submit">确认安全回档</button><p class="error-banner form-error" hidden role="alert"></p></form>` : `<div class="error-banner" role="alert"><strong>存在后续变化，不能安全回档。没有改动任何物品。</strong><ul>${(result.conflicts || []).map(row=>`<li>${esc(row.name || `物品 #${row.item_id}`)}：${esc(row.reason)}</li>`).join('')}</ul></div>`;
  }
  async function integrations() {
    const token = modal('integrations','连接其他 AI','提供受限的只读 API，不直接开放数据库文件，也不允许外部 AI 修改资料。',`<div id="integration-connection"><p class="help-text">正在读取连接信息…</p></div><details class="v3-details" open><summary>创建只读令牌</summary><form id="integration-create-form" class="v3-stack">${field('令牌用途 / 名称','name','','text','required maxlength="100" placeholder="例如：我的 AI 助手"')}${field('有效天数','expires_days',90,'number','required min="1" max="366" step="1"')}<button class="button primary" type="submit">生成只读令牌</button><p class="error-banner form-error" hidden role="alert"></p></form><div id="integration-new-token"></div></details><div id="integration-tokens"></div>`);
    const results = await Promise.allSettled([api('/api/integrations/connection'),loadTokens(token)]); if (!current(token)) return;
    if (results[0].status === 'fulfilled') {
      const data = results[0].value;
      el('integration-connection').innerHTML = `<div class="v3-notice"><strong>只读范围：物品、分类、位置</strong><p>不能写入库存、执行整理、修改设置或访问密钥。令牌只展示一次，到期或撤销后立即失效。</p></div><label class="form-field"><span class="field-label">API Base URL</span><input id="integration-base-url" class="form-control" readonly value="${esc(data.base_url)}"></label><div class="v3-button-row"><button class="button secondary button-small" data-v3="copy" data-copy="integration-base-url">复制 API 地址</button><a class="button secondary button-small" href="/api/integrations/openapi.json" download="homestorage-readonly-openapi.json">下载 OpenAPI</a><button class="button secondary button-small" data-v3="copy-openapi">复制 OpenAPI</button></div><label class="form-field"><span class="field-label">调用示例（把占位符换成刚创建的令牌）</span><textarea id="integration-curl" class="form-control v3-code" readonly rows="4">${esc(data.curl)}</textarea></label><button class="button secondary button-small" data-v3="copy" data-copy="integration-curl">复制调用示例</button><p class="help-text">其他 AI 必须能访问这个 NAS 地址。局域网地址仅同一网络或已配置 VPN 的设备可用；复制 API 不会自动让公网 AI 访问 NAS。OpenAPI 请下载或复制后导入，第三方不能用只读令牌读取管理页面。</p>`;
    } else el('integration-connection').innerHTML = errorHTML(results[0].reason.message);
    if (results[1].status === 'rejected') el('integration-tokens').innerHTML = errorHTML(results[1].reason.message);
  }
  async function loadTokens(token = generation) {
    const data = await api('/api/integrations/tokens'); if (!current(token)) return;
    el('integration-tokens').innerHTML = `<h3 class="section-title">已创建的令牌</h3>${data.tokens?.length ? data.tokens.map(row=>`<div class="v3-backup-row"><div><strong>${esc(row.name)}</strong><p>${row.revoked_at ? '已撤销' : new Date(row.expires_at).getTime() < Date.now() ? '已到期' : '有效中'} · 到期 ${date(row.expires_at,true)}</p><span class="help-text">最近调用：${date(row.last_used_at,true)}</span></div>${!row.revoked_at ? `<button class="button secondary button-small" data-v3="token-revoke" data-token="${esc(row.id)}">撤销</button>` : ''}</div>`).join('') : '<p class="help-text">暂未创建外部调用令牌。</p>'}`;
  }
  async function createToken(form) {
    const token = generation; const data = await post('/api/integrations/tokens',{name:form.elements.name.value.trim(),expires_days:Number(form.elements.expires_days.value)}); if (!current(token)) return;
    el('integration-new-token').innerHTML = `<div class="v3-notice"><strong>请现在复制，此令牌只显示一次</strong><p>关闭面板后不能再次查看。请像密码一样保管。</p><textarea id="integration-secret" class="form-control v3-code" rows="3" readonly aria-label="刚创建的只读令牌"></textarea><div class="v3-button-row"><button class="button primary button-small" data-v3="copy" data-copy="integration-secret">复制令牌</button><button class="button secondary button-small" data-v3="hide-token">已保存，隐藏令牌</button></div></div>`;
    // Assign as text. Do not retain the secret in application state or storage.
    el('integration-secret').value = data.token; form.reset(); await loadTokens(token);
  }
  async function copy(id) {
    const input = el(id); if (!input) return;
    const text = 'value' in input ? input.value : input.textContent;
    if (navigator.clipboard?.writeText && window.isSecureContext) await navigator.clipboard.writeText(text);
    else { input.focus(); input.select(); if (!document.execCommand('copy')) throw new Error('浏览器不支持自动复制，请长按或手动复制所选内容。'); }
    toast('已复制。');
  }
  async function handover() {
    const token = modal('handover','本版交接文档','每次发布记录模型、技术、实现方案、验证与部署注意事项。',`<a class="button secondary" href="/api/handover" download>下载本版交接文档</a><pre id="handover-content" class="v3-document">正在读取交接文档…</pre>`);
    const response = await fetch('/api/handover',{credentials:'same-origin'}); if (!response.ok) throw new Error('交接文档暂不可用，请稍后重试。'); const text = await response.text(); if (current(token)) el('handover-content').textContent = text;
  }
  function confirmTask(title,description,action,attributes='') { modal('confirm',title,description,`<form id="v3-confirm-form" data-task="${action}" ${attributes} class="v3-stack"><label class="v3-checkbox"><input name="confirmed" type="checkbox" required>我已了解，确认执行</label><button class="button danger" type="submit">确认${esc(title)}</button><p class="error-banner form-error" hidden role="alert"></p></form>`); }
  async function deepLink() {
    const query = new URLSearchParams(location.search); const id = query.get('item');
    if (id && /^\d+$/.test(id)) { if (query.get('scan') === '1') openScan(`${location.origin}/?item=${encodeURIComponent(id)}`); else await openDetail(Number(id)); }
  }
  const adminTasks = new Set(['users','user-edit','backups','backup-create','backup-create-upload','backup-upload','backup-delete','organize','organize-detail','organize-cancel','organize-rollback-preview','integrations','token-revoke','copy-openapi']);
  document.addEventListener('click', async event=> {
    const target = event.target.closest('[data-v3]'); if (!target || target.disabled) return;
    const action = target.dataset.v3; event.preventDefault();
    if (adminTasks.has(action) && !isAdmin()) { toast('此功能需要管理员权限。',true); return; }
    try {
      if (action === 'tools') tools();
      else if (action === 'scan' || action === 'scan-next') openScan();
      else if (action === 'camera-start') { target.disabled = true; await cameraStart(); }
      else if (action === 'camera-stop') { stopCamera(); if (el('camera-status')) el('camera-status').textContent = '摄像头已停止。'; }
      else if (action === 'scan-create') { if (!canWrite()) throw new Error('当前账号为只读权限。'); const code = scan?.code || ''; el('v3-modal').close(); await openItemForm(); if (el('item-form')) el('item-form').elements.barcode.value = code; }
      else if (action === 'scan-detail') { const id = Number(el('scan-commit-form').elements.item_id.value); await openDetail(id); }
      else if (action === 'item-qr') {
        const id = Number(target.dataset.id); const item = state.detail?.id === id ? state.detail : await api(`/api/items/${id}`);
        openModal('qr-modal',`${modalHeader('物品二维码','qr-modal-title',item.name)}<div class="modal-body qr-print-content"><div class="qr-card"><p class="eyebrow">HOME STORAGE</p><h3>${esc(item.name)}</h3><p>${esc(item.location_path)}</p><img class="qr-image" src="/api/items/${id}/qr?base_url=${encodeURIComponent(location.origin)}" width="240" height="240" alt="${esc(item.name)}的二维码"><span class="location-code">物品 #${id}${item.barcode ? ` · ${esc(item.barcode)}` : ''}</span><p class="help-text">扫描后查看档案，再由你确认扫入或扫出。</p></div><p class="help-text qr-network-note">二维码使用当前地址 ${esc(location.origin)}。请使用其他设备能访问的 NAS 地址打印。</p></div><div class="modal-footer"><button class="button secondary" data-action="close-modal">关闭</button><button class="button primary" data-action="print-qr">${icon('printer')} 打印二维码</button></div>`);
      }
      else if (action === 'csv') openCSV();
      else if (action === 'password') password();
      else if (action === 'users') await users();
      else if (action === 'user-edit') await editUser(Number(target.dataset.id));
      else if (action === 'backups') await openBackups();
      else if (action === 'backup-create' || action === 'backup-create-upload') { target.disabled = true; await startBackup(action === 'backup-create-upload'); }
      else if (action === 'backup-upload') { target.disabled = true; await startBackup(false,target.dataset.backup); }
      else if (action === 'backup-delete') confirmTask('删除本地备份','只删除选中的本地备份，不删除现有物品，也不删除已经上传的网盘文件。','backup-delete',`data-backup="${esc(target.dataset.backup)}"`);
      else if (action === 'organize') await openOrganize();
      else if (action === 'organize-detail') await organizeDetail(target.dataset.batch);
      else if (action === 'organize-cancel') { target.disabled = true; await post(`/api/organize/batches/${encodeURIComponent(target.dataset.batch)}/cancel`,{confirmed:true}); await organizeDetail(target.dataset.batch); }
      else if (action === 'organize-rollback-preview') { target.disabled = true; await rollbackPreview(target.dataset.batch); }
      else if (action === 'integrations') await integrations();
      else if (action === 'token-revoke') confirmTask('撤销只读令牌','撤销后持有这个令牌的 AI 或客户端会立即失去访问权限。','token-revoke',`data-token="${esc(target.dataset.token)}"`);
      else if (action === 'copy') await copy(target.dataset.copy);
      else if (action === 'copy-openapi') {
        const schema = await api('/api/integrations/openapi.json'); const text = JSON.stringify(schema,null,2);
        let input = el('integration-schema'); if (!input) { input = document.createElement('textarea'); input.id = 'integration-schema'; input.className = 'form-control v3-code'; input.readOnly = true; input.rows = 6; el('integration-connection').append(input); } input.value = text; await copy(input.id);
      }
      else if (action === 'hide-token') el('integration-new-token').replaceChildren();
      else if (action === 'activity-type') { const value = target.dataset.value; if (['movements','searches'].includes(value)) { metric = value; drawAnalytics(); } }
      else if (action === 'activity-day') el('activity-day-label').textContent = target.dataset.label;
      else if (action === 'activity-retry') await loadAnalytics();
      else if (action === 'handover') await handover();
    } catch (error) { failure(error); }
    finally { if (target.isConnected) target.disabled = false; }
  });
  const v3Forms = new Set(['scan-lookup-form','scan-commit-form','csv-preview-form','csv-confirm-form','account-password-form','user-create-form','user-edit-form','backup-settings-form','organize-create-form','organize-confirm-form','organize-rollback-form','integration-create-form','v3-confirm-form']);
  document.addEventListener('submit',async event=> {
    const form = event.target; if (!v3Forms.has(form.id)) return;
    event.preventDefault(); event.stopImmediatePropagation(); if (form.dataset.saving === 'true') return;
    const button = form.querySelector('[type="submit"]'); const error = form.querySelector('.form-error');
    form.dataset.saving = 'true'; if (button) button.disabled = true; if (error) error.hidden = true;
    const token = generation;
    try {
      if (['scan-commit-form','csv-preview-form','csv-confirm-form'].includes(form.id) && !canWrite()) throw new Error('当前账号为只读权限。');
      if (['user-create-form','user-edit-form','backup-settings-form','organize-create-form','organize-confirm-form','organize-rollback-form','integration-create-form','v3-confirm-form'].includes(form.id) && !isAdmin()) throw new Error('此功能需要管理员权限。');
      if (form.id === 'scan-lookup-form') await lookupScan(form);
      else if (form.id === 'scan-commit-form') await commitScan(form);
      else if (form.id === 'csv-preview-form') await previewCSV(form);
      else if (form.id === 'csv-confirm-form') await confirmCSV(form);
      else if (form.id === 'account-password-form') {
        if (form.elements.password.value !== form.elements.repeat_password.value) throw new Error('两次输入的新密码不一致。');
        try { await post('/api/account/password',{current_password:form.elements.current_password.value,password:form.elements.password.value}); }
        finally { form.elements.current_password.value = ''; form.elements.password.value = ''; form.elements.repeat_password.value = ''; }
        showLogin(); toast('密码已修改，请重新登录。');
      }
      else if (form.id === 'user-create-form') {
        try { await post('/api/users',{username:form.elements.username.value.trim(),display_name:form.elements.display_name.value.trim(),password:form.elements.password.value,role:form.elements.role.value}); }
        finally { form.elements.password.value = ''; }
        if (current(token)) { form.reset(); await loadUsers(token); toast('家庭账号已创建。'); }
      }
      else if (form.id === 'user-edit-form') {
        const payload = {display_name:form.elements.display_name.value.trim(),role:form.elements.role.value,active:form.elements.active.checked}; if (form.elements.password.value) payload.password = form.elements.password.value;
        let user; try { user = await api(`/api/users/${encodeURIComponent(form.dataset.id)}`,{method:'PATCH',body:JSON.stringify(payload)}); } finally { form.elements.password.value = ''; }
        if (Number(form.dataset.id) === Number(state.user?.id)) { if (user.role !== state.user.role || !user.active || payload.password) { showLogin(); toast('你的账号权限或密码已改变，请重新登录。'); } else { state.user = user; if (el('v3-settings-mount')) delete el('v3-settings-mount').dataset.loaded; settings(); await users(); } }
        else if (current(token)) { await users(); toast('账号已更新。'); }
      }
      else if (form.id === 'backup-settings-form') await saveBackupSettings(form);
      else if (form.id === 'organize-create-form') { const result = await post('/api/organize/batches',{confirmed:form.elements.confirmed.checked,include_archived:form.elements.include_archived.checked}); if (current(token)) await organizeDetail(result.batch.id); }
      else if (form.id === 'organize-confirm-form') { await post(`/api/organize/batches/${encodeURIComponent(form.dataset.batch)}/confirm`,{confirmed:form.elements.confirmed.checked}); if (current(token)) { await refreshAfterMutation(); await organizeDetail(form.dataset.batch); toast('分类方案已应用。'); } }
      else if (form.id === 'organize-rollback-form') { await post(`/api/organize/batches/${encodeURIComponent(form.dataset.batch)}/rollback`,{confirmed:form.elements.confirmed.checked}); if (current(token)) { await refreshAfterMutation(); await organizeDetail(form.dataset.batch); toast('已安全恢复这批物品的原分类。'); } }
      else if (form.id === 'integration-create-form') await createToken(form);
      else if (form.id === 'v3-confirm-form') {
        if (!form.elements.confirmed.checked) throw new Error('请先勾选确认。');
        if (form.dataset.task === 'backup-delete') { await api(`/api/backups/${encodeURIComponent(form.dataset.backup)}`,{method:'DELETE',body:JSON.stringify({confirmed:true})}); if (current(token)) await openBackups(); }
        else if (form.dataset.task === 'token-revoke') { await api(`/api/integrations/tokens/${encodeURIComponent(form.dataset.token)}`,{method:'DELETE'}); if (current(token)) { await integrations(); toast('只读令牌已撤销。'); } }
      }
    } catch (failureError) { if (error?.isConnected) { error.textContent = failureError.message; error.hidden = false; } else failure(failureError); }
    finally { form.dataset.saving = 'false'; if (button?.isConnected) button.disabled = false; }
  },true);
  document.addEventListener('change',event=> {
    if (event.target.form?.id === 'scan-commit-form' && event.target.name !== 'confirmed') {
      const quantity = event.target.form.elements.quantity;
      quantity.disabled = ['take_out','return'].includes(event.target.form.elements.operation.value);
      if (quantity.disabled) quantity.value = '1';
      event.target.form.elements.confirmed.checked = false;
    }
    if (event.target.form?.id === 'csv-preview-form' && event.target.name === 'file') { csvPreview++; csv = null; el('csv-preview')?.replaceChildren(); }
    if (event.target.closest('#backup-settings-form') && event.target.name === 'clear_password' && event.target.checked) event.target.form.elements.password.value = '';
  });
  document.addEventListener('input',event=> {
    if (event.target.form?.id === 'scan-lookup-form' && event.target.name === 'code') { scanLookup++; scan = null; el('scan-result')?.replaceChildren(); }
    if (event.target.form?.id === 'scan-commit-form' && event.target.name !== 'confirmed') event.target.form.elements.confirmed.checked = false;
    if (event.target.closest('#backup-settings-form') && event.target.name === 'password' && event.target.value) event.target.form.elements.clear_password.checked = false;
  });
  document.addEventListener('focusin',event=> { if (event.target.matches('.activity-cell') && el('activity-day-label')) el('activity-day-label').textContent = event.target.dataset.label; });
  document.addEventListener('keydown',event=> {
    if (!event.target.matches('.activity-cell') || !['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault(); const cells = [...el('activity-panel').querySelectorAll('.activity-cell')]; const index = cells.indexOf(event.target); const next = event.key === 'Home' ? 0 : event.key === 'End' ? cells.length-1 : index+({ArrowUp:-1,ArrowDown:1,ArrowLeft:-7,ArrowRight:7}[event.key]); const selected = cells[Math.min(cells.length-1,Math.max(0,next))]; cells.forEach(cell=>cell.tabIndex = cell === selected ? 0 : -1); selected?.focus();
  });
  document.addEventListener('DOMContentLoaded',()=> { el('v3-modal')?.addEventListener('close',()=> { generation++; clearTimeout(timer); stopCamera(); scan = null; csv = null; el('v3-modal').replaceChildren(); }); });
  return {isAdmin,canWrite,allowedAction,allowedForm,permissions,settings,afterPage,pageChanging,clear,deepLink};
})();
