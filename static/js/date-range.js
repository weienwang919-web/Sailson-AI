/* 日期区间控件。原先每个看板各写一套 setXxxQuickRange / clearXxxRange，
   函数体完全相同只差元素 ID 前缀，这里收敛成一个工厂。 */

function renderDateRangeControls(prefix, quickDays) {
  const days = quickDays || [7, 30];
  const quick = days.map(d => `<button class="btn-chip" data-range="${prefix}:${d}">最近${d}天</button>`).join('');
  return `
    <div class="date-range-controls">
      <input type="date" id="${prefix}-date-from">
      <span class="date-sep">—</span>
      <input type="date" id="${prefix}-date-to">
      ${quick}
      <button class="btn-chip" data-range="${prefix}:clear">全部</button>
    </div>`;
}

function createDateRange(prefix, onChange) {
  const from = () => document.getElementById(prefix + '-date-from');
  const to = () => document.getElementById(prefix + '-date-to');

  const api = {
    setQuick(days) {
      const end = new Date();
      const start = new Date();
      start.setDate(start.getDate() - (days - 1));
      const iso = d => d.toISOString().slice(0, 10);
      if (from()) from().value = iso(start);
      if (to()) to().value = iso(end);
      onChange && onChange();
    },
    clear() {
      if (from()) from().value = '';
      if (to()) to().value = '';
      onChange && onChange();
    },
    params() {
      const p = new URLSearchParams();
      if (from() && from().value) p.set('date_from', from().value);
      if (to() && to().value) p.set('date_to', to().value);
      return p;
    },
    /* 后端会把缺省区间回填成实际使用的范围，同步回输入框让用户看到真实口径 */
    reflect(data) {
      if (!data) return;
      if (from() && !from().value && data.date_from) from().value = data.date_from;
      if (to() && !to().value && data.date_to) to().value = data.date_to;
    },
  };

  document.addEventListener('click', evt => {
    const btn = evt.target.closest(`[data-range^="${prefix}:"]`);
    if (!btn) return;
    const arg = btn.dataset.range.split(':')[1];
    if (arg === 'clear') api.clear(); else api.setQuick(Number(arg));
  });
  [from(), to()].forEach(el => el && el.addEventListener('change', () => onChange && onChange()));

  return api;
}
