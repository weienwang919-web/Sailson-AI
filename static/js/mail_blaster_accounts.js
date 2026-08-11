/* 发件账号池：素材提交页和 KOL 建联页共用。

   依赖 mail_blaster_common.js（api / toast / esc / openModal / usable）
   和宿主页面提供的全局 POOL + loadPool()。 */

let EDITING_ID = null;

async function openAccounts() { openModal('accounts-modal'); await renderAccounts(); }

const ACC_STATUS_TEXT = { ready: '可用', failed: '失败', draft: '未测试' };

async function renderAccounts() {
  await loadPool();
  document.getElementById('acc-empty').style.display = POOL.length ? 'none' : '';
  document.getElementById('acc-rows').innerHTML = POOL.map(a => `
    <tr>
      <td><input type="checkbox" ${a.enabled ? 'checked' : ''}
                 onchange="toggleAccount(${a.id}, this.checked)"></td>
      <td class="mono">${esc(a.email)}${
        a.auth_mode === 'xoauth2' ? ' <span class="pill pill-oauth">OAuth2</span>' : ''}</td>
      <td>${esc(a.effective_display_name)}</td>
      <td class="mono">${esc(a.smtp_host)}:${a.smtp_port}${
        a.use_ssl ? ' SSL' : (a.use_tls ? ' TLS' : '')}</td>
      <td class="mono">${a.daily_limit}${a.daily_limit === 0 ? ' <span class="pill">已停发</span>' : ''}</td>
      <td><span class="pill pill-${a.status}">${ACC_STATUS_TEXT[a.status] || a.status}</span>
        ${a.last_error ? `<div class="errbox">${esc(a.last_error)}</div>` : ''}</td>
      <td>
        <button class="small" onclick="editAccount(${a.id})">编辑</button>
        <button class="small" onclick="testAccount(${a.id}, this)">测试</button>
        <button class="small danger" onclick="removeAccount(${a.id})">删除</button>
      </td>
    </tr>`).join('');
}

/* ---------- 快速新增（常见场景：服务商预设 + 授权码） ---------- */
async function addAccount() {
  const email = document.getElementById('acc-email').value.trim();
  const pwd = document.getElementById('acc-pass').value;
  if (!email || !pwd) return toast('邮箱和密码都要填', true);
  try {
    await api('/api/mail-blaster/accounts', { method: 'POST', body: {
      email, app_password: pwd, provider: document.getElementById('acc-provider').value } });
    document.getElementById('acc-email').value = '';
    document.getElementById('acc-pass').value = '';
    await renderAccounts();
    toast('已添加，记得点「测试」验证一下');
  } catch (e) { toast(e.message, true); }
}

async function bulkImport() {
  const text = document.getElementById('acc-bulk').value;
  if (!text.trim()) return toast('粘贴点内容', true);
  try {
    const r = await api('/api/mail-blaster/accounts/bulk-import', { method: 'POST', body: { text } });
    document.getElementById('acc-bulk-result').innerHTML =
      `<div class="smtp-resp">新增 ${r.created.length} 个` +
      (r.skipped.length ? `，跳过已存在 ${r.skipped.length} 个` : '') + '</div>' +
      (r.errors.length ? `<div class="errbox">${r.errors.map(esc).join('<br>')}</div>` : '');
    document.getElementById('acc-bulk').value = '';
    await renderAccounts();
  } catch (e) { toast(e.message, true); }
}

async function testAccount(id, btn) {
  btn.disabled = true; btn.textContent = '测试中…';
  try { await api(`/api/mail-blaster/accounts/${id}/test`, { method: 'POST' }); }
  catch (e) { toast(e.message, true); }
  await renderAccounts();
}

async function toggleAccount(id, enabled) {
  try { await api(`/api/mail-blaster/accounts/${id}`, { method: 'PUT', body: { enabled } }); }
  catch (e) { toast(e.message, true); }
  await loadPool();
}

async function removeAccount(id) {
  if (!confirm('删除这个发件账号？已发出的记录不受影响。')) return;
  try { await api(`/api/mail-blaster/accounts/${id}`, { method: 'DELETE' }); await renderAccounts(); }
  catch (e) { toast(e.message, true); }
}

/* ---------- 完整表单 ---------- */
const $f = id => document.getElementById(id);

function newAccount() {
  EDITING_ID = null;
  $f('f-title').textContent = '新增发件账号';
  for (const id of ['f-email', 'f-password', 'f-client-id', 'f-refresh-token',
                    'f-display', 'f-signature', 'f-username', 'f-host']) $f(id).value = '';
  $f('f-provider').value = 'custom';
  $f('f-auth-mode').value = 'password';
  $f('f-daily-limit').value = 10;
  $f('f-sort-order').value = 0;
  $f('f-enabled').checked = true;
  $f('f-email').disabled = false;
  $f('f-secret-hint').textContent = '';
  applyProvider();          // 带出预设的 host/port/ssl/tls
  onAuthModeChange();
  openModal('account-form-modal');
}

function editAccount(id) {
  const a = POOL.find(x => x.id === id);
  if (!a) return toast('账号不在列表里，刷新一下', true);
  EDITING_ID = id;
  $f('f-title').textContent = `编辑 ${a.email}`;
  $f('f-email').value = a.email;
  $f('f-email').disabled = true;        // 邮箱是唯一键，改它等于换一个账号
  $f('f-provider').value = a.provider || 'custom';
  $f('f-auth-mode').value = a.auth_mode || 'password';
  $f('f-host').value = a.smtp_host || '';
  $f('f-port').value = a.smtp_port || '';
  $f('f-username').value = a.smtp_username || '';
  $f('f-display').value = a.display_name || '';
  $f('f-signature').value = a.signature_name || '';
  $f('f-daily-limit').value = a.daily_limit;
  $f('f-sort-order').value = a.sort_order || 0;
  $f('f-ssl').checked = !!a.use_ssl;
  $f('f-tls').checked = !!a.use_tls;
  $f('f-enabled').checked = !!a.enabled;
  // 密文永不回显。留空 = 不改动。
  for (const id of ['f-password', 'f-client-id', 'f-refresh-token']) $f(id).value = '';
  $f('f-password').placeholder = a.has_password ? '已保存，留空表示不修改' : '未设置';
  $f('f-client-id').placeholder = a.has_client_id ? '已保存，留空表示不修改' : '选填';
  $f('f-refresh-token').placeholder = a.has_refresh_token ? '已保存，留空表示不修改' : '选填';
  $f('f-secret-hint').textContent = '改了密码或令牌后状态会重置为「未测试」，记得重新点测试。';
  showProviderNote();
  onAuthModeChange();
  openModal('account-form-modal');
}

/* 选服务商时带出预设。只在新增时覆盖已填的值——
   编辑时用户可能故意改过 host，不该被下拉框重置。 */
function applyProvider() {
  const opt = $f('f-provider').selectedOptions[0];
  if (!opt) return;
  if (EDITING_ID === null) {
    $f('f-host').value = opt.dataset.host || '';
    $f('f-port').value = opt.dataset.port || '';
    $f('f-ssl').checked = opt.dataset.ssl === '1';
    $f('f-tls').checked = opt.dataset.tls === '1';
  }
  showProviderNote();
}

function showProviderNote() {
  const opt = $f('f-provider').selectedOptions[0];
  $f('f-provider-note').textContent = (opt && opt.dataset.note) || '';
}

function onAuthModeChange() {
  const isOauth = $f('f-auth-mode').value === 'xoauth2';
  $f('f-oauth-fields').style.display = isOauth ? '' : 'none';
  $f('f-password-field').style.display = isOauth ? 'none' : '';
}

async function saveAccount(btn) {
  const body = {
    email: $f('f-email').value.trim(),
    provider: $f('f-provider').value,
    auth_mode: $f('f-auth-mode').value,
    smtp_host: $f('f-host').value.trim(),
    smtp_port: $f('f-port').value,
    smtp_username: $f('f-username').value.trim(),
    display_name: $f('f-display').value.trim(),
    signature_name: $f('f-signature').value.trim(),
    // 这两个必须成对提交：后端只在两个 key 都缺席时才回落到服务商预设
    use_ssl: $f('f-ssl').checked,
    use_tls: $f('f-tls').checked,
    // 用 Number 而不是 || ：0 是「今天一封都别发」，不是「没填」
    daily_limit: Number($f('f-daily-limit').value),
    sort_order: Number($f('f-sort-order').value),
    enabled: $f('f-enabled').checked,
  };
  // 留空表示不修改，所以空值不进 payload
  const pwd = $f('f-password').value;
  const cid = $f('f-client-id').value.trim();
  const rtok = $f('f-refresh-token').value.trim();
  if (pwd) body.app_password = pwd;
  if (cid) body.client_id = cid;
  if (rtok) body.refresh_token = rtok;

  btn.disabled = true;
  try {
    if (EDITING_ID === null) {
      await api('/api/mail-blaster/accounts', { method: 'POST', body });
    } else {
      delete body.email;                       // 邮箱不可改
      await api(`/api/mail-blaster/accounts/${EDITING_ID}`, { method: 'PUT', body });
    }
    closeModal('account-form-modal');
    await renderAccounts();
    toast(EDITING_ID === null ? '已添加，记得点「测试」验证一下' : '已保存');
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
}
