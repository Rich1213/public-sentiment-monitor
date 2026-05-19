const test = require('node:test');
const assert = require('node:assert/strict');

const {
  normalizeScore,
  buildPrimarySummary,
  getArticleRiskBadge,
  deriveProgressState,
  selectLatestCompletedRunsByKeyword,
  summarizeCommentInsights,
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
  assert.match(summary.narrative, /今日最需要關注的事件是超商食品出現活蟲疑慮/);
  assert.match(summary.narrative, /主要出現在 YouTube、PTT/);
  assert.match(summary.narrative, /已反映出食安與品管風險擴散/);
  assert.match(summary.narrative, /建議立即釐清是否直接涉及 7-ELEVEN/);
});

test('buildPrimarySummary prioritizes severe market food-safety crisis over weaker brand complaints', () => {
  const summary = buildPrimarySummary(
    {
      keyword: '7-ELEVEN',
      pos: 8,
      neu: 2,
      neg: 3,
      total: 13,
      scores: [4, 4, 3],
      analyses: [
        { channel: 'youtube', sentiment: '負面', score: 4, theme: '超商加盟問題', title: '2024 04 17 有500萬要加盟小7嗎' },
        { channel: 'ptt', sentiment: '負面', score: 4, theme: '發票中獎詐騙', title: 'Re: [問卦] 7-11雲端發票中獎詐騙' },
        { channel: 'dcard', sentiment: '負面', score: 3, theme: '股民不滿商品卡', title: '中鋼紀念品商品卡負評' },
      ],
      alerts: [
        { channel: 'youtube', sentiment: '負面', score: 4, theme: '超商加盟問題', title: '2024 04 17 有500萬要加盟小7嗎' },
        { channel: 'ptt', sentiment: '負面', score: 4, theme: '發票中獎詐騙', title: 'Re: [問卦] 7-11雲端發票中獎詐騙' },
      ],
    },
    {
      keyword: '超商食安',
      total: 5,
      analyses: [
        { channel: 'youtube', sentiment: '負面', score: 5, theme: '食安危機_活蟲', title: '超商鮭魚沙拉驚見活蟲蠕動' },
        { channel: 'youtube', sentiment: '負面', score: 5, theme: '超商食安蟲害', title: '義大利麵小蟲亂竄超噁' },
      ],
      alerts: [
        { channel: 'youtube', sentiment: '負面', score: 5, theme: '食安危機_活蟲', title: '超商鮭魚沙拉驚見活蟲蠕動' },
        { channel: 'youtube', sentiment: '負面', score: 5, theme: '超商食安蟲害', title: '義大利麵小蟲亂竄超噁' },
      ],
    }
  );

  assert.match(summary.narrative, /今日最需要關注的事件是超商食品出現活蟲疑慮/);
  assert.match(summary.narrative, /主要出現在 YouTube/);
  assert.match(summary.narrative, /食安與品管風險擴散/);
  assert.doesNotMatch(summary.narrative, /發票中獎詐騙/);
  assert.doesNotMatch(summary.narrative, /加盟/);
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

test('deriveProgressState ignores stale orphan active runs on restore', () => {
  const state = deriveProgressState({
    runs: [
      { keyword: '7-ELEVEN', started_at: '2026-05-18T01:00:00.000Z', ended_at: null },
      { keyword: '全家', started_at: '2026-05-18T01:05:00.000Z', ended_at: null },
    ],
    monitorKeywords: ['7-ELEVEN', '全家', '萊爾富', 'OK mart', '超商食安'],
    triggeredAtISO: null,
    nowMs: Date.parse('2026-05-18T05:00:00.000Z'),
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
  assert.match(state.statusText, /正在更新/);
});

test('selectLatestCompletedRunsByKeyword keeps only the newest completed run per keyword', () => {
  const selected = selectLatestCompletedRunsByKeyword([
    { id: 11, keyword: '7-ELEVEN', started_at: '2026-05-18T07:00:00Z', ended_at: '2026-05-18T07:05:00Z' },
    { id: 18, keyword: '7-ELEVEN', started_at: '2026-05-18T08:00:00Z', ended_at: '2026-05-18T08:05:00Z' },
    { id: 23, keyword: '7-ELEVEN', started_at: '2026-05-18T09:00:00Z', ended_at: '2026-05-18T09:05:00Z' },
    { id: 24, keyword: '全家', started_at: '2026-05-18T09:10:00Z', ended_at: '2026-05-18T09:15:00Z' },
    { id: 25, keyword: '萊爾富', started_at: '2026-05-18T09:20:00Z', ended_at: '2026-05-18T09:25:00Z' },
    { id: 99, keyword: '7-ELEVEN', started_at: '2026-05-18T09:30:00Z', ended_at: null },
  ]);

  assert.deepEqual(selected.map(r => r.id), [25, 24, 23]);
});

test('summarizeCommentInsights returns dominant sentiment, top themes, and representative comments', () => {
  const summary = summarizeCommentInsights([
    { sentiment: '負面', score: 5, theme: '食安危機', content: '真的太誇張，活蟲根本不能接受', author: 'a' },
    { sentiment: '負面', score: 4, theme: '食安危機', content: '這種品管我不敢再買', author: 'b' },
    { sentiment: '負面', score: 4, theme: '品牌信任', content: '以後看到小七會怕', author: 'c' },
    { sentiment: '中立', score: 2, theme: '新聞討論', content: '等官方說明', author: 'd' },
  ]);

  assert.equal(summary.dominantSentiment, '負面');
  assert.deepEqual(summary.topThemes, ['食安危機', '品牌信任', '新聞討論']);
  assert.equal(summary.representativeComments.length, 3);
  assert.match(summary.representativeComments[0].content, /活蟲/);
});
