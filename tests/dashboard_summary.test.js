const test = require('node:test');
const assert = require('node:assert/strict');

const {
  normalizeScore,
  buildPrimarySummary,
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
  assert.match(summary.narrative, /今日負評主要來自 YouTube、PTT、Google News/);
  assert.match(summary.narrative, /消費者主要反饋為生鮮沙拉驚見活蟲、義大利麵出現活蟲/);
  assert.match(summary.narrative, /需要立即釐清是否直接涉及 7-ELEVEN/);
});

test('buildPrimarySummary handles no-primary-data gracefully', () => {
  const summary = buildPrimarySummary(null);
  assert.equal(summary.riskScore, 0);
  assert.equal(summary.label, '尚無資料');
});
