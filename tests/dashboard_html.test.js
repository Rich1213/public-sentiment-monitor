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
