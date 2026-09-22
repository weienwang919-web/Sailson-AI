/* KOL 建联页。

   通用小工具在 mail_blaster_common.js，发件账号池在 mail_blaster_accounts.js，
   两个都由模板先于本文件加载。 */

let POOL = [], PARSED = null, JOB = null, PREVIEWS = [], TEMPLATES = [], ATTACHMENTS = [];
let previewIndex = 0, pollTimer = null, jobPromise = null;
const JOB_STATUS = {draft: '草稿', queued: '排队中', sending: '发送中', done: '已结束'};
const ITEM_STATUS = {pending: '待发送', sent: '已发送', sending: '发送中', failed: '失败', skipped: '已跳过', unknown: '投递待核实'};

async function loadActivities() {
  const empty = document.getElementById('activity-empty');
  try {
    const {jobs} = await api('/api/mail-blaster/outreach/jobs');
    empty.textContent = '还没有活动';
    empty.style.display = jobs.length ? 'none' : '';
    document.getElementById('activity-rows').innerHTML = jobs.map(j => `<tr>
      <td>${esc(j.name || j.subject_tpl || `活动 #${j.id}`)}<div class="hint">#${j.id}</div></td>
      <td>${esc(j.sender_email || '邮箱已移除')}</td>
      <td>${esc(j.paused_reason ? '已暂停' : JOB_STATUS[j.status] || j.status)}</td>
      <td>${j.sent} / ${j.total}${Number(j.failed) ? `<div class="hint">失败 ${j.failed}</div>` : ''}${
        Number(j.unknown) ? `<div class="hint">待核实 ${j.unknown}</div>` : ''}</td>
      <td>${esc((j.created_at || '').replace('T', ' ').slice(0, 16))}</td>
      <td><button class="small" onclick="restoreActivity(${j.id}, this)">${j.status === 'draft' ? '继续编辑' : '查看进度'}</button></td>
    </tr>`).join('');
  } catch (e) { empty.style.display = ''; empty.textContent = e.message; }
}

function updateActivityControls() {
  const locked = JOB && JOB.job.status !== 'draft';
  document.querySelectorAll('#pane-send input, #pane-send textarea, #pane-send select').forEach(el => el.disabled = !!locked);
  document.getElementById('save-activity').disabled = !!locked;
  document.getElementById('activity-state').textContent = JOB ? `#${JOB.job.id} · ${JOB_STATUS[JOB.job.status] || JOB.job.status}` : '未保存';
  document.getElementById('send-activity').disabled = !!(JOB &&
    (['queued', 'sending'].includes(JOB.job.status) ||
     (JOB.job.status === 'done' && !JOB.counts.failed && !JOB.job.paused_reason && !JOB.counts.pending)));
}

function newActivity() {
  clearInterval(pollTimer);
  JOB = null; PARSED = null; ATTACHMENTS = []; PREVIEWS = [];
  document.getElementById('activity-name').value = '';
  document.getElementById('list-text').value = '';
  document.getElementById('list-card').style.display = 'none';
  for (const id of ['parse-info', 'parse-report', 'send-info', 'pause-box', 'dynamic-ph']) document.getElementById(id).textContent = '';
  document.getElementById('progress').style.display = 'none';
  renderAttachments(); updateActivityControls(); switchTab('send');
}

async function restoreActivity(id, btn) {
  clearInterval(pollTimer);
  document.getElementById('progress').style.display = 'none';
  document.getElementById('pause-box').textContent = '';
  if (btn) btn.disabled = true;
  try {
    const data = await api(`/api/mail-blaster/jobs/${id}/status`);
    await loadPool();
    const columns = [...new Set(data.items.flatMap(i => Object.keys(i.vars || {})))];
    renderParsed({rows: data.items.map(i => ({email:i.recipient, vars:i.vars || {}})),
      columns, errors:[], duplicates:[], suppressed:[], source:'saved'});
    JOB = data; ATTACHMENTS = data.attachments || [];
    document.getElementById('activity-name').value = data.job.name || '';
    for (const field of ['subject', 'body', 'signature']) document.getElementById(field).value = data.job[`${field}_tpl`];
    const sel = document.getElementById('account');
    if (![...sel.options].some(o => o.value === String(data.job.sender_account_id))) {
      sel.add(new Option('原邮箱已停用或授权已撤销', data.job.sender_account_id));
    }
    sel.value = String(data.job.sender_account_id); onAccountChange();
    renderAttachments(); updateActivityControls(); switchTab('send');
    renderItemStatuses(data);
    if (['queued','sending'].includes(data.job.status)) startPolling(id);
    else if (data.job.status === 'done') await refresh(id);
  } catch(e) { toast(e.message, true); }
  finally { if (btn) btn.disabled = false; }
}

async function saveActivity(btn) {
  btn.disabled = true;
  try { if (await ensureJob()) { toast('草稿已保存'); await loadActivities(); } }
  catch(e) { toast(e.message, true); }
  finally { updateActivityControls(); }
}

function editable() { return !JOB || JOB.job.status === 'draft'; }

function renderItemStatuses(data) {
  if (!document.getElementById('status-head')) {
    document.getElementById('list-head').insertAdjacentHTML('beforeend', '<th id="status-head">发送状态</th>');
  }
  document.querySelectorAll('#list-rows tr').forEach((tr, i) => {
    const item = data.items[i];
    if (!item) return;
    let cell = tr.querySelector('.item-status');
    if (!cell) { cell = tr.insertCell(); cell.className = 'item-status'; }
    cell.innerHTML = `${esc(ITEM_STATUS[item.status] || item.status)}${item.error ? `<div class="hint">${esc(item.error)}</div>` : ''}`;
  });
}

/* ---------- 账号池 ---------- */
async function loadPool() {
  try { POOL = (await api('/api/mail-blaster/accounts?purpose=outreach')).accounts; }
  catch (e) { return toast(e.message, true); }
  const sel = document.getElementById('account');
  const keep = sel.value;
  const ok = POOL.filter(usable);
  sel.innerHTML = ok.length
    ? ok.map(a => `<option value="${a.id}">${esc(a.email)}（今日上限 ${
        a.daily_limit === null ? '不限' : a.daily_limit}${
        a.can_receive ? '' : '，收不了回信'}）</option>`).join('')
    : '<option value="">还没有可用账号</option>';
  if (keep && ok.some(a => String(a.id) === keep)) sel.value = keep;
  onAccountChange();
}

function currentAccount() {
  return POOL.find(a => String(a.id) === document.getElementById('account').value);
}

function onAccountChange() {
  const a = currentAccount();
  const info = document.getElementById('account-info');
  if (!a) {
    info.innerHTML = '<span style="color:var(--err)">没有可用的已授权邮箱，请联系管理员。</span>';
    return;
  }
  info.innerHTML = esc(`发件人显示为「${a.effective_display_name}」，落款「${a.effective_signature_name}」`) +
    (a.daily_limit === 0 ? '　<span style="color:var(--err)">⚠️ 每日上限是 0，这个账号今天发不出去</span>' : '') +
    (a.can_receive ? '' :
      '　<span style="color:var(--err)">⚠️ 没配 IMAP 或是 OAuth2 号，收不到达人回信</span>');
}

/* ---------- 第 2 步：名单（粘贴 / Excel 两个入口，同一个结果） ---------- */
function downloadTemplate() {
  window.open('/api/mail-blaster/outreach/list-template.xlsx', '_blank');
}

async function doParse(btn) {
  if (!editable()) return toast('已发送的活动不能修改名单，请新建活动', true);
  if (JOB && !confirm('替换名单将建立新活动，原活动草稿会保留。继续？')) return;
  const text = document.getElementById('list-text').value;
  if (!text.trim()) return toast('先粘贴名单，或上传 Excel', true);
  btn.disabled = true;
  try {
    renderParsed(await api('/api/mail-blaster/outreach/parse-list', {
      method: 'POST', body: { text } }));
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
}

async function uploadXlsx(file) {
  if (!editable()) return toast('已发送的活动不能修改名单，请新建活动', true);
  if (JOB && !confirm('替换名单将建立新活动，原活动草稿会保留。继续？')) return;
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  document.getElementById('parse-info').textContent = '解析中…';
  try {
    renderParsed(await api('/api/mail-blaster/outreach/parse-list', {
      method: 'POST', body: form }));
  } catch (e) {
    document.getElementById('parse-info').textContent = '';
    toast(e.message, true);
  }
}

/* 粘贴和 Excel 走同一条路由、返回同一个结构，所以这里往下完全没有分支。 */
function renderParsed(data) {
  PARSED = data;
  const src = data.source === 'excel' ? `Excel「${esc(data.sheet || '')}」` : '粘贴';
  document.getElementById('parse-info').innerHTML =
    `${src} → <strong>${data.rows.length}</strong> 个收件人` +
    (data.columns.length ? `，${data.columns.length} 个自定义变量` : '');

  const box = document.getElementById('parse-report');
  const bits = [];
  if (data.errors.length)
    bits.push(`<div class="errbox">${data.errors.map(esc).join('<br>')}</div>`);
  if (data.duplicates.length)
    bits.push(`<div class="smtp-resp">重复邮箱只保留第一条：${data.duplicates.map(esc).join('、')}</div>`);
  if (data.suppressed.length)
    bits.push(`<div class="smtp-resp">在抑制名单里，已剔除：${data.suppressed.map(esc).join('、')}</div>`);
  box.innerHTML = bits.join('');

  document.getElementById('list-card').style.display = data.rows.length ? '' : 'none';
  document.getElementById('list-hint').textContent =
    data.columns.length
      ? `每一列都可以在模板里用 {{列名}} 引用。共 ${data.rows.length} 封。`
      : `名单里只有邮箱，没有可用的自定义变量。共 ${data.rows.length} 封。`;
  document.getElementById('list-head').innerHTML =
    '<th style="width:56px">#</th><th style="width:260px">收件邮箱</th>' +
    data.columns.map(c => `<th>${esc(c)}</th>`).join('');
  document.getElementById('list-rows').innerHTML = data.rows.map((r, i) => `
    <tr><td>${i + 1}</td><td class="mono">${esc(r.email)}</td>${
      data.columns.map(c => `<td>${esc(r.vars[c] || '')}</td>`).join('')}</tr>`).join('');

  // 名单里的每一列生成一个占位符
  document.getElementById('dynamic-ph').innerHTML = data.columns.map(c =>
    `<span class="ph" title="名单里的列" onclick="insertPh('{{${esc(c)}}}')">{{${esc(c)}}}</span>`).join('');
  JOB = null;
  document.getElementById('send-info').textContent = '';
  updateActivityControls();
}

function insertPh(token) {
  const body = document.getElementById('body');
  const subject = document.getElementById('subject');
  const target = document.activeElement === subject ? subject : body;
  insertAtCursor(target, token);
}

/* ---------- 第 4 步：附件（整批共用一组） ---------- */
async function uploadAttachments(files) {
  if (!editable()) return toast('活动已开始发送，附件不能修改', true);
  const picked = Array.from(files || []);
  if (!picked.length) return;
  // 服务端在建批次那步还会再判一次（这里的两条是「多次分开传」也要拦得住），
  // 但拦在上传前，用户不用等一次白跑的往返
  const kept = ATTACHMENTS.filter(a => !picked.some(f => f.name === a.filename));
  if (kept.length + picked.length > ATTACH_MAX_COUNT) {
    return toast(`最多带 ${ATTACH_MAX_COUNT} 个附件`, true);
  }
  const total = kept.reduce((s, a) => s + a.byte_size, 0) +
                picked.reduce((s, f) => s + f.size, 0);
  if (total > ATTACH_TOTAL_BYTES) {
    return toast(`附件合计 ${fmtSize(total)}，超过单封上限 ${fmtSize(ATTACH_TOTAL_BYTES)}`, true);
  }
  const form = new FormData();
  picked.forEach(f => form.append('files', f));
  try {
    const res = await api('/api/mail-blaster/attachments', { method: 'POST', body: form });
    // 同名的算重传，替换掉旧的那条，避免一封信里出现两个同名附件
    res.attachments.forEach(a => {
      const i = ATTACHMENTS.findIndex(x => x.filename === a.filename);
      if (i >= 0) ATTACHMENTS[i] = a; else ATTACHMENTS.push(a);
    });
    renderAttachments();
    toast(`已添加 ${res.attachments.length} 个附件`);
  } catch (e) { toast(e.message, true); }
}

function removeAttachment(id) {
  if (!editable()) return;
  ATTACHMENTS = ATTACHMENTS.filter(a => a.id !== id);
  renderAttachments();
}

function renderAttachments() {
  const total = ATTACHMENTS.reduce((s, a) => s + a.byte_size, 0);
  document.getElementById('attach-list').innerHTML = ATTACHMENTS.map(a => `
    <div class="attach-item">
      <span class="name">📎 ${esc(a.filename)}</span>
      <span class="size">${fmtSize(a.byte_size)}</span>
      <a href="/api/mail-blaster/attachments/${a.id}?name=${encodeURIComponent(a.filename)}"
         target="_blank">下载核对</a>
      <button class="rm" title="移除" onclick="removeAttachment(${a.id})">✕</button>
    </div>`).join('') +
    (ATTACHMENTS.length > 1
      ? `<div class="hint" style="margin:2px 0 0">合计 ${fmtSize(total)}</div>` : '');
}

/* ---------- 第 4 步：模板库 ---------- */
async function loadTemplates() {
  try { TEMPLATES = (await api('/api/mail-blaster/templates?mode=outreach')).templates; }
  catch (e) { return; }
  document.getElementById('tpl-list').innerHTML =
    '<option value="">— 载入已存模板 —</option>' +
    TEMPLATES.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('');
}

async function saveTpl() {
  const name = document.getElementById('tpl-name').value.trim();
  if (!name) return toast('给模板起个名字', true);
  try {
    await api('/api/mail-blaster/templates', { method: 'POST', body: {
      name, mode: 'outreach',
      subject: document.getElementById('subject').value,
      body: document.getElementById('body').value,
      signature: document.getElementById('signature').value } });
    await loadTemplates();
    toast('已保存');
  } catch (e) { toast(e.message, true); }
}

function loadTpl() {
  if (!editable()) return toast('活动已开始发送，模板不能修改', true);
  const t = TEMPLATES.find(x => String(x.id) === document.getElementById('tpl-list').value);
  if (!t) return;
  document.getElementById('subject').value = t.subject;
  document.getElementById('body').value = t.body_html;
  document.getElementById('signature').value = t.signature_html;
  document.getElementById('tpl-name').value = t.name;
  toast(`已载入「${t.name}」`);
}

async function delTpl() {
  const id = document.getElementById('tpl-list').value;
  if (!id) return toast('先选一个模板', true);
  try {
    await api(`/api/mail-blaster/templates/${id}?mode=outreach`, { method: 'DELETE' });
    await loadTemplates();
    toast('已删除');
  } catch (e) { toast(e.message, true); }
}

/* ---------- 第 5 步：建批次 → 预览 → 发送 ---------- */
function templatePayload() {
  return {
    name: document.getElementById('activity-name').value,
    sender_account_id: Number(document.getElementById('account').value) || null,
    attachments: ATTACHMENTS.map(a => ({id:a.id, filename:a.filename})),
    subject_tpl: document.getElementById('subject').value,
    body_tpl: document.getElementById('body').value,
    signature_tpl: document.getElementById('signature').value,
  };
}

async function ensureJob() {
  if (jobPromise) return jobPromise;
  jobPromise = persistJob();
  try { return await jobPromise; }
  finally { jobPromise = null; updateActivityControls(); }
}

async function persistJob() {
  if (JOB && JOB.job.status !== 'draft') return JOB;
  if (!PARSED || !PARSED.rows.length) { toast('先解析名单', true); return null; }
  const account = currentAccount();
  if (!account) { toast('先选一个发件账号', true); return null; }
  if (JOB) {
    JOB = await api(`/api/mail-blaster/jobs/${JOB.job.id}`, {method:'PUT', body:templatePayload()});
    return JOB;
  }
  JOB = await api('/api/mail-blaster/outreach/jobs', { method: 'POST', body: {
    sender_account_id: account.id, rows: PARSED.rows, ...templatePayload(),
    attachments: ATTACHMENTS.map(a => ({ id: a.id, filename: a.filename })) } });
  if (JOB.list.suppressed.length) {
    toast(`${JOB.list.suppressed.length} 个地址在抑制名单里，已剔除`);
  }
  return JOB;
}

async function doPreview(btn) {
  btn.disabled = true;
  try {
    const job = await ensureJob();
    if (!job) return;
    // 只传模板，绝不传 items —— sync_job 会把 items 里没带 sender_account_id
    // 的行置空，整批账号会被抹掉
    PREVIEWS = (await api(`/api/mail-blaster/jobs/${job.job.id}/preview`,
      { method: 'POST', body: job.job.status === 'draft' ? templatePayload() : {} })).previews;
    previewIndex = 0;
    showPreview();
    openModal('preview-modal');
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
}

function showPreview() {
  const p = PREVIEWS[previewIndex];
  if (!p) return;
  document.getElementById('preview-idx').textContent =
    `第 ${previewIndex + 1} / ${PREVIEWS.length} 封`;
  document.getElementById('preview-head').innerHTML =
    `<div>发件人：${esc(p.from_line || '')}</div>` +
    `<div>收件人：${esc(p.to_line || '')}</div>` +
    `<div>主题：${esc(p.subject || '')}</div>` +
    // 附件列的是服务端记在这个批次上的，不是页面上还没提交的那份
    ((p.attachments || []).length
      ? `<div>附件：${p.attachments.map(a =>
          `📎 ${esc(a.filename)}（${fmtSize(a.byte_size)}）`).join('　')}</div>` : '');
  document.getElementById('preview-body').innerHTML = p.html || `<em>${esc(p.error || '')}</em>`;
}

function stepPreview(d) {
  previewIndex = Math.max(0, Math.min(PREVIEWS.length - 1, previewIndex + d));
  showPreview();
}

async function doSend(btn) {
  const account = currentAccount();
  if (!account) return toast('先选一个发件账号', true);
  if (!PARSED || !PARSED.rows.length) return toast('先解析名单', true);
  const n = PARSED.rows.length;
  const mins = Math.round(n * (GAP_MIN + GAP_MAX) / 2 / 60);
  if (!confirm(`给 ${n} 个达人发建联邮件？\n\n` +
               `发件账号：${account.email}（今日上限 ${
                 account.daily_limit === null ? '不限' : account.daily_limit}）\n` +
               `节奏：每封间隔 ${GAP_MIN}–${GAP_MAX} 秒，预计约 ${mins} 分钟\n` +
               `发送窗口：${SEND_WINDOW}\n` +
               `附件：${ATTACHMENTS.length
                 ? ATTACHMENTS.map(a => a.filename).join('、') : '无'}\n\n` +
               `每封都会带退订出口，抑制名单里的地址不会收到。`)) return;
  btn.disabled = true;
  try {
    const job = await ensureJob();
    if (!job) return;
    await api(`/api/mail-blaster/jobs/${job.job.id}/send`,
      { method: 'POST', body: job.job.status === 'draft' ? templatePayload() : {} });
    JOB.job.status = 'queued';
    updateActivityControls();
    document.getElementById('progress').style.display = '';
    startPolling(job.job.id);
    toast('已入队，正在后台发送');
  } catch (e) { toast(e.message, true); }
  finally { updateActivityControls(); }
}

function startPolling(jobId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => refresh(jobId), 2000);
  refresh(jobId);
}

async function refresh(jobId) {
  let data;
  try { data = await api(`/api/mail-blaster/jobs/${jobId}/status`); }
  catch (e) { clearInterval(pollTimer); return toast(e.message, true); }
  if (!JOB || JOB.job.id !== jobId) return;
  JOB = data; updateActivityControls(); renderItemStatuses(data);
  document.getElementById('progress').style.display = '';
  const c = data.counts;
  const done = (c.sent || 0) + (c.failed || 0) + (c.skipped || 0) + (c.unknown || 0);
  document.getElementById('progress').firstElementChild.style.width =
    `${c.total ? Math.round(done / c.total * 100) : 0}%`;
  document.getElementById('send-info').innerHTML =
    `已发 ${c.sent || 0}　失败 ${c.failed || 0}　跳过 ${c.skipped || 0}　待核实 ${c.unknown || 0}　共 ${c.total}`;

  if (data.job.status === 'done') {
    clearInterval(pollTimer);
    const reason = data.job.paused_reason;
    document.getElementById('pause-box').innerHTML =
      reason ? `<div class="banner banner-warn">${esc(reason)}</div>` : '';
    toast(reason ? '批次已暂停' : '发送完成', !!reason);
  }
}

/* ---------- 抑制名单 ---------- */
async function openSuppression() {
  openModal('suppression-modal');
  await renderSuppression();
}

async function renderSuppression() {
  let items;
  try { items = (await api('/api/mail-blaster/suppression')).items; }
  catch (e) { return toast(e.message, true); }
  document.getElementById('sup-empty').style.display = items.length ? 'none' : '';
  document.getElementById('sup-rows').innerHTML = items.map(r => `
    <tr>
      <td class="mono">${esc(r.email)}</td>
      <td>${esc(r.reason || '—')}</td>
      <td>${({manual: '手动', reply: '回信', bounce: '退信'})[r.source] || esc(r.source)}</td>
      <td><button class="small danger"
                  onclick="removeSuppression('${esc(r.email)}')">移除</button></td>
    </tr>`).join('');
}

async function addSuppression() {
  const email = document.getElementById('sup-email').value.trim();
  if (!email) return toast('填个邮箱', true);
  try {
    await api('/api/mail-blaster/suppression', { method: 'POST', body: {
      email, reason: document.getElementById('sup-reason').value.trim() } });
    document.getElementById('sup-email').value = '';
    document.getElementById('sup-reason').value = '';
    await renderSuppression();
  } catch (e) { toast(e.message, true); }
}

async function removeSuppression(email) {
  try {
    await api(`/api/mail-blaster/suppression/${encodeURIComponent(email)}`, { method: 'DELETE' });
    await renderSuppression();
  } catch (e) { toast(e.message, true); }
}

/* ---------- 启动 ---------- */
/* 在 DOMContentLoaded 里绑，不要学素材页那样在模块顶层直接取元素——
   顶层绑定要求 DOM 一定已经在了，页面结构一改就抛。 */
document.addEventListener('DOMContentLoaded', () => {
  const drop = document.getElementById('drop-xlsx');
  const input = document.getElementById('xlsx-input');
  drop.addEventListener('click', () => input.click());
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => {
    e.preventDefault(); drop.classList.remove('over');
    uploadXlsx(e.dataTransfer.files[0]);
  });
  input.addEventListener('change', () => {
    uploadXlsx(input.files[0]);
    input.value = '';        // 同一个文件连传两次也要能触发 change
  });

  const adrop = document.getElementById('drop-attach');
  const ainput = document.getElementById('attach-input');
  adrop.addEventListener('click', () => ainput.click());
  adrop.addEventListener('dragover', e => { e.preventDefault(); adrop.classList.add('over'); });
  adrop.addEventListener('dragleave', () => adrop.classList.remove('over'));
  adrop.addEventListener('drop', e => {
    e.preventDefault(); adrop.classList.remove('over');
    uploadAttachments(e.dataTransfer.files);
  });
  ainput.addEventListener('change', () => {
    uploadAttachments(ainput.files);
    ainput.value = '';
  });

  loadPool();
  loadTemplates();
  loadActivities();
});
