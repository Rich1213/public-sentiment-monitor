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

  function getArticleRiskBadge(score) {
    const normalized = Math.round(score) || 1;
    const scoreMap = {
      5: { cls: 'score-5', icon: '🔥', label: '危機' },
      4: { cls: 'score-4', icon: '🚨', label: '高度關注' },
      3: { cls: 'score-3', icon: '⚠️', label: '需要關注' },
      2: { cls: 'score-2', icon: '📋', label: '一般負評' },
      1: { cls: 'score-1', icon: '✅', label: '低度留意' },
    };
    return scoreMap[normalized] || scoreMap[3];
  }

  function clamp(num, min, max) {
    return Math.max(min, Math.min(max, num));
  }

  function uniq(arr) {
    return [...new Set(arr.filter(Boolean))];
  }

  function countBy(items, keyFn) {
    const map = new Map();
    items.forEach(item => {
      const key = keyFn(item);
      if (!key) return;
      map.set(key, (map.get(key) || 0) + 1);
    });
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }

  function pickPrimaryChannels(alerts, negativeAnalyses) {
    const source = alerts.length > 0 ? alerts : negativeAnalyses;
    return countBy(source, a => CHANNEL_LABELS[a.channel] || a.channel)
      .slice(0, 2)
      .map(([label]) => label);
  }

  function pickTopThemes(alerts, negativeAnalyses) {
    const alertThemes = alerts
      .filter(a => normalizeScore(a.score) >= 4)
      .map(a => a.theme)
      .filter(Boolean);
    if (alertThemes.length > 0) {
      return uniq(alertThemes).slice(0, 2);
    }
    return uniq(
      countBy(negativeAnalyses, a => a.theme)
        .slice(0, 2)
        .map(([theme]) => theme)
    );
  }

  function buildNarrative(primary, negativeAnalyses, topThemes, riskLabel, primaryChannels) {
    if (!primary || !primary.total) {
      return '今日尚無足夠資料，請先執行監控或等待更多主品牌輿情進入。';
    }

    if (negativeAnalyses.length === 0) {
      return `今日主品牌聲量以正面或中立訊號為主，尚未觀察到明確危機擴散，建議持續追蹤主要渠道變化。`;
    }

    const channelText = primaryChannels.join('、');
    const themeText = topThemes.length > 0 ? topThemes.slice(0, 2).join('、') : '食安與品管疑慮';
    const riskText = riskLabel.colorToken === 'danger' || riskLabel.colorToken === 'high'
      ? '已反映出食安與品管風險擴散'
      : '已反映出負面討論持續累積';

    return `今日最需要關注的事件是${themeText}，主要出現在 ${channelText}，${riskText}，需要立即釐清是否直接涉及 ${primary.keyword} 門市、商品或供應鏈。`;
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

    const topThemes = pickTopThemes(alerts, negativeAnalyses);
    const primaryChannels = pickPrimaryChannels(alerts, negativeAnalyses);

    return {
      riskScore,
      ...riskLabel,
      narrative: buildNarrative(primary, negativeAnalyses, topThemes, riskLabel, primaryChannels),
    };
  }

  function inferBatchStart(runs, monitorKeywords) {
    const monitorRuns = runs
      .filter(r => monitorKeywords.includes(r.keyword))
      .sort((a, b) => new Date(a.started_at) - new Date(b.started_at));

    if (monitorRuns.length === 0) return null;

    const batch = [monitorRuns[monitorRuns.length - 1]];
    for (let i = monitorRuns.length - 2; i >= 0; i--) {
      const curr = new Date(batch[0].started_at);
      const prev = new Date(monitorRuns[i].started_at);
      const gapMinutes = (curr - prev) / 60000;
      if (gapMinutes > 15) break;
      batch.unshift(monitorRuns[i]);
    }
    return batch[0].started_at;
  }

  function deriveProgressState({ runs, monitorKeywords, triggeredAtISO, nowMs = Date.now() }) {
    const inferredStart = triggeredAtISO || inferBatchStart(runs || [], monitorKeywords || []);
    const triggeredMs = inferredStart ? new Date(inferredStart).getTime() : 0;
    const freshRuns = (runs || []).filter(r =>
      monitorKeywords.includes(r.keyword) &&
      (!triggeredMs || new Date(r.started_at).getTime() >= triggeredMs)
    );
    const activeRun = freshRuns.find(r => !r.ended_at);
    const doneRuns = freshRuns.filter(r => r.ended_at);
    const totalExpected = (monitorKeywords || []).length || 1;

    if (activeRun) {
      const doneCount = doneRuns.length;
      const pct = Math.round((doneCount / totalExpected) * 90) + 5;
      const articles = doneRuns.reduce((sum, r) => sum + (r.articles_found || 0), 0);
      return {
        visible: true,
        pct,
        brandLabel: activeRun.keyword,
        countText: `品牌 ${doneCount + 1} / ${totalExpected}`,
        statusText: `正在採集「${activeRun.keyword}」· 已完成 ${doneCount} / ${totalExpected} 個品牌 · 累計 ${articles} 篇`,
      };
    }

    if (doneRuns.length >= totalExpected && doneRuns.length > 0) {
      const totalArticles = doneRuns.reduce((sum, r) => sum + (r.articles_found || 0), 0);
      return {
        visible: true,
        pct: 100,
        brandLabel: '完成 ✓',
        countText: `品牌 ${doneRuns.length} / ${totalExpected}`,
        statusText: `採集完成！共 ${totalArticles} 篇文章，重新整理資料中...`,
        completed: true,
      };
    }

    if (triggeredMs && (nowMs - triggeredMs) <= 90000) {
      return {
        visible: true,
        pct: 5,
        brandLabel: '啟動中',
        countText: `品牌 0 / ${totalExpected}`,
        statusText: '等待 Railway 背景任務啟動...',
      };
    }

    return { visible: false };
  }

  return {
    normalizeScore,
    buildPrimarySummary,
    getRiskLabel,
    getArticleRiskBadge,
    deriveProgressState,
    inferBatchStart,
  };
});
