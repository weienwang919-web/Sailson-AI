/* KOL 回信管理页。依赖 mail_blaster_common.js（api / toast / esc）。 */

let THREADS = [], CURRENT = null, FILTER = '';

const STAT_DEFS = [
  ['pending', '待回复'], ['replied', '已回复'], ['negotiating', '议价中'],
  ['won', '已成交'], ['lost', '已放弃'],
];

async function loadThreads() {
  let data;
  const qs = new URLSearchParams();
  if (FILTER) qs.set('status', FILTER);
  const kw = document.getElementById('q').value.trim();
  if (kw) qs.set('q', kw);
  try { data = await api('/api/mail-blaster/inbox/threads?' + qs); }
  catch (e) { return toast(e.message, true); }

  THREADS = data.threads;
  const s = data.stats || {};
  document.getElementById('stats').innerHTML = STAT_DEFS.map(([k, label]) =>
    `<div class="stat ${FILTER === k ? 'on' : ''}" onclick="setFilter('${k}')">
       <b>${s[k] || 0}</b><span>${label}</span></div>`).join('') +
    `<div class="stat ${FILTER === '' ? 'on' : ''}" onclick="setFilter('')">
       <b>${THREADS.length}</b><span>全部</span></div>` +
    (s.needs_human ? `<div class="stat"><b style="color:var(--err)">${s.needs_human}</b>
       <span>AI 标了待人工</span></div>` : '');

  document.getElementById('thread-empty').style.display = THREADS.length ? 'none' : '';
  document.getElementById('thread-rows').innerHTML = THREADS.map(t => `
    <tr class="thread-row ${CURRENT === t.id ? 'on' : ''}" onclick="openThread(${t.id})">
      <td>
        <div>${esc(t.kol_name || '—')}${t.unhandled ? ' <span class="pill">新</span>' : ''}</div>
        <div class="mono" style="font-size:12px;color:var(--muted)">${esc(t.kol_email)}</div>
      </td>
      <td><span class="pill">${esc(t.status_text)}</span></td>
      <td class="mono">${t.latest_amount != null
        ? esc(t.latest_amount + ' ' + (t.latest_currency || '')) : '—'}</td>
    </tr>`).join('');

  const un = data.unmatched || [];
  document.getElementById('unmatched-card').style.display = un.length ? '' : 'none';
  document.getElementById('unmatched-rows').innerHTML = un.map(m => `
    <tr>
      <td><div class="mono">${esc(m.from_email)}</div>
          <div style="font-size:12px;color:var(--muted)">${esc(m.subject || '')}</div></td>
      <td style="width:120px">${esc(m.received_at || '')}</td>
      <td style="width:80px"><button class="small" onclick="claim(${m.id})">认领</button></td>
    </tr>`).join('');
}

function setFilter(k) { FILTER = k; loadThreads(); }

async function claim(inboxId) {
  const who = prompt('这封信是哪个达人回的？填他的邮箱（留空就用发件地址）');
  if (who === null) return;
  try {
    await api(`/api/mail-blaster/inbox/messages/${inboxId}/claim`,
      { method: 'POST', body: { kol_email: who.trim() } });
    toast('已挂到会话线');
    await loadThreads();
  } catch (e) { toast(e.message, true); }
}

async function openThread(id) {
  CURRENT = id;
  let d;
  try { d = await api(`/api/mail-blaster/inbox/threads/${id}`); }
  catch (e) { return toast(e.message, true); }

  document.getElementById('detail-empty').style.display = 'none';
  document.getElementById('detail').style.display = '';
  const t = d.thread;
  document.getElementById('d-title').textContent = t.kol_name || t.kol_email;
  document.getElementById('d-status').textContent = t.status_text;
  document.getElementById('d-meta').innerHTML =
    `${esc(t.kol_email)}　·　首发 ${esc(t.first_sent_at || '—')}` +
    `　·　最近回信 ${esc(t.last_reply_at || '—')}` +
    `　·　已还价 ${d.round} 轮` +
    (t.intent_text ? `　·　最近意图：${esc(t.intent_text)}` : '');

  const nx = document.getElementById('d-next');
  nx.style.display = t.next_action ? '' : 'none';
  nx.textContent = t.next_action || '';

  document.getElementById('d-timeline').innerHTML = d.messages.map(m => {
    const tags = [];
    if (m.intent_text) tags.push(`<span class="pill">${esc(m.intent_text)}</span>`);
    if (m.quote_amount != null)
      tags.push(`<span class="pill">${esc(m.quote_amount + ' ' + (m.quote_currency || ''))}</span>`);
    if (m.needs_human) tags.push('<span class="pill pill-failed">待人工</span>');
    if (m.ai_status === 'failed') tags.push('<span class="pill pill-failed">AI 失败</span>');
    return `<li>
      <div class="meta">${esc(m.received_at)}　${esc(m.from_email)}　${tags.join(' ')}
        <button class="small" style="margin-left:8px"
                onclick="toggleHandled(${m.id}, ${!m.handled})">
          ${m.handled ? '标为未处理' : '标为已处理'}</button></div>
      ${m.summary ? `<div class="meta">AI：${esc(m.summary)}</div>` : ''}
      ${m.needs_human_reason ? `<div class="errbox">${esc(m.needs_human_reason)}</div>` : ''}
      ${m.ai_error ? `<div class="errbox">${esc(m.ai_error)}</div>` : ''}
      <div class="body">${esc(m.body_text || '')}</div>
    </li>`;
  }).join('');

  document.getElementById('d-quotes-empty').style.display = d.quotes.length ? 'none' : '';
  document.getElementById('d-quotes').innerHTML = d.quotes.map(q => `
    <tr>
      <td>v${q.version}</td>
      <td>${q.status === 'countered' ? '我方' : '对方'}</td>
      <td class="mono">${q.amount != null ? esc(q.amount + ' ' + q.currency) : '—'}</td>
      <td>${esc(q.note || '')}</td>
      <td class="mono">${esc(q.created_at)}</td>
    </tr>`).join('');

  document.getElementById('d-draft').value = '';
  document.getElementById('d-plan').textContent = '';
  document.getElementById('d-set-status').value = '';
  await loadThreads();
}

async function toggleHandled(inboxId, handled) {
  try {
    await api(`/api/mail-blaster/inbox/messages/${inboxId}/handled`,
      { method: 'POST', body: { handled } });
    await openThread(CURRENT);
  } catch (e) { toast(e.message, true); }
}

async function changeStatus() {
  const v = document.getElementById('d-set-status').value;
  if (!v || !CURRENT) return;
  try {
    await api(`/api/mail-blaster/inbox/threads/${CURRENT}/status`,
      { method: 'POST', body: { status: v } });
    toast('状态已更新');
    await openThread(CURRENT);
  } catch (e) { toast(e.message, true); }
}

async function suggest(btn) {
  if (!CURRENT) return;
  btn.disabled = true;
  try {
    const r = await api(`/api/mail-blaster/inbox/threads/${CURRENT}/suggest`, {
      method: 'POST', body: {
        target: document.getElementById('d-target').value,
        ceiling: document.getElementById('d-ceiling').value,
        currency: document.getElementById('d-currency').value,
      } });
    // 先刷新（我方还价会作为新版本进账本），再填草稿。
    // 顺序反过来的话 openThread 会把刚生成的草稿和说明清空。
    await openThread(CURRENT);
    document.getElementById('d-plan').textContent = r.rationale || '';
    document.getElementById('d-draft').value = r.draft ||
      '（AI 没能生成话术，但上面的数字建议仍然有效，可以自己写）';
    toast(r.next_action || '已生成');
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
}

async function pollNow(btn) {
  btn.disabled = true;
  try {
    await api('/api/mail-blaster/inbox/poll', { method: 'POST' });
    toast(`已入队，后台正在收信（平时每 ${POLL_MINUTES} 分钟自动收一次）`);
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
}

document.addEventListener('DOMContentLoaded', loadThreads);
