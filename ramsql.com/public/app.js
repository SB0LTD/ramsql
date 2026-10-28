(function () {
  'use strict';
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* nav */
  var nav = document.getElementById('nav');
  addEventListener('scroll', function () { nav.classList.toggle('solid', scrollY > 30); }, { passive: true });

  /* reveal */
  var io = new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
  }, { threshold: 0.12 });
  document.querySelectorAll('.reveal').forEach(function (n) { io.observe(n); });

  /* counters */
  function count(el) {
    var to = parseFloat(el.dataset.count), dec = +(el.dataset.dec || 0), suf = el.dataset.suffix || '';
    var t0 = performance.now(), dur = reduce ? 1 : 1800;
    (function tick(t) {
      var p = Math.min(1, (t - t0) / dur), v = to * (1 - Math.pow(1 - p, 4));
      el.innerHTML = v.toFixed(dec) + suf;
      if (p < 1) requestAnimationFrame(tick);
    })(t0);
  }
  var cio = new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { count(e.target); cio.unobserve(e.target); } });
  });
  document.querySelectorAll('[data-count]').forEach(function (n) { cio.observe(n); });

  /* benchmark data: all "measured" values come from the retained reports in /benchmarks */
  var fmt = function (n) { return n >= 100 ? Math.round(n).toLocaleString('en-US') : n.toFixed(n < 10 ? 1 : 0); };
  var DATA = {
    irdb: {
      scale: 'log',
      note: 'Source: production-20261026 report. Native single-worker SQL API calls, shuffled 1,000,000-row fixture, median of 10 trials per engine, IRDB 1.10.821. Bars use a logarithmic scale. Sort and grouped workloads show the narrowest leads.',
      rows: [
        ['Uniform key lookup', [['RamSQL', 1425601.8, 'us'], ['IRDB', 2920.3, 'them']], '488\u00d7'],
        ['Range page', [['RamSQL', 166535.4, 'us'], ['IRDB', 93.2, 'them']], '1,786\u00d7'],
        ['Mixed 50/50 read-write', [['RamSQL', 1412210.8, 'us'], ['IRDB', 148.5, 'them']], '9,509\u00d7'],
        ['Filtered SUM', [['RamSQL', 5830.4, 'us'], ['IRDB', 1167.4, 'them']], '4.99\u00d7'],
        ['Grouped aggregate', [['RamSQL', 1251.4, 'us'], ['IRDB', 628.0, 'them']], '1.99\u00d7'],
        ['Top-k sort', [['RamSQL', 50.7, 'us'], ['IRDB', 18.4, 'them']], '2.76\u00d7']
      ]
    },
    pg: {
      scale: 'lin',
      note: 'Source: dbMeter reviewer suite, repeated native trials. 100,000 rows, 8 clients, stock PostgreSQL JDBC 42.7.4, PostgreSQL 18.3, IRDB 1.10.821, medians of three trials, zero errors. Primary-key lookups are close to PostgreSQL; the lead is modest and varies between trials.',
      rows: [
        ['Filtered scan', [['RamSQL', 37611, 'us'], ['PostgreSQL 18.3', 2313, 'them'], ['IRDB', 6729, 'them']], '16\u00d7 PG'],
        ['Top-N sort', [['RamSQL', 2931, 'us'], ['PostgreSQL 18.3', 1090, 'them'], ['IRDB', 998, 'them']], '2.7\u00d7 PG'],
        ['Primary-key lookup', [['RamSQL', 162494, 'us'], ['PostgreSQL 18.3', 144244, 'them'], ['IRDB', 6887, 'them']], '1.13\u00d7 PG']
      ]
    },
    target: {
      scale: 'log',
      note: 'Hatched bars are roadmap TARGETS, not results. Solid bars are measured. Source: dbMeter million-row sales subset, 8 clients, single trial. We publish this gap because it is the next thing we are closing.',
      rows: [
        ['Million-row analytic mix, 8 clients (queries/s)', [['PostgreSQL 18.3', 27.1, 'them'], ['RamSQL today', 43.6, 'us'], ['IRDB', 1053.6, 'them'], ['RamSQL target', 1053.6, 'tgt']], 'target: match or beat IRDB']
      ]
    }
  };

  var chart = document.getElementById('chart'), noteEl = document.getElementById('chart-note');
  function render(key) {
    var d = DATA[key], html = '';
    d.rows.forEach(function (r) {
      var vals = r[1].map(function (b) { return b[1]; }), max = Math.max.apply(null, vals);
      html += '<div class="row"><h4>' + r[0] + '<em>' + r[2] + '</em></h4>';
      r[1].forEach(function (b) {
        var w = d.scale === 'log' ? Math.max(2, (Math.log10(b[1] * 10) / Math.log10(max * 10)) * 100) : (b[1] / max) * 100;
        html += '<div class="bar"><span>' + b[0] + '</span><div class="track"><div class="fill ' + b[2] + '" data-w="' + w.toFixed(2) + '"></div></div><b>' + (b[2] === 'tgt' ? '\u2265 ' : '') + fmt(b[1]) + '</b></div>';
      });
      html += '</div>';
    });
    chart.innerHTML = html;
    noteEl.textContent = d.note;
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        chart.querySelectorAll('.fill').forEach(function (f, i) {
          f.style.transitionDelay = (i * 60) + 'ms';
          f.style.width = f.dataset.w + '%';
        });
      });
    });
  }
  var started = false;
  new IntersectionObserver(function (es, o) {
    if (es[0].isIntersecting && !started) { started = true; render('irdb'); o.disconnect(); }
  }, { threshold: 0.25 }).observe(chart);
  document.querySelectorAll('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('.tab').forEach(function (x) { x.classList.remove('active'); });
      t.classList.add('active');
      started = true;
      render(t.dataset.tab);
    });
  });

  /* terminal typing */
  var out = document.getElementById('term-out');
  var script = [
    ['<span class="m">$</span> psql -h 127.0.0.1 -p 5432 ramsql\n', 0],
    ['<span class="t">ramsql=&gt;</span> <span class="k">SELECT</span> region, <span class="k">SUM</span>(amount)\n        <span class="k">FROM</span> sales <span class="k">WHERE</span> year = <span class="s">2026</span>\n        <span class="k">GROUP BY</span> region;\n', 1],
    ['<span class="m"> region | sum\n--------+-----------\n EMEA   | 48210930\n APAC   | 51877204\n AMER   | 63390118\n(3 rows)</span>\n\n', 0],
    ['<span class="t">ramsql=&gt;</span> <span class="k">SELECT</span> * <span class="k">FROM</span> accounts <span class="k">WHERE</span> id = <span class="s">731042</span>;\n', 1],
    ['<span class="m"> id     | owner  | balance\n--------+--------+---------\n 731042 | acme   | 1204.50\n(1 row)</span>\n\n', 0],
    ['<span class="t">ramsql=&gt;</span> <span class="caret"></span>', 0]
  ];
  function type() {
    var i = 0, html = '';
    out.innerHTML = '<span class="caret"></span>';
    (function step() {
      if (i >= script.length) return;
      var s = script[i++];
      if (!s[1] || reduce) { html += s[0]; out.innerHTML = html; return setTimeout(step, reduce ? 0 : 450); }
      var toks = s[0].match(/<[^>]+>|&[a-z]+;|[\s\S]/g), n = 0;
      (function ch() {
        n++;
        while (n < toks.length && /^</.test(toks[n - 1])) n++;
        out.innerHTML = html + toks.slice(0, n).join('') + '<span class="caret"></span>';
        if (n < toks.length) setTimeout(ch, 16); else { html += s[0]; setTimeout(step, 350); }
      })();
    })();
  }
  var ran = false;
  new IntersectionObserver(function (es, o) {
    if (es[0].isIntersecting && !ran) { ran = true; type(); o.disconnect(); }
  }, { threshold: 0.4 }).observe(document.getElementById('term'));

  /* background: memory-cell lattice with data pulses */
  var cv = document.getElementById('bg'), cx = cv.getContext('2d'), W, H, cells = [], pulses = [], mx = -999, my = -999, G = 56;
  function size() {
    var dpr = Math.min(2, devicePixelRatio || 1);
    W = cv.width = innerWidth * dpr; H = cv.height = innerHeight * dpr;
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    W = innerWidth; H = innerHeight;
    cells = [];
    for (var y = G / 2; y < H; y += G) for (var x = G / 2; x < W; x += G) cells.push({ x: x, y: y, a: Math.random() * 6.28, s: .4 + Math.random() * .9 });
  }
  addEventListener('resize', size);
  addEventListener('mousemove', function (e) { mx = e.clientX; my = e.clientY; }, { passive: true });
  size();
  function frame(t) {
    cx.clearRect(0, 0, W, H);
    var sy = scrollY;
    for (var i = 0; i < cells.length; i++) {
      var c = cells[i], d = Math.hypot(c.x - mx, c.y - my), near = Math.max(0, 1 - d / 220);
      var al = .05 + .05 * Math.sin(t / 1200 * c.s + c.a) + near * .55;
      cx.fillStyle = near > .05 ? 'rgba(56,232,255,' + al + ')' : 'rgba(150,160,200,' + al + ')';
      var r = 1.1 + near * 2.2;
      cx.fillRect(c.x - r, c.y - r - (sy * .05) % G, r * 2, r * 2);
    }
    if (Math.random() < .08 && pulses.length < 14) {
      var horiz = Math.random() < .5;
      pulses.push({ h: horiz, p: Math.floor(Math.random() * (horiz ? H : W) / G) * G + G / 2, v: 0, sp: 4 + Math.random() * 6, len: 90 + Math.random() * 120 });
    }
    pulses = pulses.filter(function (p) {
      p.v += p.sp;
      var max = p.h ? W : H;
      if (p.v - p.len > max) return false;
      var g = p.h ? cx.createLinearGradient(p.v - p.len, 0, p.v, 0) : cx.createLinearGradient(0, p.v - p.len, 0, p.v);
      g.addColorStop(0, 'rgba(123,92,255,0)'); g.addColorStop(1, 'rgba(56,232,255,.65)');
      cx.strokeStyle = g; cx.lineWidth = 1.5; cx.beginPath();
      if (p.h) { cx.moveTo(p.v - p.len, p.p); cx.lineTo(p.v, p.p); } else { cx.moveTo(p.p, p.v - p.len); cx.lineTo(p.p, p.v); }
      cx.stroke();
      return true;
    });
    if (!reduce) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
