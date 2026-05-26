const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'intelligence.html'), 'utf8');

test('intelligence page renders dedicated sections and intelligence endpoints', () => {
  assert.match(html, /品牌決策/);
  assert.match(html, /\/intelligence\/topics/);
  assert.match(html, /\/intelligence\/snapshots\/monthly/);
});

test('intelligence page includes separate navigation from daily dashboard', () => {
  assert.match(html, /\.\/dashboard\.html/);
  assert.match(html, /\.\/intelligence\.html/);
});
