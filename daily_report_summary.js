(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.DailyReportSummary = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  function formatMix(section) {
    return `正 ${section.pos_count} / 中 ${section.neu_count} / 負 ${section.neg_count}`;
  }

  function topThreadLabel(thread) {
    return `${thread.channel || 'unknown'}｜${thread.theme || '未分類'}｜${thread.sentiment || '未知'}`;
  }

  return {
    formatMix,
    topThreadLabel,
  };
});
