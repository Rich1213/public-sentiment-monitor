window.IntelligenceSummary = {
  formatTopicCount(topic) {
    const events = Number(topic?.event_count || 0);
    const signals = Number(topic?.signal_count || 0);
    return `${events} 個事件 / ${signals} 筆訊號`;
  },

  sentimentLabel(topic) {
    const mix = JSON.parse(topic?.sentiment_mix_json || "{}");
    const neg = Number(mix["負面"] || 0);
    const pos = Number(mix["正面"] || 0);
    if (neg > pos) return "風險主導";
    if (pos > neg) return "機會主導";
    return "混合討論";
  },

  topCompetitiveThemes(matrix) {
    return Object.entries(matrix || {})
      .map(([theme, scopes]) => ({
        theme,
        total: Object.values(scopes || {}).reduce((sum, count) => sum + Number(count || 0), 0),
      }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 5);
  },
};
