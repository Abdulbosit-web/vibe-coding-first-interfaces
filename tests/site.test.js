// Behaviour tests for every site page, run with: node tests/site.test.js
// They open the page the way a visitor does (phone and desktop) and check
// what that visitor sees and can click. External hosts (fonts, map) are
// blocked so the result does not depend on the network.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const file = rel => 'file://' + path.resolve(__dirname, '..', rel);

// Each file in tests/sites/ describes one site page: the facts confirmed
// from its listing (2GIS, YCLIENTS, DIKIDI). Every test runs on every site.
const SITES_DIR = path.join(__dirname, 'sites');
const SITES = fs.readdirSync(SITES_DIR).filter(f => f.endsWith('.js')).sort()
  .map(f => require(path.join(SITES_DIR, f)))
  .map(s => ({ ...s, page: file(s.page) }));
if (!SITES.length) throw new Error('no sites in tests/sites');

const tests = [];
let FACTS, PAGE; // set per site by the runner below
const contexts = []; // closed after every test so pages do not pile up
const test = (name, fn) => tests.push({ name, fn });

async function open(browser, viewport) {
  const ctx = await browser.newContext({ viewport, locale: 'ru-RU' });
  contexts.push(ctx);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route(/^https?:/, r => r.abort());
  await page.goto(PAGE);
  return { page, ctx, errors };
}

const PHONE = { width: 375, height: 740 };
const DESKTOP = { width: 1440, height: 900 };

test('SEO: title, description, Open Graph, one h1', async b => {
  const { page } = await open(b, DESKTOP);
  assert.match(await page.title(), FACTS.name);
  const meta = n => page.getAttribute(`meta[${n}]`, 'content');
  assert.ok((await meta('name="description"')).length > 50);
  assert.match(await meta('property="og:title"'), FACTS.name);
  assert.ok(await meta('property="og:description"'));
  assert.equal(await page.locator('h1').count(), 1);
  assert.ok(await page.locator('h2').count() >= 5);
});

test('every phone link calls the confirmed number', async b => {
  const { page } = await open(b, PHONE);
  const hrefs = await page.$$eval('a[href^="tel:"]', a => a.map(x => x.getAttribute('href')));
  if (!FACTS.phoneHref) return assert.deepEqual(hrefs, [], 'number not confirmed: no call links');
  assert.ok(hrefs.length >= 3, 'expected phone links in hero, contacts and sticky bar');
  for (const h of hrefs) assert.equal(h, FACTS.phoneHref);
});

test('every booking button opens the online booking page in a new tab', async b => {
  const { page } = await open(b, DESKTOP);
  const links = await page.$$eval('a[data-book]', a => a.map(x => [x.href, x.target, x.rel]));
  assert.ok(links.length >= 6, 'hero, every service card and contacts need a booking button');
  for (const [href, target, rel] of links) {
    assert.equal(href, FACTS.bookingUrl);
    assert.equal(target, '_blank');
    assert.match(rel, /noopener/);
  }
});

test('address and hours are shown', async b => {
  const { page } = await open(b, DESKTOP);
  const text = await page.innerText('body');
  assert.ok(text.includes(FACTS.address));
  if (FACTS.hours) assert.ok(text.includes(FACTS.hours));
});

test('price list shows exactly the confirmed prices', async b => {
  const { page } = await open(b, DESKTOP);
  const rows = await page.$$eval('[data-price-row]', rs => rs.map(r => [
    r.querySelector('[data-name]').textContent.trim(),
    r.querySelector('[data-cost]').textContent.trim(),
  ]));
  assert.deepEqual(Object.fromEntries(rows), FACTS.prices);
});

test('no filler text or placeholders', async b => {
  const { page } = await open(b, DESKTOP);
  // Inlined images are base64 noise: drop them before looking for filler words.
  const html = (await page.content()).replace(/data:image\/[a-z]+;base64,[A-Za-z0-9+\/=]+/g, '').toLowerCase();
  for (const bad of ['lorem', 'ipsum', 'todo', 'example.com', 'xxx-xx']) assert.ok(!html.includes(bad), bad);
});

test('map and 2GIS route are present', async b => {
  const { page } = await open(b, DESKTOP);
  const src = await page.getAttribute('iframe[data-map]', 'src');
  assert.match(decodeURIComponent(src), FACTS.mapText);
  assert.match(await page.getAttribute('a[data-route]', 'href'), /^https:\/\/2gis\.ru\//);
});

test('phone: no horizontal scroll', async b => {
  const { page } = await open(b, PHONE);
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  assert.ok(sw <= cw, `scrollWidth ${sw} > ${cw}`);
});

test('phone: menu opens, and a menu link closes it and scrolls', async b => {
  const { page } = await open(b, PHONE);
  const burger = page.locator('[data-burger]');
  const nav = page.locator('#site-nav');
  assert.equal(await nav.isVisible(), false);
  await burger.click();
  assert.equal(await burger.getAttribute('aria-expanded'), 'true');
  assert.equal(await nav.isVisible(), true);
  await nav.locator('a[href="#prices"]').click();
  assert.equal(await burger.getAttribute('aria-expanded'), 'false');
  // Smooth scrolling ends with the section just under the sticky header.
  // Wait until the section is near the top AND has stopped moving.
  await page.waitForFunction(() => {
    const top = Math.round(document.querySelector('#prices').getBoundingClientRect().top);
    const still = top === window.__lastTop; window.__lastTop = top;
    return still && Math.abs(top) < 120;
  }, null, { timeout: 6000, polling: 100 }).catch(() => {});
  const top = await page.$eval('#prices', el => Math.round(el.getBoundingClientRect().top));
  assert.ok(Math.abs(top) < 120, `prices section top at ${top}`);
});

test('phone: sticky call/booking bar is visible; desktop: hidden', async b => {
  const p = await open(b, PHONE);
  assert.equal(await p.page.locator('[data-sticky-bar]').isVisible(), true);
  const d = await open(b, DESKTOP);
  assert.equal(await d.page.locator('[data-sticky-bar]').isVisible(), false);
});

test('local images load', async b => {
  const { page } = await open(b, DESKTOP);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(500);
  const broken = await page.$$eval('img', imgs => imgs.filter(i => !i.src.startsWith('http') || i.src.startsWith('file:')).filter(i => !(i.complete && i.naturalWidth > 0)).map(i => i.src));
  assert.deepEqual(broken, []);
});

test('reveal-on-scroll elements end up visible', async b => {
  const { page } = await open(b, DESKTOP);
  for (let y = 0; y < 12000; y += 500) { await page.evaluate(v => window.scrollTo(0, v), y); await page.waitForTimeout(40); }
  await page.waitForTimeout(800);
  const hidden = await page.$$eval('[data-reveal]', els => els.filter(e => getComputedStyle(e).opacity !== '1').length);
  assert.equal(hidden, 0);
});

test('no script errors', async b => {
  const { page, errors } = await open(b, PHONE);
  await page.locator('[data-burger]').click();
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(300);
  assert.deepEqual(errors, []);
});

(async () => {
  const browser = await chromium.launch();
  let failed = 0;
  for (const site of SITES) {
    FACTS = site; PAGE = site.page;
    console.log('\n' + path.relative(path.resolve(__dirname, '..'), new URL(PAGE).pathname));
    for (const t of tests) {
      try { await t.fn(browser); console.log('ok   ', t.name); }
      catch (e) { failed++; console.log('FAIL ', t.name, '\n      ', e.message.split('\n')[0]); }
      finally { await Promise.all(contexts.splice(0).map(c => c.close())); }
    }
  }
  await browser.close();
  const total = tests.length * SITES.length;
  console.log(`\n${total - failed}/${total} passed`);
  process.exit(failed ? 1 : 0);
})();
