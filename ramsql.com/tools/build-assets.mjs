// Generates OG/Twitter image, icons, optimized images and manifest screenshots with Playwright.
// Usage: cd ramsql.com/tools && npm i && npx playwright install chromium && node build-assets.mjs
import { chromium } from 'playwright';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const pub = path.join(root, 'public');
const assets = path.join(root, 'assets');
const tpl = path.join(here, 'templates');
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.jpg': 'image/jpeg', '.ico': 'image/x-icon', '.json': 'application/json' };

const server = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split('?')[0]);
  let base = pub;
  if (u.startsWith('/__tpl/')) { base = tpl; u = u.slice(6); }
  else if (u.startsWith('/__assets/')) { base = assets; u = u.slice(9); }
  if (u === '/') u = '/index.html';
  const f = path.join(base, u);
  if (!f.startsWith(base) || !fs.existsSync(f)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'content-type': types[path.extname(f)] || 'application/octet-stream' });
  fs.createReadStream(f).pipe(res);
}).listen(0);
const port = server.address().port;
const url = (p) => `http://127.0.0.1:${port}${p}`;
const out = (...p) => { const f = path.join(pub, ...p); fs.mkdirSync(path.dirname(f), { recursive: true }); return f; };

const browser = await chromium.launch();

async function shot(tplName, file, { w, h, scale = 1, type = 'png', quality, transparent = false, query = '' }) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: scale });
  const page = await ctx.newPage();
  await page.goto(url(`/__tpl/${tplName}${query}`));
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
  await page.screenshot({ path: out(file), type, quality, omitBackground: transparent });
  await ctx.close();
  console.log('wrote', file, Math.round(fs.statSync(out(file)).size / 1024) + 'KB');
}

// Optimized site images (originals stay in assets/)
await shot('opt-shield.html', 'img/shield.jpg', { w: 640, h: 640, type: 'jpeg', quality: 86 });
await shot('opt-chip.html', 'img/chip.png', { w: 600, h: 300, transparent: true });

// Social card: 1200x630 JPEG. WhatsApp ignores images over ~300KB, so keep it small.
await shot('og.html', 'og-image.jpg', { w: 1200, h: 630, type: 'jpeg', quality: 82 });

// Icons
await shot('icon.html', 'icons/maskable-512.png', { w: 512, h: 512, query: '?pad=0.64' });
await shot('icon.html', 'icons/maskable-192.png', { w: 192, h: 192, query: '?pad=0.64' });
await shot('icon.html', 'icons/shortcut-96.png', { w: 96, h: 96, query: '?pad=0.9' });

// Manifest / store screenshots from the live page
async function page(name, w, h, scale, steps) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: scale, reducedMotion: 'no-preference' });
  const p = await ctx.newPage();
  await p.goto(url('/'), { waitUntil: 'networkidle' });
  await p.evaluate(() => document.fonts.ready);
  for (const [file, sel, wait] of steps) {
    if (sel) {
      await p.evaluate((s) => { const el = document.querySelector(s); window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 70); }, sel);
    } else await p.evaluate(() => scrollTo(0, 0));
    await p.waitForTimeout(wait);
    await p.screenshot({ path: out('screenshots', file), type: 'png' });
    console.log('wrote screenshots/' + file, Math.round(fs.statSync(out('screenshots', file)).size / 1024) + 'KB');
  }
  await ctx.close();
}
await page('desktop', 1280, 720, 1, [
  ['desktop-hero.png', null, 3200],
  ['desktop-benchmarks.png', '#performance', 3200],
  ['desktop-engine.png', '#license', 2400]
]);
await page('mobile', 375, 667, 2, [
  ['mobile-hero.png', null, 3200],
  ['mobile-benchmarks.png', '#chart', 3200],
  ['mobile-license.png', '#license', 2400]
]);

await browser.close();
server.close();
