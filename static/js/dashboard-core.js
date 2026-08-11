/* 看板公共工具。两个页面（官号总览 / 矩阵号看板）曾各自内联一份逐字相同的实现，
   这里收敛为唯一来源。 */

function fmt(v) {
  if (v === null || v === undefined || v === '') return '-';
  if (typeof v === 'number') return v.toLocaleString();
  return String(v);
}

function esc(v) {
  return String(v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function pct(v) {
  if (v === null || v === undefined || v === '') return '-';
  const n = Number(v);
  if (Number.isNaN(n)) return '-';
  return (n * 100).toFixed(2) + '%';
}

function fmtDelta(v) {
  if (v === null || v === undefined) return '-';
  const cls = v > 0 ? 'stat-up' : (v < 0 ? 'stat-down' : '');
  const sign = v > 0 ? '+' : '';
  return `<span class="${cls}">${sign}${fmt(v)}</span>`;
}

/* 大数字缩写，用于图表轴和紧凑位置 */
function fmtShort(v) {
  const n = Number(v || 0);
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(1) + '亿';
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + '万';
  return n.toLocaleString();
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status === 'error') throw new Error(data.message || ('HTTP ' + res.status));
  return data;
}

function setStatus(elId, text, isError) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = text || '';
  el.className = 'status' + (isError ? ' err' : '');
}

/* 注意：.stat-value 的数字截断只用纯 CSS 兜底（overflow-wrap: anywhere），
   不要再加 JS 动态缩字号——那套方案反复失效过多次，字体异步加载后还会复发。 */
