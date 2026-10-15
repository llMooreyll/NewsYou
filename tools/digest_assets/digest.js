/* digest.js — inlined into site/digest.html by tools/render_digest.py.
 *
 * No dependencies. Everything degrades: without JS every panel is still
 * reachable via its hash, Telegram falls back to CSS columns and the rails are
 * ordinary lists.
 */
(function () {
  'use strict';
  var doc = document, root = doc.documentElement, win = window;
  var $ = function (s, c) { return (c || doc).querySelector(s); };
  var $$ = function (s, c) { return [].slice.call((c || doc).querySelectorAll(s)); };
  var reduce = win.matchMedia && win.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var header = $('.hd');

  function headH() {
    var h = header ? header.getBoundingClientRect().height : 0;
    root.style.setProperty('--head-h', h + 'px');
    return h;
  }

  /* ------------------------------------------------------------------
   * horizontal scrollers: edge fades + arrow buttons only where hidden
   * content exists in that direction
   * ------------------------------------------------------------------ */
  function syncScroller(sc) {
    var t = sc.querySelector('.scroller-track');
    if (!t) return;
    var max = t.scrollWidth - t.clientWidth;
    sc.classList.toggle('more-l', t.scrollLeft > 2);
    sc.classList.toggle('more-r', t.scrollLeft < max - 2);
  }
  function initScroller(sc) {
    var t = sc.querySelector('.scroller-track');
    if (!t) return;
    t.addEventListener('scroll', function () { syncScroller(sc); }, { passive: true });
    $$('.sc-btn', sc).forEach(function (b) {
      b.addEventListener('click', function () {
        var dir = b.classList.contains('l') ? -1 : 1;
        t.scrollBy({ left: dir * t.clientWidth * 0.7, behavior: reduce ? 'auto' : 'smooth' });
      });
    });
    /* vertical wheel scrolls the strip sideways on desktop */
    t.addEventListener('wheel', function (e) {
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      if (t.scrollWidth <= t.clientWidth) return;
      t.scrollLeft += e.deltaY;
      e.preventDefault();
    }, { passive: false });
    syncScroller(sc);
  }
  $$('.scroller').forEach(initScroller);
  function syncAllScrollers() { $$('.scroller').forEach(syncScroller); }

  function revealIn(track, el) {
    if (!track || !el) return;
    var tr = track.getBoundingClientRect(), r = el.getBoundingClientRect();
    if (r.left < tr.left + 40 || r.right > tr.right - 40) {
      var target = track.scrollLeft + (r.left - tr.left) - (tr.width - r.width) / 2;
      track.scrollTo({ left: target, behavior: reduce ? 'auto' : 'smooth' });
    }
  }

  /* ------------------------------------------------------------------
   * tabs
   * ------------------------------------------------------------------ */
  var tabs = $$('.tab');
  var tabTrack = $('.tabs .scroller-track');
  var ink = $('.tab-ink');
  var current = null;
  var scrollMemo = {};

  function moveInk(tab, instant) {
    if (!ink || !tab) return;
    if (instant) ink.style.transition = 'none';
    ink.style.width = tab.offsetWidth - 26 + 'px';
    ink.style.transform = 'translateX(' + (tab.offsetLeft + 13) + 'px)';
    if (instant) { void ink.offsetWidth; ink.style.transition = ''; }
  }

  function panelOf(id) { return doc.getElementById('p-' + id); }

  function show(id, opts) {
    opts = opts || {};
    var panel = panelOf(id);
    if (!panel || id === current) return;
    var oldIdx = tabs.findIndex(function (t) { return t.dataset.tab === current; });
    var newIdx = tabs.findIndex(function (t) { return t.dataset.tab === id; });
    if (current) scrollMemo[current] = win.scrollY;

    $$('.panel.on').forEach(function (p) { p.classList.remove('on', 'in-l', 'in-r', 'in-n'); p.hidden = true; });
    tabs.forEach(function (t) {
      var on = t.dataset.tab === id;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
    });
    panel.hidden = false;
    /* Rows rise only the first time a panel is shown; coming back to one is a
       return to something already read, so it simply appears. */
    if (panel.dataset.visited) panel.classList.add('seen');
    panel.dataset.visited = '1';
    panel.classList.add('on', oldIdx < 0 ? 'in-n' : (newIdx > oldIdx ? 'in-r' : 'in-l'));
    current = id;

    var tab = tabs[newIdx];
    moveInk(tab, opts.instant);
    revealIn(tabTrack, tab);

    if (!opts.keepScroll) win.scrollTo(0, scrollMemo[id] || 0);
    if (history.replaceState && !opts.noHash) history.replaceState(null, '', '#' + id);

    layoutMasonry(panel);
    $$('.scroller', panel).forEach(syncScroller);
    spy();
  }

  tabs.forEach(function (t) {
    t.addEventListener('click', function () { show(t.dataset.tab); });
  });
  /* roving focus inside the tablist */
  if (tabTrack) tabTrack.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    var i = tabs.indexOf(doc.activeElement);
    if (i < 0) return;
    var n = tabs[(i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
    n.focus(); show(n.dataset.tab);
    e.preventDefault(); e.stopPropagation();
  });

  /* ------------------------------------------------------------------
   * side rails: scroll-spy (sections) or filter (telegram channels)
   *
   * Each entry carries its own marker (CSS), so there is no shared ink to
   * measure and move. In a contents rail the group being read is unfolded and
   * every other group folds, which keeps a page of seventeen categories down
   * to the handful of lines that matter where you are.
   * ------------------------------------------------------------------ */
  function setRail(rail, link) {
    $$('.rail-a', rail).forEach(function (a) { a.classList.toggle('on', a === link); a.classList.remove('in'); });
    var group = link.closest('.rail-g');
    var parent = group && group.querySelector(':scope > .rail-a');
    /* a sub-item lights up its parent; on mobile the subs are hidden, so the
       parent is what shows where you are */
    if (parent && parent !== link) parent.classList.add('in');
    if (rail.dataset.mode === 'spy') {
      $$('.rail-g', rail).forEach(function (g) { g.classList.toggle('open', g === group); });
    }
    var target = link.offsetParent === null && parent ? parent : link;
    var track = rail.querySelector('.scroller-track');
    if (track && track.scrollWidth > track.clientWidth) revealIn(track, target);
    /* a tall rail scrolls on its own; keep the active entry inside it without
       touching the page's scroll (scrollIntoView would move both) */
    if (rail.scrollHeight > rail.clientHeight + 2) {
      var rr = rail.getBoundingClientRect(), tr = target.getBoundingClientRect();
      if (tr.top < rr.top + 40) rail.scrollTop -= rr.top + 40 - tr.top;
      else if (tr.bottom > rr.bottom - 40) rail.scrollTop += tr.bottom - rr.bottom + 40;
    }
  }

  var spyLock = 0;
  function spy() {
    var panel = current && panelOf(current);
    if (!panel || Date.now() < spyLock) return;
    var rail = panel.querySelector('.rail[data-mode="spy"]');
    if (!rail) return;
    var links = $$('.rail-a', rail);
    var line = headH() + (win.matchMedia('(max-width: 900px)').matches ? 90 : 48);
    var active = links[0], activeTop = null;
    links.forEach(function (a) {
      var sec = doc.getElementById(a.dataset.target);
      if (!sec) return;
      var top = sec.getBoundingClientRect().top;
      if (top - line > 0) return;
      /* sections side by side in a grid pass the line together; the first of
         the row stands for it, which is where a reader's eye starts */
      if (activeTop !== null && Math.abs(top - activeTop) < 4) return;
      active = a; activeTop = top;
    });
    if (win.innerHeight + win.scrollY >= doc.body.scrollHeight - 4) active = links[links.length - 1];
    if (!active.classList.contains('on')) setRail(rail, active);
  }

  doc.addEventListener('click', function (e) {
    var a = e.target.closest('.rail-a');
    if (!a) return;
    var rail = a.closest('.rail');
    if (rail.dataset.mode === 'spy') {
      var sec = doc.getElementById(a.dataset.target);
      if (!sec) return;
      setRail(rail, a);
      spyLock = Date.now() + 700;      /* do not let spy fight the smooth scroll */
      var off = headH() + (win.matchMedia('(max-width: 900px)').matches ? 56 : 16);
      win.scrollTo({ top: sec.getBoundingClientRect().top + win.scrollY - off,
                     behavior: reduce ? 'auto' : 'smooth' });
    } else if (rail.dataset.mode === 'filter') {
      var key = a.dataset.filter;
      var panel = rail.closest('.panel');
      setRail(rail, a);
      var k = 0;
      $$('.tg', panel).forEach(function (card) {
        var keep = key === '*' || card.dataset.ch === key;
        card.hidden = !keep;
        card.classList.remove('pop');
        if (keep) { card.style.setProperty('--k', k++); }
      });
      layoutMasonry(panel, true);
      $$('.tg:not([hidden])', panel).forEach(function (c) { void c.offsetWidth; c.classList.add('pop'); });
      var top = panel.getBoundingClientRect().top + win.scrollY - headH();
      if (win.scrollY > top) win.scrollTo({ top: top, behavior: reduce ? 'auto' : 'smooth' });
    }
  });

  /* ------------------------------------------------------------------
   * citations: jump to the item inside its own panel
   *
   * The digest panel is a view over items that live in other panels, so a
   * citation has to move the page. Order matters: show() first, because a
   * hidden panel has no geometry and scrollIntoView would do nothing; then
   * suppress the reveal animation, which restarts on unhide and would leave
   * the target transparent while it scrolls; then lock the spy, so the rail is
   * not dragged to a section the scroll merely passes through.
   * ------------------------------------------------------------------ */
  function flashAfterScroll(el) {
    var done = false;
    function end() {
      if (done) return;
      done = true;
      el.classList.add('flash');
      setTimeout(function () { el.classList.remove('flash'); }, 1600);
    }
    if ('onscrollend' in win) win.addEventListener('scrollend', end, { once: true });
    setTimeout(end, reduce ? 60 : 650);   /* scrollend support varies; this is the floor */
  }

  function jumpTo(id, instant, keepHash) {
    /* getElementById, never querySelector: item ids contain colons */
    var target = doc.getElementById('it-' + id);
    if (!target) return false;
    var panel = target.closest('.panel');
    if (panel && panel.id) show(panel.id.slice(2), { noHash: !!keepHash });
    target.classList.add('no-rv');
    spyLock = Date.now() + 900;
    target.scrollIntoView({ block: 'nearest', behavior: (instant || reduce) ? 'auto' : 'smooth' });
    flashAfterScroll(target);
    return true;
  }

  doc.addEventListener('click', function (e) {
    var a = e.target.closest('[data-jump]');
    if (!a) return;
    /* Keep the item id out of location.hash: boot reads that as a tab key, so
       a reload after a click would land on the first tab. */
    e.preventDefault();
    jumpTo(a.dataset.jump);
  });

  /* ------------------------------------------------------------------
   * masonry: cards are dealt in reading order to the shortest column.
   * Image sizes are written at build time, so heights are final up front.
   * ------------------------------------------------------------------ */
  function layoutMasonry(panel, force) {
    var box = panel && panel.querySelector('.mas');
    if (!box || !box.offsetWidth) return;
    var w = box.clientWidth;
    var n = Math.max(1, Math.min(4, Math.floor((w + 14) / (300 + 14))));
    if (!force && box.dataset.cols === String(n)) return;
    var cards = $$('.tg', box);
    box.classList.add('js');
    var cols = [];
    for (var i = 0; i < n; i++) {
      var c = doc.createElement('div');
      c.className = 'mas-col';
      cols.push(c);
    }
    $$('.mas-col', box).forEach(function (c) { c.remove(); });
    cols.forEach(function (c) { box.appendChild(c); });
    var heights = cols.map(function () { return 0; });
    cards.forEach(function (card) {
      var j = heights.indexOf(Math.min.apply(null, heights));
      cols[j].appendChild(card);
      if (!card.hidden) heights[j] += card.offsetHeight + 14;
    });
    box.dataset.cols = String(n);
  }

  /* ------------------------------------------------------------------
   * full-text expander
   * ------------------------------------------------------------------ */
  doc.addEventListener('click', function (e) {
    var btn = e.target.closest('.exp-btn, .exp-close');
    if (!btn) return;
    var wrap = btn.closest('.exp');
    var closing = wrap.classList.contains('open');
    if (closing && btn.classList.contains('exp-close')) {
      var anchor = wrap.closest('.ar, .feat') || wrap;
      var top = anchor.getBoundingClientRect().top - headH() - 12;
      if (top < 0) win.scrollBy({ top: top, behavior: reduce ? 'auto' : 'smooth' });
    }
    wrap.classList.toggle('open', !closing);
    var head = wrap.querySelector(':scope > .exp-btn');
    if (head) head.setAttribute('aria-expanded', closing ? 'false' : 'true');
  });

  /* ------------------------------------------------------------------
   * category fold on the front page: the first few headlines show, the
   * rest unfold in place. Folding back from far down the list returns the
   * reader to the category's head instead of leaving them in the next one.
   * ------------------------------------------------------------------ */
  doc.addEventListener('click', function (e) {
    var btn = e.target.closest('.cat-btn');
    if (!btn) return;
    var wrap = btn.closest('.cat-more');
    var opening = !wrap.classList.contains('open');
    if (!opening) {
      var head = wrap.closest('.cat') || wrap;
      var top = head.getBoundingClientRect().top - headH() - 12;
      if (top < 0) win.scrollBy({ top: top, behavior: reduce ? 'auto' : 'smooth' });
    }
    wrap.classList.toggle('open', opening);
    btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
  });

  /* ------------------------------------------------------------------
   * telegram long posts: the card keeps its clamped preview, the full post
   * opens in the overlay (same dialog as the merged originals), so the
   * masonry never reflows
   * ------------------------------------------------------------------ */
  function readerFor(card) {
    var node = card.cloneNode(true);
    node.removeAttribute('data-ch');
    node.removeAttribute('hidden');
    node.removeAttribute('style');
    node.classList.remove('pop');
    node.classList.add('rd');
    $$('.more', node).forEach(function (m) { m.remove(); });
    $$('.clamp', node).forEach(function (c) { c.classList.remove('clamp'); c.style.maxHeight = ''; });
    return node;
  }
  doc.addEventListener('click', function (e) {
    var btn = e.target.closest('.more');
    if (!btn) return;
    var card = btn.closest('.tg');
    if (!card) return;
    var ch = card.querySelector('.tg-ch');
    openOv(readerFor(card), (ch ? ch.textContent + ' · ' : '') + '全文');
  });

  /* ------------------------------------------------------------------
   * overlay: a stack of panels (full post, merged originals, image).
   * Opening from inside a panel pushes a new one on top; ×, Esc and the
   * backdrop close only the top panel and return to the one beneath.
   * ------------------------------------------------------------------ */
  var ov = $('#ov'), panelTpl = $('#ov-panel'), stack = [];
  var LEAVE_MS = 420;
  function topPanel() { return stack.length ? stack[stack.length - 1] : null; }
  function openOv(node, title, isImg) {
    if (!ov || !panelTpl) return;
    var panel = panelTpl.content.firstElementChild.cloneNode(true);
    panel.querySelector('.ov-h h2').textContent = title || '';
    panel.setAttribute('aria-label', title || '详情');
    panel.querySelector('.ov-b').appendChild(node);
    if (isImg) panel.classList.add('img');
    var under = topPanel();
    if (under) {
      under.el.classList.add('covered');
      under.el.setAttribute('aria-hidden', 'true');
      panel.classList.add('child');
    }
    stack.push({ el: panel, from: doc.activeElement });
    ov.appendChild(panel);
    if (stack.length === 1) {
      ov.classList.add('open');
      ov.setAttribute('aria-hidden', 'false');
      doc.body.style.overflow = 'hidden';
    }
    void panel.offsetWidth;                 /* commit the start state */
    panel.classList.add('on');
    var x = panel.querySelector('.ov-x');
    if (x) x.focus({ preventScroll: true });
  }
  function closeOv() {
    var top = stack.pop();
    if (!top) return;
    var el = top.el;
    el.classList.remove('on');
    el.classList.add('leaving');
    setTimeout(function () { el.remove(); }, reduce ? 0 : LEAVE_MS);
    var under = topPanel();
    if (under) {
      under.el.classList.remove('covered');
      under.el.removeAttribute('aria-hidden');
    } else {
      ov.classList.remove('open');
      ov.setAttribute('aria-hidden', 'true');
      doc.body.style.overflow = '';
    }
    if (top.from && top.from.focus && doc.contains(top.from)) top.from.focus({ preventScroll: true });
  }
  doc.addEventListener('click', function (e) {
    var trig = e.target.closest('[data-ov]');
    if (trig) {
      var tpl = doc.getElementById(trig.dataset.ov);
      if (tpl) openOv(tpl.content.cloneNode(true), trig.dataset.ovTitle || '原始消息');
      return;
    }
    var img = e.target.closest('.shots img');
    if (img && !img.closest('.ov')) {
      var big = doc.createElement('img');
      big.src = img.src; big.alt = img.alt || '';
      openOv(big, '', true);
      return;
    }
    if (!ov || !stack.length) return;
    var top = topPanel().el;
    if (e.target.closest('.ov-x, .ov-back')) { if (e.target.closest('.ov-p') === top) closeOv(); return; }
    if (e.target === ov) { closeOv(); return; }
    if (top.classList.contains('img') && e.target.closest('.ov-b') && top.contains(e.target)) closeOv();
  });
  /* keep Tab inside the top panel */
  doc.addEventListener('keydown', function (e) {
    if (e.key !== 'Tab' || !stack.length) return;
    var f = $$('a[href],button:not([disabled])', topPanel().el).filter(function (n) { return n.offsetParent !== null; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && doc.activeElement === first) { last.focus(); e.preventDefault(); }
    else if (!e.shiftKey && doc.activeElement === last) { first.focus(); e.preventDefault(); }
  });

  /* ------------------------------------------------------------------
   * keyboard: 1–9 / 0 jump, [ ] or ←/→ (outside inputs) step, Esc closes
   * ------------------------------------------------------------------ */
  doc.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeOv(); return; }
    if (stack.length) return;
    var tag = (e.target.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || e.metaKey || e.ctrlKey || e.altKey) return;
    var i = tabs.findIndex(function (t) { return t.dataset.tab === current; });
    if (e.key === 'ArrowRight' || e.key === ']') { if (tabs[i + 1]) show(tabs[i + 1].dataset.tab); e.preventDefault(); return; }
    if (e.key === 'ArrowLeft' || e.key === '[') { if (tabs[i - 1]) show(tabs[i - 1].dataset.tab); e.preventDefault(); return; }
    var n = parseInt(e.key, 10);
    if (n >= 1 && n <= 9 && tabs[n - 1]) show(tabs[n - 1].dataset.tab);
    else if (n === 0 && tabs[9]) show(tabs[9].dataset.tab);
  });

  /* ------------------------------------------------------------------
   * theme: system by default, manual choice persisted. The initial value
   * is set by an inline script in <head> so there is no flash.
   * ------------------------------------------------------------------ */
  var THEME_KEY = 'digest-theme';
  var themeBtn = $('.theme-btn');
  function applyTheme(t) {
    root.dataset.theme = t;
    var meta = $('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', getComputedStyle(root).getPropertyValue('--bg').trim());
    if (themeBtn) themeBtn.setAttribute('aria-label', t === 'dark' ? '切换到浅色' : '切换到深色');
  }
  if (themeBtn) {
    applyTheme(root.dataset.theme || 'dark');
    themeBtn.addEventListener('click', function () {
      var next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
      themeBtn.classList.add('spin');
      setTimeout(function () { themeBtn.classList.remove('spin'); }, 420);
      if (doc.startViewTransition && !reduce) {
        doc.startViewTransition(function () { applyTheme(next); });
      } else {
        root.classList.add('theming');
        applyTheme(next);
        setTimeout(function () { root.classList.remove('theming'); }, 450);
      }
    });
  }
  /* follow the OS while the user has not chosen */
  if (win.matchMedia) {
    var mq = win.matchMedia('(prefers-color-scheme: light)');
    var onSys = function (e) {
      var saved = null;
      try { saved = localStorage.getItem(THEME_KEY); } catch (err) {}
      if (!saved) applyTheme(e.matches ? 'light' : 'dark');
    };
    if (mq.addEventListener) mq.addEventListener('change', onSys);
  }

  /* ------------------------------------------------------------------
   * CJK serif detection. Latin serif next to CJK sans looks broken, so
   * when no decent CJK serif is installed the serif stack becomes sans.
   * The result is cached so later visits apply it before first paint.
   * ------------------------------------------------------------------ */
  (function detectSerif() {
    if (!win.FontFace || !doc.fonts) return;
    var names = ['Songti SC Regular', 'STSongti-SC-Regular', 'Songti SC', 'Noto Serif CJK SC', 'Noto Serif CJK SC Regular',
                 'NotoSerifCJKsc-Regular', 'Source Han Serif SC', 'Source Han Serif SC Regular', 'SourceHanSerifSC-Regular',
                 'Noto Serif SC', 'NotoSerifSC-Regular', 'STSong'];
    var src = names.map(function (n) { return 'local("' + n + '")'; }).join(',');
    var face;
    try { face = new FontFace('DB Serif Probe', src); } catch (e) { return; }
    face.load().then(function () { set(true); }, function () { set(false); });
    function set(ok) {
      root.classList.toggle('no-cjk-serif', !ok);
      try { localStorage.setItem('digest-cjk-serif', ok ? '1' : '0'); } catch (e) {}
      setTimeout(function () {
        moveInk(tabs.find(function (t) { return t.dataset.tab === current; }), true);
        var p = current && panelOf(current);
        if (p) layoutMasonry(p, true);
      }, 30);
    }
  })();

  /* ------------------------------------------------------------------
   * boot
   * ------------------------------------------------------------------ */
  var raf = 0;
  win.addEventListener('scroll', function () {
    if (raf) return;
    raf = requestAnimationFrame(function () { raf = 0; spy(); });
  }, { passive: true });

  var rz = 0;
  win.addEventListener('resize', function () {
    clearTimeout(rz);
    rz = setTimeout(function () {
      headH(); syncAllScrollers();
      moveInk(tabs.find(function (t) { return t.dataset.tab === current; }), true);
      var p = current && panelOf(current);
      if (p) layoutMasonry(p);
    }, 120);
  });

  headH();
  $$('.panel').forEach(function (p) { if (!p.classList.contains('on')) p.hidden = true; });
  /* #<tab> or #<tab>/it-<item id>. When both are present the item wins: a deep
     link should land on what it names, not on whichever panel it happened to
     spell first. */
  var hash = (location.hash || '').slice(1);
  var parts = hash.split('/');
  var deepId = (parts[1] || '').replace(/^it-/, '');
  var deepEl = deepId ? doc.getElementById('it-' + deepId) : null;
  var first = parts[0];
  var initial = deepEl ? deepEl.closest('.panel').id.slice(2)
                       : (panelOf(first) ? first : (tabs[0] && tabs[0].dataset.tab));
  $$('.panel.on').forEach(function (p) { p.classList.remove('on'); });
  if (initial) show(initial, { instant: true, keepScroll: true, noHash: !!deepEl || !first });
  /* after show(), which rewrites the hash */
  if (deepEl) jumpTo(deepId, true, true);
  $$('.rail').forEach(function (r) {
    var on = $('.rail-a.on', r) || $('.rail-a', r);
    if (on) setRail(r, on);
  });
  /* fonts and images can shift widths after first paint */
  win.addEventListener('load', function () {
    headH(); syncAllScrollers();
    moveInk(tabs.find(function (t) { return t.dataset.tab === current; }), true);
    var p = current && panelOf(current);
    if (p) layoutMasonry(p, true);
  });
  root.dataset.ready = '1';
})();
