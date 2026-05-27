const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('daily-report.html includes navigation to dashboard and intelligence', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'daily-report.html'), 'utf8');
  assert.match(html, /dashboard\.html/);
  assert.match(html, /intelligence\.html/);
  assert.match(html, /昨日分類日報/);
});

test('daily-report.html contains headline and section containers', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'daily-report.html'), 'utf8');
  assert.match(html, /id="daily-report-headline"/);
  assert.match(html, /id="daily-report-sections"/);
});
