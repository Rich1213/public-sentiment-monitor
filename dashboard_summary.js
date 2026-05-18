(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.DashboardSummary = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  const CHANNEL_LABELS = {
    google_news: 'Google News',
    ptt: 'PTT',
    dcard: 'Dcard',
    youtube: 'YouTube',
  };

  function normalizeScore(rawScore) {
    if (rawScore === null || rawScore === undefined) return 0;
    if (rawScore >= 1) return Math.round(rawScore);
    if (rawScore >= 0.85) return 5;
    if (rawScore >= 0.70) return 4;
    if (rawScore >= 0.50) return 3;
    if (rawScore >= 0.30) return 2;
    return 1;
  }

  function getRiskLabel(score) {
    if (score >= 75) return { label: '危機處理中', colorToken: 'danger', note: '需要立即處理' };
    if (score >= 50) return { label: '需要立即關注', colorToken: 'high', note: '需要立即關注' };
    if (score >= 25) return { label: '需要關注', colorToken: 'medium', note: '需要關注' };
    if (score > 0) return { label: '低度留意', colorToken: 'low', note: '持續觀察即可' };
    return { label: '尚無資料', colorToken: 'muted', note: '等待更多資料' };
  }

  function clamp(num, min, max) {
    return Math.max(min, Math.min(max, num));
  }

  function uniq(arr) {
    return [...new Set(arr.filter(Boolean))];
  }

  function buildNarrative(primary, negativeAnalyses, topThemes, riskLabel) {
    if (!primary || !primary.total) {
      return '今日尚無足夠資料，請先執行監控或等待更多主品牌輿情進入。';
    }

    if (negativeAnalyses.length === 0) {
      return `今日主品牌聲量以正面或中立訊號為主，尚未觀察到明確危機擴散，建議持續追蹤主要渠道變化。`;
    }

    const channels = uniq(negativeAnalyses.map(a => CHANNEL_LABELS[a.channel] || a.channel)).slice(0, 3);
    const channelText = channels.join('、');
    const themeText = topThemes.length > 0 ? topThemes.slice(0, 2).join('、') : '食安與品管疑慮';
    const leading = riskLabel.colorToken === 'danger' || riskLabel.colorToken === 'high'
      ? '目前負面討論已具擴散性'
      : '目前負面討論已開始累積';

    return `今日負評主要來自 ${channelText}，消費者主要反饋為${themeText}，${leading}，需要立即釐清是否直接涉及 ${primary.keyword} 門市、商品或供應鏈。`;
  }

  function buildPrimarySummary(primary) {
    if (!primary || !primary.total) {
      return {
        riskScore: 0,
        label: '尚無資料',
        colorToken: 'muted',
        note: '等待更多資料',
        narrative: '今日尚無足夠資料，請先執行監控或等待更多主品牌輿情進入。',
      };
    }

    const analyses = primary.analyses || [];
    const negativeAnalyses = analyses.filter(a => a.sentiment === '負面');
    const total = Math.max(primary.total || analyses.length, 1);
    const negativeRatio = negativeAnalyses.length / total;
    const alerts = primary.alerts || [];
    const severeAlerts = alerts.filter(a => normalizeScore(a.score) >= 4);
    const negativeChannels = uniq(negativeAnalyses.map(a => a.channel));

    const volumeScore = clamp(negativeRatio * 35 + Math.min(negativeAnalyses.length, 8) * 4, 0, 35);
    const severityScore = clamp(
      severeAlerts.length * 12 +
      alerts.filter(a => normalizeScore(a.score) === 3).length * 6 +
      (negativeAnalyses.length ? (negativeAnalyses.reduce((sum, a) => sum + normalizeScore(a.score), 0) / negativeAnalyses.length) * 5 : 0),
      0,
      45
    );
    const spreadScore = clamp(negativeChannels.length * 7 + (negativeChannels.includes('youtube') && negativeChannels.includes('ptt') ? 8 : 0), 0, 20);
    const riskScore = Math.round(clamp(volumeScore + severityScore + spreadScore, 0, 100));
    const riskLabel = getRiskLabel(riskScore);

    const topThemes = uniq(
      negativeAnalyses
        .map(a => a.theme)
        .concat(alerts.map(a => a.theme))
    );

    return {
      riskScore,
      ...riskLabel,
      narrative: buildNarrative(primary, negativeAnalyses, topThemes, riskLabel),
    };
  }

  return {
    normalizeScore,
    buildPrimarySummary,
    getRiskLabel,
  };
});
