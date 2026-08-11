/* mail-blaster 两个页面（素材提交 / KOL 建联）共用的小工具。
   从 mail_blaster.js 原样抽出来，逻辑没改动，只是搬了位置。

   注意：本站 API 的约定是 {status:'success'|'error', message:...}，
   跟 mail-blaster 独立版的 {ok:true} 不同，api() 已按本站约定改写。 */

const STATUS_TEXT = { pending: '待发送', queued: '已入队', sending: '发送中…',
                      sent: '已发送', failed: '失败', skipped: '已跳过' };

function toast(msg, isError) {
  let el = document.getElementById('mb-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'mb-toast'; el.className = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.toggle('err', !!isError);
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), isError ? 6000 : 2600);
}

async function api(url, options = {}) {
  const opts = { ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(url, opts);
  if (res.status === 401 || res.status === 403) {
    throw new Error('没有权限或登录已过期，请刷新页面重新登录');
  }
  let data;
  try { data = await res.json(); }
  catch (e) { throw new Error(`服务器返回了非 JSON 内容（HTTP ${res.status}）`); }
  if (!res.ok || data.status === 'error') {
    throw new Error(data.message || data.error || `HTTP ${res.status}`);
  }
  return data;
}

function esc(t) {
  return String(t == null ? '' : t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function openModal(id) { document.getElementById(id).classList.add('show'); }
function closeModal(id) { document.getElementById(id).classList.remove('show'); }
document.addEventListener('click', e => {
  if (e.target.classList && e.target.classList.contains('modal')) e.target.classList.remove('show');
});

function insertAtCursor(ta, text) {
  const s = ta.selectionStart ?? ta.value.length, e = ta.selectionEnd ?? ta.value.length;
  ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
  ta.focus(); ta.selectionStart = ta.selectionEnd = s + text.length;
}

/* 只重画变化的格子。整表重建会吞掉用户还没提交的输入，也会让 select 丢焦点。
   第三个参数是 {选择器: 渲染函数}，注意不是 {cells: {...}}——
   独立版的签名是后者，照搬会静默失效（querySelector('cells') 找不到就跳过）。 */
function patchRows(tbody, rows, cells) {
  for (const row of rows) {
    const tr = tbody.querySelector(`tr[data-id="${row.id}"]`);
    if (!tr) continue;
    for (const [sel, render] of Object.entries(cells)) {
      const td = tr.querySelector(sel);
      if (!td) continue;
      const next = render(row);
      if (td.innerHTML !== next) td.innerHTML = next;
    }
  }
}

// 密码模式要有密码，OAuth 模式要有 refresh_token
const usable = a => a.enabled && a.status === 'ready' &&
  (a.auth_mode === 'xoauth2' ? (a.has_client_id && a.has_refresh_token) : a.has_password);
