const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const state = {now: 120, started: 100, interval: 2, timezone: 'UTC',
    output_dir: '/tmp/codex-speed-example', sessions: [], requests: [], errors: [],
    metric: 'request_average', window_seconds: 60};
  state.errors = ['无法读取 <会话>：文件不存在'];
  const html = fs.readFileSync(path.join(root, 'dashboard.html'), 'utf8')
    .replace('__INITIAL_STATE__', JSON.stringify(state).replace(/</g, '\\u003c'))
    .replace('__SAVED__', 'true');
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent(html);
    const details = page.locator('#session-errors');
    assert.equal(await details.isVisible(), true);
    assert.equal(await details.getAttribute('open'), null);
    assert.equal(await page.locator('#session-errors-list').isVisible(), false);
    assert.equal(await page.locator('#session-errors-summary').innerText(), '会话读取异常 · 1 条');
    await page.locator('#session-errors-summary').click();
    assert.equal(await page.locator('#session-errors-list').innerText(), state.errors[0]);
    assert.equal(await page.locator('#session-errors-list 会话').count(), 0);
    await page.evaluate(() => render());
    assert.notEqual(await details.getAttribute('open'), null);
    await page.locator('#session-errors-summary').click();
    await page.evaluate(() => render());
    assert.equal(await details.getAttribute('open'), null);
    await page.evaluate(() => { state.errors = []; render(); });
    assert.equal(await details.isVisible(), false);
    await page.evaluate(() => { window.fetch = async () => { throw new Error('test disconnect'); }; poll(); });
    assert.equal(await page.locator('#error').isVisible(), true);
    assert.match(await page.locator('#error').innerText(), /无法连接监测服务/);
    assert.deepEqual(errors, []);
    console.log('PASS: default collapse, expand/collapse persistence, escaped text, resolved errors hidden, connection alert');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
