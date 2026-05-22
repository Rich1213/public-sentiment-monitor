const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');

test('channel coverage labels include monochrome inline SVG icons', () => {
  assert.match(html, /function getChannelIconSvg\(channel\)/);
  assert.match(html, /class="ch-label-text"/);
  assert.match(html, /class="ch-label-icon"/);
  assert.match(html, /viewBox="0 0 16 16"/);
});

test('brand matrix includes an aggregated 其他 slot for non-brand keywords', () => {
  assert.match(html, /const BRAND_MATRIX_LABELS = \['7-ELEVEN', '全家', '萊爾富', 'OK mart', '其他'\];/);
  assert.match(html, /function buildBrandMatrixMap\(brandMap\)/);
  assert.match(html, /matrixMap\['其他'\]/);
});

test('dashboard typography and hero styling use restrained display numerals', () => {
  assert.match(html, /fonts\.googleapis\.com\/css2\?family=Bebas\+Neue/);
  assert.match(html, /\.kpi-hero\s*\{/);
  assert.match(html, /\.kpi-number\s*\{/);
  assert.match(html, /\.kpi-brand-name\s*\{/);
  assert.match(html, /<span class="kpi-number" id="kpi-alerts-main">/);
  assert.match(html, /<span class="kpi-number" id="kpi-articles">/);
});

test('dashboard readability improvements enlarge table text and use thicker gradient bars', () => {
  assert.match(html, /\.alert-table\s*\{[^}]*font-size:\s*14px;/s);
  assert.match(html, /\.alert-table td\s*\{[^}]*padding:\s*14px 12px;/s);
  assert.match(html, /\.sentiment-bar\s*\{[^}]*height:\s*16px;/s);
  assert.match(html, /\.ch-bar-wrap\s*\{[^}]*height:\s*16px;/s);
  assert.match(html, /linear-gradient\(90deg,\s*#34d399,\s*#10b981\)/);
  assert.match(html, /linear-gradient\(90deg,\s*#fb7185,\s*#ef4444\)/);
});

test('main target brand KPI remains static and is not reassigned from runtime brand data', () => {
  assert.match(html, /id="kpi-brand">7-ELEVEN<\/div>/);
  assert.doesNotMatch(html, /document\.getElementById\('kpi-brand'\)\.textContent\s*=/);
});
