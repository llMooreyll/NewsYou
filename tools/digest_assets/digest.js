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
    panel.classList.add('on', oldIdx < 0 ? 'in-n' : (newIdx > oldIdx ? 'in-r' : 'in-l'));
    current = id;

    var tab = tabs[newIdx];
    moveInk(tab, opts.instant);
    revealIn(tabTrack, tab);

    if (!opts.keepScroll) win.scrollTo(0, scrollMemo[id] || 0);
    if (history.replaceState && !opts.noHash) history.replaceState(null, '', '#' + id);

    layoutMasonry(panel);
    $$('.scroller', panel).forEach(syncScroller);
    /* rails measured while hidden have no geometry; place their ink now */
    $$('.rail', panel).forEach(function (r) {
      var on = $('.rail-a.on', r) || $('.rail-a', r);
      if (on) setRail(r, on, true);
    });
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
   * ------------------------------------------------------------------ */
  function railInk(rail, link, instant) {
    var bar = rail.querySelector('.rail-ink');
    if (!bar || !link) return;
    var horizontal = win.matchMedia('(max-width: 900px)').matches;
    if (instant) bar.style.transition = 'none';
    if (horizontal) {
      bar.style.height = '';
      bar.style.width = link.offsetWidth - 24 + 'px';
      bar.style.transform = 'translateX(' + (link.offsetLeft + 12) + 'px)';
    } else {
      bar.style.width = '';
      bar.style.height = link.offsetHeight + 'px';
      bar.style.transform = 'translateY(' + link.offsetTop + 'px)';
    }
    if (instant) { void bar.offsetWidth; bar.style.transition = ''; }
  }
  function setRail(rail, link, instant) {
    $$('.rail-a', rail).forEach(function (a) { a.classList.toggle('on', a === link); });
    railInk(rail, link, instant);
    var track = rail.querySelector('.scroller-track');
    if (track && track.scrollWidth > track.clientWidth) revealIn(track, link);
  }

  var spyLock = 0;
  function spy() {
    var panel = current && panelOf(current);
    if (!panel || Date.now() < spyLock) return;
    var rail = panel.querySelector('.rail[data-mode="spy"]');
    if (!rail) return;
    var links = $$('.rail-a', rail);
    var line = headH() + (win.matchMedia('(max-width: 900px)').matches ? 90 : 48);
    var active = links[0];
    links.forEach(function (a) {
      var sec = doc.getElementById(a.dataset.target);
      if (sec && sec.getBoundingClientRect().top - line <= 0) active = a;
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
   * telegram long-post clamp, animated with a measured max-height
   * ------------------------------------------------------------------ */
  doc.addEventListener('click', function (e) {
    var btn = e.target.closest('.more');
    if (!btn) return;
    var box = btn.parentNode.querySelector('.clamp');
    if (!box) return;
    var open = box.classList.contains('open');
    if (!open) {
      box.style.maxHeight = box.scrollHeight + 'px';
      box.classList.add('open');
      btn.textContent = '收起';
      var done = function () { if (box.classList.contains('open')) box.style.maxHeight = 'none'; box.removeEventListener('transitionend', done); };
      box.addEventListener('transitionend', done);
    } else {
      box.style.maxHeight = box.scrollHeight + 'px';
      void box.offsetHeight;
      box.classList.remove('open');
      box.style.maxHeight = '';
      btn.textContent = '展开全文';
      var card = box.closest('.tg');
      var t = card.getBoundingClientRect().top - headH() - 12;
      if (t < 0) win.scrollBy({ top: t, behavior: reduce ? 'auto' : 'smooth' });
    }
    btn.setAttribute('aria-expanded', open ? 'false' : 'true');
  });

  /* ------------------------------------------------------------------
   * overlay: merged originals and the image lightbox
   * ------------------------------------------------------------------ */
  var ov = $('#ov'), ovBody = ov && $('.ov-b', ov), ovTitle = ov && $('.ov-h h2', ov), lastFocus = null;
  function openOv(node, title, isImg) {
    if (!ov) return;
    lastFocus = doc.activeElement;
    ovBody.innerHTML = '';
    ovBody.appendChild(node);
    ovTitle.textContent = title || '';
    ov.classList.toggle('img', !!isImg);
    ov.classList.add('open');
    ov.setAttribute('aria-hidden', 'false');
    doc.body.style.overflow = 'hidden';
    ovBody.scrollTop = 0;
    var x = $('.ov-x', ov); if (x) x.focus({ preventScroll: true });
  }
  function closeOv() {
    if (!ov || !ov.classList.contains('open')) return;
    ov.classList.remove('open');
    ov.setAttribute('aria-hidden', 'true');
    doc.body.style.overflow = '';
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }
  doc.addEventListener('click', function (e) {
    var trig = e.target.closest('[data-ov]');
    if (trig) {
      var tpl = doc.getElementById(trig.dataset.ov);
      if (tpl) openOv(tpl.content.cloneNode(true), trig.dataset.ovTitle || '原始消息');
      return;
    }
    var img = e.target.closest('.shots img');
    if (img) {
      var big = doc.createElement('img');
      big.src = img.src; big.alt = img.alt || '';
      openOv(big, '', true);
      return;
    }
    if (e.target.closest('.ov-x') || e.target === ov || (ov && ov.classList.contains('img') && e.target.closest('.ov-b'))) closeOv();
  });

  /* ------------------------------------------------------------------
   * keyboard: 1–9 / 0 jump, [ ] or ←/→ (outside inputs) step, Esc closes
   * ------------------------------------------------------------------ */
  doc.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeOv(); return; }
    if (ov && ov.classList.contains('open')) return;
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
      if (p) {
        layoutMasonry(p);
        $$('.rail', p).forEach(function (r) { var on = $('.rail-a.on', r); if (on) railInk(r, on, true); });
      }
    }, 120);
  });

  headH();
  $$('.panel').forEach(function (p) { if (!p.classList.contains('on')) p.hidden = true; });
  var first = (location.hash || '').slice(1);
  var initial = panelOf(first) ? first : (tabs[0] && tabs[0].dataset.tab);
  $$('.panel.on').forEach(function (p) { p.classList.remove('on'); });
  if (initial) show(initial, { instant: true, keepScroll: true, noHash: !first });
  $$('.rail').forEach(function (r) {
    var on = $('.rail-a.on', r) || $('.rail-a', r);
    if (on) setRail(r, on, true);
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
