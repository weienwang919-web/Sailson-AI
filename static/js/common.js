/**
 * Sailson AI 公共脚本
 * 轮询、工具函数等
 */
(function() {
  'use strict';
  window.SailsonCommon = {
    POLL_MAX: 72,
    POLL_INTERVAL: 5000,
    escapeHtml: function(str) {
      if (!str) return '';
      var div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }
  };
})();
