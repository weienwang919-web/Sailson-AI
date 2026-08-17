/* mail-blaster 素材提交页。

   通用小工具（toast / api / esc / openModal / patchRows / usable / fmtSize / STATUS_TEXT）
   在 mail_blaster_common.js，发件账号池在 mail_blaster_accounts.js，
   两个都由模板先于本文件加载，KOL 建联页共用同一份。 */

let JOB = null, POOL = [], TEMPLATES = [], PREVIEWS = [], COOLDOWNS = {};
let COOLDOWN_DAYS = 7, previewIndex = 0, pollTimer = null;

/* 每行的附件：itemId -> [{id, filename, mime, byte_size}]。
   刻意放模块级而不是塞进 JOB.items —— refresh() 每 2.5 秒
   `JOB = {...JOB, ...data}` 把 items 整个换成服务端的版本，
   写在那里的未提交编辑会被轮询悄悄冲掉。
   也不能只放 DOM：<td> 存不下结构化数据，而 collect() 要按 id 取回来。 */
let ATTACH_BY_ITEM = new Map();
let pendingAttachItemId = null;
// Map 是从服务端已存的绑定播种的，所以「Map 非空」不等于「有没存的改动」。
// 单独记一个脏标记，落库成功就清掉，否则发完信关页面还要被拦一下。
let attachDirty = false;

/* ---------- 账号池 ---------- */
async function loadPool() {
  try { POOL = (await api('/api/mail-blaster/accounts?purpose=material')).accounts; }
  catch (e) { return toast(e.message, true); }
  if (!POOL.some(usable)) {
    document.getElementById('upload-info').innerHTML =
      '<span style="color:var(--err)">账号池里还没有「已启用 + 测试通过」的发件账号，' +
      '先点右上角「发件账号池」加几个。</span>';
  }
  renderDomainPoolHint();
}

/* ---------- Excel 导入 ---------- */
const dropX = document.getElementById('drop-xlsx');
const inputX = document.getElementById('xlsx-input');
dropX.addEventListener('click', () => inputX.click());
dropX.addEventListener('dragover', e => { e.preventDefault(); dropX.classList.add('over'); });
dropX.addEventListener('dragleave', () => dropX.classList.remove('over'));
dropX.addEventListener('drop', e => {
  e.preventDefault(); dropX.classList.remove('over');
  const f = [...e.dataTransfer.files].find(x => x.name.toLowerCase().endsWith('.xlsx'));
  f ? uploadXlsx(f) : toast('请拖一个 .xlsx 文件', true);
});
inputX.addEventListener('change', () => {
  if (inputX.files.length) uploadXlsx(inputX.files[0]);
  inputX.value = '';
});

function replacementDomainSettings() {
  const enabled = document.getElementById('replace-domain-enabled').checked;
  const domain = document.getElementById('replacement-domain').value.trim();
  return { replace_domain_enabled: enabled, replacement_domain: domain };
}

function ensureReplacementDomain() {
  const s = replacementDomainSettings();
  if (s.replace_domain_enabled && !s.replacement_domain) {
    toast('已勾选替换域名，请填写目标域名', true);
    return false;
  }
  return true;
}

function toggleReplacementDomain() {
  const enabled = document.getElementById('replace-domain-enabled').checked;
  const input = document.getElementById('replacement-domain');
  input.disabled = !enabled;
  if (enabled) input.focus();
  renderDomainPoolHint();
}

/* 勾上之后当场说清楚这批会用哪些号发。服务端也会拦（create_job_from_excel），
   但那要等上传完 Excel 才看得到，这里先给个数。 */
function renderDomainPoolHint() {
  const box = document.getElementById('replace-domain-hint');
  if (!box) return;
  if (!document.getElementById('replace-domain-enabled').checked) {
    box.textContent = '';
    return;
  }
  const ok = POOL.filter(a => usable(a) && DOMAIN_REPLACEMENT_PROVIDERS.includes(a.provider));
  box.innerHTML = ok.length
    ? `将只用这 ${ok.length} 个账号发送：${ok.map(a => esc(a.email)).join('、')}` +
      '（其余账号的服务商会拒绝代发，已自动排除）'
    : '<span style="color:var(--err)">账号池里没有能替换发件域名的账号。' +
      '目前只有网易 163 的号支持，微软 / Gmail 会拒绝代发。</span>';
}

async function uploadXlsx(file) {
  if (!ensureReplacementDomain()) return;
  const form = new FormData();
  const domainSettings = replacementDomainSettings();
  form.append('recipient', document.getElementById('recipient').value.trim());
  form.append('subject_tpl', document.getElementById('subject-tpl').value);
  form.append('body_tpl', document.getElementById('body-tpl').value);
  form.append('signature_tpl', document.getElementById('signature-tpl').value);
  form.append('ocr', document.getElementById('use-ocr').checked ? '1' : '0');
  form.append('replace_domain_enabled', domainSettings.replace_domain_enabled ? '1' : '0');
  form.append('replacement_domain', domainSettings.replacement_domain);
  form.append('file', file);

  document.getElementById('upload-info').textContent = `解析中… ${file.name}（含 OCR 时会慢一些）`;
  document.getElementById('excel-report').innerHTML = '';
  try {
    JOB = await api('/api/mail-blaster/jobs/from-excel', { method: 'POST', body: form });
    ATTACH_BY_ITEM = new Map();   // 换了 Excel，上一批的行→附件绑定全作废
    attachDirty = false;
    await loadCooldowns(JOB.items.map(i => i.recipient || ''));
    const x = JOB.excel;
    const short = JOB.pool_size - JOB.items.length;
    document.getElementById('upload-info').innerHTML =
      `从「${esc(x.sheet)}」表头第 ${x.header_row} 行读到 <strong>${x.total_rows}</strong> 行，` +
      `入队 <strong>${x.imported}</strong> 行` +
      (x.skipped.length ? `，跳过 <strong>${x.skipped.length}</strong> 行` : '') +
      `，可用账号 <strong>${JOB.pool_size}</strong> 个。` +
      (x.has_recipient ? '' : ' <span style="color:var(--warn)">没有「收件邮箱」列，用上面填的兜底值。</span>') +
      (x.no_image ? ` <span style="color:var(--warn)">其中 ${x.no_image} 行没有图片，` +
                    `需要在下面逐行点 📎 挂附件，否则不会发。</span>` : '') +
      (short < 0 ? ` <span style="color:var(--warn)">账号不够，最后 ${-short} 行没配上账号。</span>` : '');
    renderReport(x);
    renderRows();
    ['pair-card', 'tpl-card', 'send-card'].forEach(id =>
      document.getElementById(id).style.display = '');
    if (x.needs_ocr) startOcrPolling();
  } catch (e) {
    document.getElementById('upload-info').textContent = '';
    document.getElementById('excel-report').innerHTML = `<div class="errbox">${esc(e.message)}</div>`;
  }
}

function renderReport(x) {
  const blocks = [];
  if (x.skipped.length) blocks.push(
    `<div class="smtp-resp"><strong>跳过 ${x.skipped.length} 行（不会发）：</strong><br>` +
    x.skipped.map(s => `第 ${s.row} 行 —— ${esc(s.reason)}`).join('<br>') + '</div>');
  if (x.cooldown && x.cooldown.length) blocks.push(
    `<div class="smtp-resp"><strong>发件账号轮换：</strong>${x.cooldown_days} 天内给同一收件人发过的已避开。` +
    `以下几行池子不够，用了冷却期内的账号：<br>` + x.cooldown.map(esc).join('<br>') + '</div>');
  if (x.notices && x.notices.length) blocks.push(
    `<div class="smtp-resp"><strong>没有图片的 ${x.notices.length} 行（照常导入，挂个附件就能发）：</strong><br>` +
    x.notices.map(esc).join('<br>') + '</div>');
  if (x.errors.length) blocks.push(`<div class="errbox">${x.errors.map(esc).join('<br>')}</div>`);
  document.getElementById('excel-report').innerHTML = blocks.join('');
}

async function loadCooldowns(recipients) {
  COOLDOWNS = {};
  for (const r of [...new Set(recipients.filter(Boolean))]) {
    try {
      const d = await api('/api/mail-blaster/accounts?cooldown_for=' + encodeURIComponent(r));
      COOLDOWN_DAYS = d.cooldown_days;
      const m = {};
      d.accounts.forEach(a => { if (a.cooldown_until) m[a.id] = a.cooldown_until; });
      COOLDOWNS[r] = m;
    } catch (e) { /* 查不到就当没冷却，不挡流程 */ }
  }
}

/* ---------- 配对表 ---------- */
function statusCell(item) {
  let h = `<span class="pill pill-${item.status}">${STATUS_TEXT[item.status] || item.status}</span>`;
  if (item.status === 'failed') {
    h += `<button class="small" style="margin-top:6px" onclick="resend(${item.id})">重发这一封</button>` +
         `<div class="errbox">${esc(item.error)}</div>`;
  }
  if (item.status === 'skipped' && item.error) {
    h += `<div class="smtp-resp">${esc(item.error)}</div>`;
    // 📎 前缀 = 只是缺内容（不是抑制名单那种永久决定）。补完附件这一封可以单独发，
    // 不给按钮的话用户补了附件也无处可点，只能重传整个 Excel。
    if (item.error.startsWith('📎')) {
      h += `<button class="small" style="margin-top:6px" onclick="resend(${item.id})">发这一封</button>`;
    }
  }
  if (item.status === 'sent' && item.smtp_response) {
    h += `<div class="smtp-resp" title="服务器接收时的应答。250 只代表它收下了，不代表会投递。">` +
         `${esc(item.smtp_response)}</div>`;
  }
  return h;
}

function renderRows() {
  document.getElementById('pair-rows').innerHTML = JOB.items.map(item => {
    const blocked = COOLDOWNS[item.recipient || ''] || {};
    const options = ['<option value="">— 不发 —</option>'].concat(POOL.map(a => {
      const tag = !usable(a) ? '（未通过测试）'
                : (blocked[a.id] ? `（${COOLDOWN_DAYS}天内给这人发过）` : '');
      return `<option value="${a.id}" ${a.id === item.sender_account_id ? 'selected' : ''}>` +
             `${esc(a.email)}${tag}</option>`;
    })).join('');
    const custom = ITEM_FIELDS.map(([k]) =>
      `<td><input type="text" class="p-var" data-key="${k}" ` +
      `value="${esc((item.vars || {})[k] || '')}" placeholder="—"></td>`).join('');
    return `
      <tr data-id="${item.id}" data-img="${item.image_url ? 1 : 0}">
        <td>${item.image_url ? `<img class="thumb" src="${item.image_url}">` : ''}</td>
        <td class="mono">${esc(item.recipient) || '<span class="dim">用兜底值</span>'}</td>
        <td><select class="p-account" onchange="onAccountChange(${item.id}, this.value)">${options}</select></td>
        <td><input type="text" class="p-display" value="${esc(item.from_display)}"></td>
        <td class="c-attach" data-attach-for="${item.id}"></td>
        ${custom}
        <td class="c-status">${statusCell(item)}</td>
      </tr>`;
  }).join('');
  // 服务端已经记着的绑定灌进 Map，然后 Map 就是唯一真相 ——
  // refresh() 之后 JOB.items 是服务端版本，再从它读附件会盖掉未提交的编辑。
  JOB.items.forEach(item => {
    if (!ATTACH_BY_ITEM.has(item.id)) ATTACH_BY_ITEM.set(item.id, item.attachments || []);
    renderRowAttachments(item.id);
  });
}

/* ---------- 逐行附件（素材的另一种形态） ---------- */
function pickRowAttachment(itemId) {
  pendingAttachItemId = itemId;
  document.getElementById('row-attach-input').click();
}

const inputA = document.getElementById('row-attach-input');
inputA.addEventListener('change', () => {
  const itemId = pendingAttachItemId;
  const picked = [...inputA.files];
  // 必须清空：不清的话连着传同一个文件第二次不会触发 change，看起来像卡死了
  inputA.value = '';
  pendingAttachItemId = null;
  if (itemId != null && picked.length) uploadRowAttachments(itemId, picked);
});

// 行→附件的绑定要点了预览/发送才落库（和账号、显示名、OCR 填的 vars 一个待遇）。
// 字节早就进了 mb_attachments，丢的只是这层映射，但重新挂一遍也烦，提醒一句。
window.addEventListener('beforeunload', e => {
  if (attachDirty) e.preventDefault();
});

async function uploadRowAttachments(itemId, picked) {
  const have = ATTACH_BY_ITEM.get(itemId) || [];
  // 服务端 sync_item_attachments 还会再判一次（分几次传也要拦得住），
  // 这里只是省掉一趟白跑的往返
  const kept = have.filter(a => !picked.some(f => f.name === a.filename));
  if (kept.length + picked.length > ATTACH_MAX_COUNT) {
    return toast(`每行最多带 ${ATTACH_MAX_COUNT} 个附件`, true);
  }
  const total = kept.reduce((s, a) => s + a.byte_size, 0) +
                picked.reduce((s, f) => s + f.size, 0);
  if (total > ATTACH_TOTAL_BYTES) {
    return toast(`这一行的附件合计 ${fmtSize(total)}，超过单封上限 ${fmtSize(ATTACH_TOTAL_BYTES)}`, true);
  }
  const form = new FormData();
  picked.forEach(f => form.append('files', f));
  try {
    const res = await api('/api/mail-blaster/attachments', { method: 'POST', body: form });
    const list = ATTACH_BY_ITEM.get(itemId) || [];
    res.attachments.forEach(a => {      // 同名算重传，替换掉旧的，别让一封信里出现两个同名附件
      const i = list.findIndex(x => x.filename === a.filename);
      if (i >= 0) list[i] = a; else list.push(a);
    });
    ATTACH_BY_ITEM.set(itemId, list);
    attachDirty = true;
    renderRowAttachments(itemId);
    toast(`这一行加了 ${res.attachments.length} 个附件（点预览或发送时写进批次）`);
  } catch (e) { toast(e.message, true); }
}

function removeRowAttachment(itemId, attachmentId) {
  ATTACH_BY_ITEM.set(itemId,
    (ATTACH_BY_ITEM.get(itemId) || []).filter(a => a.id !== attachmentId));
  attachDirty = true;
  renderRowAttachments(itemId);
}

function renderRowAttachments(itemId) {
  const td = document.querySelector(`#pair-rows td[data-attach-for="${itemId}"]`);
  if (!td) return;
  const list = ATTACH_BY_ITEM.get(itemId) || [];
  const hasImage = td.closest('tr').dataset.img === '1';
  td.innerHTML =
    `<button class="attach-add" onclick="pickRowAttachment(${itemId})">📎 加附件</button>` +
    (!hasImage && !list.length
      ? '<div class="attach-warn">没图也没附件，不会发</div>' : '') +
    (list.length ? '<div class="attach-list">' + list.map(a => `
      <div class="attach-item" title="${esc(a.filename)}（${fmtSize(a.byte_size)}）">
        <a class="name" target="_blank"
           href="/api/mail-blaster/attachments/${a.id}?name=${encodeURIComponent(a.filename)}"
        >📎 ${esc(a.filename)}</a>
        <button class="rm" title="移除"
                onclick="removeRowAttachment(${itemId}, ${a.id})">✕</button>
      </div>`).join('') + '</div>' : '');
}

function onAccountChange(itemId, accountId) {
  const a = POOL.find(x => String(x.id) === String(accountId));
  const tr = document.querySelector(`tr[data-id="${itemId}"]`);
  if (tr) tr.querySelector('.p-display').value = a ? a.effective_display_name : '';
}

function collect() {
  const items = [...document.querySelectorAll('#pair-rows tr')].map(tr => {
    const vars = {};
    tr.querySelectorAll('.p-var').forEach(el => { vars[el.dataset.key] = el.value.trim(); });
    const sel = tr.querySelector('.p-account').value;
    const id = Number(tr.dataset.id);
    return { id, sender_account_id: sel ? Number(sel) : null,
             from_display: tr.querySelector('.p-display').value, vars,
             // 只给 doSend 的提示用，服务端不读 —— 那边的真相是 mb_items.image_id
             has_image: tr.dataset.img === '1',
             // 只传 {id, filename}，字节在点选那一刻就已经进库了。
             // 大小不传：采信前端的 byte_size 等于把上限交给调用方自己填。
             attachments: (ATTACH_BY_ITEM.get(id) || [])
                            .map(a => ({ id: a.id, filename: a.filename })) };
  });
  return {
    recipient: document.getElementById('recipient').value.trim(),
    subject_tpl: document.getElementById('subject-tpl').value,
    body_tpl: document.getElementById('body-tpl').value,
    signature_tpl: document.getElementById('signature-tpl').value,
    ...replacementDomainSettings(),
    items,
  };
}

/* ---------- 占位符 / 模板库 ---------- */
let lastFocused = null;
['body-tpl', 'signature-tpl', 'subject-tpl'].forEach(id =>
  document.getElementById(id).addEventListener('focus', e => { lastFocused = e.target; }));
function insertPh(token) {
  insertAtCursor(lastFocused || document.getElementById('body-tpl'), token);
}

async function loadTemplates() {
  try { TEMPLATES = (await api('/api/mail-blaster/templates')).templates; } catch (e) { return; }
  document.getElementById('tpl-picker').innerHTML = ['<option value="">— 模板库 —</option>']
    .concat(TEMPLATES.map(t => `<option value="${t.id}">${esc(t.name)}</option>`)).join('');
  document.getElementById('tpl-info').textContent =
    TEMPLATES.length ? `已存 ${TEMPLATES.length} 个模板` : '还没存过模板';
}

function applyTemplate(id) {
  const t = TEMPLATES.find(x => String(x.id) === String(id));
  if (!t) return;
  document.getElementById('subject-tpl').value = t.subject;
  document.getElementById('body-tpl').value = t.body_html;
  document.getElementById('signature-tpl').value = t.signature_html;
  toast(`已载入「${t.name}」`);
}

async function saveTemplate() {
  const picked = TEMPLATES.find(x => String(x.id) === document.getElementById('tpl-picker').value);
  const name = prompt('模板名字（用已有的名字会覆盖它）', picked ? picked.name : '');
  if (name === null || !name.trim()) return;
  try {
    await api('/api/mail-blaster/templates', { method: 'POST', body: {
      name: name.trim(),
      subject: document.getElementById('subject-tpl').value,
      body: document.getElementById('body-tpl').value,
      signature: document.getElementById('signature-tpl').value } });
    await loadTemplates();
    toast(`已保存「${name.trim()}」，下次打开自动带出来`);
  } catch (e) { toast(e.message, true); }
}

async function deleteTemplate() {
  const id = document.getElementById('tpl-picker').value;
  const t = TEMPLATES.find(x => String(x.id) === String(id));
  if (!t) return toast('先在下拉里选一个模板', true);
  if (!confirm(`删除模板「${t.name}」？`)) return;
  try { await api(`/api/mail-blaster/templates/${id}`, { method: 'DELETE' }); await loadTemplates(); }
  catch (e) { toast(e.message, true); }
}

/* ---------- 预览 ---------- */
async function doPreview() {
  if (!JOB) return toast('先导入 Excel', true);
  if (!ensureReplacementDomain()) return;
  try {
    PREVIEWS = (await api(`/api/mail-blaster/jobs/${JOB.job.id}/preview`,
                          { method: 'POST', body: collect() })).previews;
    attachDirty = false;   // 这一趟 sync_job 已经把绑定写进库了
    previewIndex = 0; showPreview(); openModal('preview-modal');
  } catch (e) { toast(e.message, true); }
}

function showPreview() {
  const p = PREVIEWS[previewIndex];
  document.getElementById('preview-pos').textContent = `${previewIndex + 1} / ${PREVIEWS.length}`;
  if (!p) return;
  document.getElementById('preview-body').innerHTML = p.html ? `
    <div class="mail-preview">
      <div class="mail-head">
        <div><span>发件人</span>${esc(p.from_line)}</div>
        <div><span>收件人</span>${esc(p.to_line || '（没有收件邮箱）')}</div>
        <div><span>主题</span><strong>${esc(p.subject)}</strong></div>
        ${(p.attachments || []).length
          ? `<div><span>附件</span>${p.attachments.map(a =>
              `📎 ${esc(a.filename)}（${fmtSize(a.byte_size)}）`).join('　')}</div>` : ''}
      </div>
      <div class="mail-body">${p.html}</div>
    </div>` : `<div class="errbox">${esc(p.error || '无法渲染')}</div>`;
}

function stepPreview(d) {
  if (!PREVIEWS.length) return;
  previewIndex = (previewIndex + d + PREVIEWS.length) % PREVIEWS.length;
  showPreview();
}

/* ---------- 发送（只入队，worker 真发）---------- */
async function doSend() {
  if (!JOB) return toast('先导入 Excel', true);
  if (!ensureReplacementDomain()) return;
  const state = collect();
  const picked = state.items.filter(i => i.sender_account_id);
  // 图和附件都没有的行 worker 会标成「已跳过」。这里只提示不拦：
  // 200 行里 1 行空就整批拒绝是敌意的，跳过本来就是可恢复的。
  const blank = picked.filter(i => !i.has_image && !i.attachments.length);
  const n = picked.length - blank.length;
  if (!picked.length) return toast('没有任何一行选了发件账号', true);
  if (!n) return toast('没有任何一行可以发送：每行至少要有一张图片，或者点 📎 挂一个附件', true);
  if (ocrTimer) return toast('营业执照识别还在进行，等它完成再发', true);
  if (!confirm(`确认发送 ${n} 封？` +
      (blank.length ? `\n\n另有 ${blank.length} 行既没图也没附件，会标成「已跳过」不发。` +
                      `\n给它们补上附件后再点一次发送，会自动捡回来。` : '') +
      `\n\n任务会交给后台 worker 执行，提交后可以关掉页面。`)) return;

  document.getElementById('send-btn').disabled = true;
  try {
    await api(`/api/mail-blaster/jobs/${JOB.job.id}/send`, { method: 'POST', body: state });
    attachDirty = false;
    document.getElementById('progress').style.display = '';
    toast('已提交给后台，开始发送');
    startPolling();
  } catch (e) {
    document.getElementById('send-btn').disabled = false;
    toast(e.message, true);
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(refresh, 2500);
  refresh();
}

async function refresh() {
  if (!JOB) return;
  let data;
  try { data = await api(`/api/mail-blaster/jobs/${JOB.job.id}/status`); } catch (e) { return; }
  JOB = { ...JOB, ...data };
  patchRows(document.getElementById('pair-rows'), data.items, { '.c-status': statusCell });

  const c = data.counts;
  const done = (c.sent || 0) + (c.failed || 0) + (c.skipped || 0);
  document.getElementById('progress').firstElementChild.style.width =
    `${c.total ? (done / c.total * 100) : 0}%`;
  document.getElementById('send-summary').textContent =
    `${done} / ${c.total} 已处理 · 成功 ${c.sent || 0} · 失败 ${c.failed || 0}` +
    (c.skipped ? ` · 跳过 ${c.skipped}` : '');

  if (data.job.status === 'done') {
    clearInterval(pollTimer); pollTimer = null;
    document.getElementById('send-btn').disabled = false;
    document.getElementById('pause-box').innerHTML = data.job.paused_reason
      ? `<div class="errbox">${esc(data.job.paused_reason)}</div>` : '';
    toast(c.failed ? `发送结束，有 ${c.failed} 封失败` : '全部发送成功', !!c.failed);
  }
}

async function resend(itemId) {
  try {
    // 带上这一行的最新状态。「📎 缺内容」的行是发完之后才补的附件，
    // 而绑定在点预览/发送前只活在 ATTACH_BY_ITEM 里 —— 不一起传过去，
    // 服务端查不到附件会原样再跳过一次，按钮看着就像坏的。
    const mine = collect().items.filter(i => i.id === itemId);
    await api(`/api/mail-blaster/items/${itemId}/resend`,
              { method: 'POST', body: { items: mine } });
    attachDirty = false;   // 这一趟也走了 sync_job
    toast('已提交重发'); startPolling();
    setTimeout(() => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; refresh(); } }, 30000);
  } catch (e) { toast(e.message, true); }
}


/* ---------- 营业执照识别（worker 里跑，这里逐行轮询填入）---------- */
let ocrTimer = null;
let ocrSeen = 0;

function startOcrPolling() {
  if (ocrTimer) clearInterval(ocrTimer);
  ocrSeen = 0;
  document.getElementById('ocr-box').innerHTML =
    '<div class="smtp-resp" id="ocr-line">营业执照识别已提交后台，等待 worker 领取…</div>';
  ocrTimer = setInterval(pollOcr, 2000);
  pollOcr();
}

/* 只填空着的格子。用户手改过的一律不覆盖 —— 识别是来补空缺的，不是来抢方向盘的。 */
function fillVarsFromItems(items) {
  let filled = 0;
  for (const item of items) {
    const tr = document.querySelector(`#pair-rows tr[data-id="${item.id}"]`);
    if (!tr) continue;
    tr.querySelectorAll('.p-var').forEach(input => {
      const incoming = (item.vars || {})[input.dataset.key] || '';
      if (incoming && !input.value.trim()) {
        input.value = incoming;
        input.classList.add('just-filled');
        setTimeout(() => input.classList.remove('just-filled'), 1600);
        filled++;
      }
    });
  }
  return filled;
}

async function pollOcr() {
  if (!JOB) return;
  let data;
  try { data = await api(`/api/mail-blaster/jobs/${JOB.job.id}/status`); } catch (e) { return; }

  const st = data.job.ocr_status;
  const rep = data.job.ocr_report || { total: 0, notes: [] };
  const done = rep.notes.length;

  // 不管有没有跑完，先把已经识别出来的填进去
  JOB = { ...JOB, items: data.items };
  fillVarsFromItems(data.items);

  if (st === 'running' || st === 'pending') {
    if (done !== ocrSeen) {
      ocrSeen = done;
      renderOcrProgress(rep, false);
    } else if (st === 'pending') {
      const line = document.getElementById('ocr-line');
      if (line) line.textContent = '营业执照识别已提交后台，等待 worker 领取…';
    }
    return;
  }

  clearInterval(ocrTimer); ocrTimer = null;
  if (st === 'failed') {
    document.getElementById('ocr-box').innerHTML =
      '<div class="errbox">营业执照识别失败，请手填 name / id / number（看 worker 日志排查）</div>';
    return;
  }
  renderOcrProgress(rep, true);
}

function renderOcrProgress(rep, finished) {
  const notes = rep.notes || [];
  const total = rep.total || notes.length;
  const pct = total ? Math.round(notes.length / total * 100) : 100;

  const lines = notes.filter(n => Object.keys(n.filled || {}).length).map(n =>
    `第 ${n.row} 行 —— ` + Object.entries(n.filled)
      .map(([k, v]) => `${k}: ${esc(v)}`).join('，'));
  const bad = notes.filter(n => n.error);
  const warns = notes.filter(n => n.warn);

  let h = '<div class="smtp-resp" id="ocr-line">';
  h += finished
    ? `<strong>营业执照识别完成</strong>（${notes.length}/${total}）`
    : `<strong>营业执照识别中…</strong> ${notes.length}/${total}`;
  h += `<div class="progress" style="margin:8px 0 4px"><div style="width:${pct}%"></div></div>`;
  if (lines.length) h += lines.join('<br>');
  else if (finished) h += '没有补上任何字段';
  if (bad.length) h += (lines.length ? '<br>' : '') +
    bad.map(n => `第 ${n.row} 行 —— ${esc(n.error)}`).join('<br>');
  h += '</div>';
  if (warns.length) h += '<div class="errbox"><strong>需要人工核对：</strong><br>' +
    warns.map(n => `第 ${n.row} 行 —— ${esc(n.warn)}`).join('<br>') + '</div>';
  document.getElementById('ocr-box').innerHTML = h;
}

/* ---------- 发信记录 ---------- */
async function openHistory() { openModal('history-modal'); await loadHistory(); }

async function loadHistory() {
  const q = document.getElementById('hist-q').value.trim();
  let items = [];
  try { items = (await api('/api/mail-blaster/history?q=' + encodeURIComponent(q))).items; }
  catch (e) { return toast(e.message, true); }
  document.getElementById('hist-empty').style.display = items.length ? 'none' : '';
  document.getElementById('hist-info').textContent = `${items.length} 条`;
  document.getElementById('hist-rows').innerHTML = items.map(h => `
    <tr>
      <td class="mono">${esc((h.sent_at || '').replace('T', ' ').slice(0, 16))}</td>
      <td class="mono">${esc(h.recipient)}</td>
      <td class="mono">${esc(h.material_id) || '<span class="dim">—</span>'}</td>
      <td>${esc(h.material_name)}${h.subject ? `<div class="dim">${esc(h.subject)}</div>` : ''}</td>
      <td class="mono">${esc(h.sender_email)}</td>
    </tr>`).join('');
}

loadPool();
loadTemplates();
