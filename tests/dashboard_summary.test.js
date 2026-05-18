const test = require('node:test');
const assert = require('node:assert/strict');

const {
  normalizeScore,
  buildPrimarySummary,
  getArticleRiskBadge,
  deriveProgressState,
} = require('../dashboard_summary.js');

test('normalizeScore maps legacy and integer scales', () => {
  assert.equal(normalizeScore(0.7), 4);
  assert.equal(normalizeScore(0.85), 5);
  assert.equal(normalizeScore(3), 3);
});

test('buildPrimarySummary returns crisis-oriented 0-100 risk score and narrative', () => {
  const summary = buildPrimarySummary({
    keyword: '7-ELEVEN',
    pos: 3,
    neu: 1,
    neg: 4,
    total: 8,
    scores: [5, 4, 4, 3, 2, 1, 1, 4],
    analyses: [
      { channel: 'youtube', sentiment: '負面', score: 5, theme: '生鮮沙拉驚見活蟲' },
      { channel: 'ptt', sentiment: '負面', score: 4, theme: '義大利麵出現活蟲' },
      { channel: 'google_news', sentiment: '負面', score: 4, theme: '食安疑慮擴散' },
      { channel: 'youtube', sentiment: '正面', score: 1, theme: '促銷新品' },
    ],
    alerts: [
      { score: 5, theme: '生鮮沙拉驚見活蟲', channel: 'youtube' },
      { score: 4, theme: '義大利麵出現活蟲', channel: 'ptt' },
    ],
  });

  assert.equal(summary.label, '危機處理中');
  assert.equal(summary.colorToken, 'danger');
  assert.ok(summary.riskScore >= 75);
  assert.match(summary.narrative, /今日最需要關注的事件是生鮮沙拉驚見活蟲、義大利麵出現活蟲/);
  assert.match(summary.narrative, /主要出現在 YouTube、PTT/);
  assert.match(summary.narrative, /已反映出食安與品管風險擴散/);
  assert.match(summary.narrative, /需要立即釐清是否直接涉及 7-ELEVEN/);
});

test('buildPrimarySummary handles no-primary-data gracefully', () => {
  const summary = buildPrimarySummary(null);
  assert.equal(summary.riskScore, 0);
  assert.equal(summary.label, '尚無資料');
});

test('getArticleRiskBadge uses conservative human labels', () => {
  assert.deepEqual(getArticleRiskBadge(5), { cls: 'score-5', icon: '🔥', label: '危機' });
  assert.deepEqual(getArticleRiskBadge(4), { cls: 'score-4', icon: '🚨', label: '高度關注' });
  assert.deepEqual(getArticleRiskBadge(3), { cls: 'score-3', icon: '⚠️', label: '需要關注' });
  assert.deepEqual(getArticleRiskBadge(2), { cls: 'score-2', icon: '📋', label: '一般負評' });
  assert.deepEqual(getArticleRiskBadge(1), { cls: 'score-1', icon: '✅', label: '低度留意' });
});

test('deriveProgressState hides stale waiting state when no active railway run exists', () => {
  const state = deriveProgressState({
    runs: [],
    monitorKeywords: ['7-ELEVEN', '全家', '萊爾富', 'OK mart', '超商食安'],
    triggeredAtISO: '2026-05-18T05:00:00.000Z',
    nowMs: Date.parse('2026-05-18T05:05:00.000Z'),
  });

  assert.equal(state.visible, false);
});

test('deriveProgressState shows active railway progress when run exists', () => {
  const state = deriveProgressState({
    runs: [
      { keyword: '7-ELEVEN', started_at: '2026-05-18T05:00:00.000Z', ended_at: null },
      { keyword: '全家', started_at: '2026-05-18T04:58:00.000Z', ended_at: '2026-05-18T04:59:00.000Z', articles_found: 12 },
    ],
    monitorKeywords: ['7-ELEVEN', '全家', '萊爾富', 'OK mart', '超商食安'],
    triggeredAtISO: '2026-05-18T04:57:00.000Z',
    nowMs: Date.parse('2026-05-18T05:01:00.000Z'),
  });

  assert.equal(state.visible, true);
  assert.equal(state.brandLabel, '7-ELEVEN');
  assert.match(state.statusText, /正在採集/);
});
