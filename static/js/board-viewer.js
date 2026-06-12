// Visual VASL board viewer (Phase 1: read-only pan/zoom rendering).
//
// Authed chat only — demo never loads this file. When a .vsav is attached
// (the #vsav-file-input shared with chat-shared.js), the save is POSTed to
// /api/vsav/preview, which returns a render manifest:
//   { map: {width, height, background_url},
//     background: {url, x, y, width, height, missing_boards} | null,
//     geometry: {dx, dy, edge, board_w, board_h},
//     boards: [{name, base, reversed, crop, x, y, width, height}],
//     pieces: [{name, px, py, hex, stack, stack_index, stack_size,
//               is_marker, art: [layer paths bottom->top], side?, flags?,
//               label?, counter?}] }   // pieces are in draw order
// ALL coordinates are raw VASL map pixels (400px edge margin included);
// the background PNG covers only the board area and carries its own
// offset, so it is simply placed at (background.x, background.y).
//
// Interactions: drag = pan, wheel / pinch = zoom, hover = hex badge,
// click stack = fan it out, click counter = tooltip, click elsewhere =
// collapse. Counters render at their natural pixel size (VASL art is
// drawn 1:1 against the 56.25px hex pitch) with a small cascade offset
// per stack level so depth is visible and the top counter hides those
// below (fair-play concealment is preserved visually).

(function () {
  'use strict';

  const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')
    .concat(['AA', 'BB', 'CC', 'DD', 'EE', 'FF', 'GG']);
  const CASCADE = 3;          // px offset per stack level (up-left)
  const FAN_STEP = 54;        // px horizontal spread when a stack is fanned
  const MIN_SCALE = 0.1;
  const MAX_SCALE = 4;

  const SIDE_RGB = {
    Finnish: '#787887', Russian: '#a06e3c', German: '#5a8cc8',
    American: '#6b7d4f', British: '#c8a050', Japanese: '#c8b450',
  };

  let panel = null, viewport = null, world = null, badge = null,
      tooltip = null, titleEl = null;
  let tooltipFor = null;   // piece currently shown, for click-to-toggle
  let manifest = null;
  let tx = 0, ty = 0, scale = 1;
  let fannedStack = null;

  // ----------------------------------------------------------------
  // Geometry: map pixel -> hex label (port of vsav_service.map_xy_to_hex)
  // ----------------------------------------------------------------

  function hexAt(x, y) {
    if (!manifest) return null;
    const g = manifest.geometry;
    for (const b of manifest.boards) {
      if (x < b.x || x >= b.x + b.width || y < b.y || y > b.y + b.height) continue;
      const lx = x - b.x, ly = y - b.y;
      const cw = b.crop.w > 0 ? b.crop.w : g.board_w;
      const ch = b.crop.h > 0 ? b.crop.h : g.board_h;
      let xo, yo;
      if (b.reversed) { xo = b.crop.x + cw - lx; yo = b.crop.y + ch - ly; }
      else            { xo = b.crop.x + lx;      yo = b.crop.y + ly; }
      let i = Math.round(xo / g.dx);
      i = Math.max(0, Math.min(LETTERS.length - 1, i));
      let r;
      if (i % 2 === 1) r = Math.max(0, Math.min(10, Math.round(yo / g.dy)));
      else r = Math.max(1, Math.min(10, Math.round((yo - g.dy / 2) / g.dy) + 1));
      return b.base + '-' + LETTERS[i] + r;
    }
    return null;
  }

  function artUrl(path) {
    return '/api/counter-art/' +
      path.split('/').map(encodeURIComponent).join('/');
  }

  // ----------------------------------------------------------------
  // Panel + DOM
  // ----------------------------------------------------------------

  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement('div');
    panel.id = 'board-viewer';
    panel.className = 'board-viewer';
    panel.style.display = 'none';
    panel.innerHTML =
      '<div class="bv-header">' +
        '<span class="bv-title">Board</span>' +
        '<span class="bv-hex-badge"></span>' +
        '<span class="bv-spacer"></span>' +
        '<button type="button" class="bv-btn bv-collapse" title="Collapse / expand">▾</button>' +
        '<button type="button" class="bv-btn bv-close" title="Close viewer">×</button>' +
      '</div>' +
      '<div class="bv-body">' +
        '<div class="bv-viewport">' +
          '<div class="bv-world"></div>' +
          '<div class="bv-tooltip" style="display:none"></div>' +
        '</div>' +
      '</div>';
    const dock = document.querySelector('.input-dock');
    if (dock && dock.parentNode) dock.parentNode.insertBefore(panel, dock);
    else document.body.appendChild(panel);

    viewport = panel.querySelector('.bv-viewport');
    world = panel.querySelector('.bv-world');
    badge = panel.querySelector('.bv-hex-badge');
    tooltip = panel.querySelector('.bv-tooltip');
    titleEl = panel.querySelector('.bv-title');
    // the whole tooltip dismisses on click/tap (the × is a visual hint)
    tooltip.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      hideTooltip();
    });

    panel.querySelector('.bv-close').addEventListener('click', () => {
      panel.style.display = 'none';
    });
    panel.querySelector('.bv-collapse').addEventListener('click', (e) => {
      const body = panel.querySelector('.bv-body');
      const hidden = body.style.display === 'none';
      body.style.display = hidden ? '' : 'none';
      e.target.textContent = hidden ? '▾' : '▸';
    });

    bindViewportEvents();
    return panel;
  }

  function applyTransform() {
    world.style.transform =
      'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
  }

  function pieceOffset(p, fanned) {
    // cascade up-left by stack level; fan spreads the stack to the right
    if (fanned) {
      return { x: p.stack_index * FAN_STEP, y: 0 };
    }
    return { x: -CASCADE * p.stack_index, y: -CASCADE * p.stack_index };
  }

  function renderManifest(man, saveName) {
    manifest = man;
    ensurePanel();
    titleEl.textContent = 'Board — ' + (saveName || 'VASL save');
    world.innerHTML = '';
    world.style.width = man.map.width + 'px';
    world.style.height = man.map.height + 'px';
    fannedStack = null;
    hideTooltip();

    if (man.background && man.map.background_url) {
      const bg = document.createElement('img');
      bg.className = 'bv-bg';
      bg.src = man.map.background_url;
      bg.style.left = man.background.x + 'px';
      bg.style.top = man.background.y + 'px';
      bg.width = man.background.width;
      bg.height = man.background.height;
      bg.draggable = false;
      world.appendChild(bg);
    }

    man.pieces.forEach((p, i) => {
      const el = document.createElement('div');
      el.className = 'bv-piece';
      el.dataset.idx = i;
      const off = pieceOffset(p, false);
      el.style.left = (p.px + off.x) + 'px';
      el.style.top = (p.py + off.y) + 'px';
      el.style.zIndex = 10 + i;

      // fallback chip (replaced by art when the first layer loads)
      const fb = document.createElement('div');
      fb.className = 'bv-fallback';
      fb.style.background = SIDE_RGB[p.side] || '#b0a890';
      fb.textContent = (p.name || '?').slice(0, 8);
      el.appendChild(fb);

      (p.art || []).forEach((a) => {
        const img = document.createElement('img');
        img.className = 'bv-art';
        img.draggable = false;
        img.addEventListener('load', () => {
          img.style.width = img.naturalWidth + 'px';
          img.style.height = img.naturalHeight + 'px';
          img.style.display = '';
          fb.style.display = 'none';
        });
        img.addEventListener('error', () => { img.remove(); });
        img.style.display = 'none';
        img.src = artUrl(a);
        el.appendChild(img);
      });

      world.appendChild(el);
    });

    fitToBoards();
    panel.style.display = '';
    panel.querySelector('.bv-body').style.display = '';
    panel.querySelector('.bv-collapse').textContent = '▾';
  }

  function fitToBoards() {
    // fit the board area (background box if present, else the whole map)
    const vw = viewport.clientWidth || 600;
    const vh = viewport.clientHeight || 320;
    const box = manifest.background ||
      { x: 0, y: 0, width: manifest.map.width, height: manifest.map.height };
    scale = Math.min(vw / box.width, vh / box.height);
    scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale));
    tx = (vw - box.width * scale) / 2 - box.x * scale;
    ty = (vh - box.height * scale) / 2 - box.y * scale;
    applyTransform();
  }

  // ----------------------------------------------------------------
  // Stacks, tooltip
  // ----------------------------------------------------------------

  function setStackFanned(stackNo, fanned) {
    manifest.pieces.forEach((p, i) => {
      if (p.stack !== stackNo) return;
      const el = world.querySelector('.bv-piece[data-idx="' + i + '"]');
      if (!el) return;
      const off = pieceOffset(p, fanned);
      el.style.left = (p.px + off.x) + 'px';
      el.style.top = (p.py + off.y) + 'px';
      el.style.zIndex = (fanned ? 500 : 10) + i;
      el.classList.toggle('bv-fanned', fanned);
    });
  }

  function collapseFanned() {
    if (fannedStack !== null) setStackFanned(fannedStack, false);
    fannedStack = null;
    hideTooltip();
  }

  function pieceTooltipHtml(p) {
    const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const parts = [];
    let title = esc(p.name);
    if (p.side) title = esc(p.side) + ' ' + title;
    parts.push('<strong>' + title + '</strong>');
    if (p.counter && p.counter !== p.name) parts.push('counter: ' + esc(p.counter));
    if (p.hex) parts.push('hex ' + esc(p.hex));
    const f = p.flags || {};
    const flags = [];
    if (f.broken) flags.push('BROKEN');
    if (f.concealed_by) flags.push('concealed');
    if (f.hip_by) flags.push('HIP');
    // skis is "worn" (ski mode) or "carried" (1 PP); legacy saves: true
    if (f.skis) flags.push(f.skis === true ? 'skis' : 'skis ' + f.skis);
    if (f.bicycle) flags.push('bicycle');
    if (flags.length) parts.push(flags.join(', '));
    if (p.label) parts.push('label: ' + esc(p.label));
    if (p.is_marker) parts.push('(marker)');
    if (p.stack_size > 1) {
      parts.push('stack ' + (p.stack_index + 1) + '/' + p.stack_size +
                 ' (bottom→top)');
    }
    return parts.join('<br>');
  }

  function showTooltip(p, el) {
    tooltipFor = p;
    tooltip.innerHTML =
      '<span class="bv-tt-close" title="Dismiss">×</span>' +
      pieceTooltipHtml(p);
    tooltip.style.display = '';
    const vr = viewport.getBoundingClientRect();
    const er = el.getBoundingClientRect();
    let x = er.right - vr.left + 8;
    let y = er.top - vr.top;
    tooltip.style.left = '0px'; tooltip.style.top = '0px';
    const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
    if (x + tw > vr.width) x = Math.max(4, er.left - vr.left - tw - 8);
    if (y + th > vr.height) y = Math.max(4, vr.height - th - 4);
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  }

  function hideTooltip() {
    tooltip.style.display = 'none';
    tooltipFor = null;
  }

  function handleClick(target) {
    const pieceEl = target.closest ? target.closest('.bv-piece') : null;
    if (!pieceEl) { collapseFanned(); return; }
    const p = manifest.pieces[+pieceEl.dataset.idx];
    if (p.stack_size > 1 && fannedStack !== p.stack) {
      collapseFanned();
      fannedStack = p.stack;
      setStackFanned(p.stack, true);
      showTooltip(p, pieceEl);
    } else if (tooltipFor === p) {
      hideTooltip();           // second click on the same counter toggles off
    } else {
      showTooltip(p, pieceEl);
    }
  }

  // ----------------------------------------------------------------
  // Pan / zoom / hover
  // ----------------------------------------------------------------

  function bindViewportEvents() {
    const pointers = new Map();   // pointerId -> {x, y}
    let dragging = false, moved = 0;
    let lastPinchDist = null;
    let downTarget = null;        // element under the FIRST pointerdown —
                                  // setPointerCapture retargets pointerup
                                  // to the viewport, so remember it here

    viewport.addEventListener('pointerdown', (e) => {
      if (pointers.size === 0) downTarget = e.target;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      viewport.setPointerCapture(e.pointerId);
      dragging = true;
      moved = 0;
      lastPinchDist = null;
      e.preventDefault();
    });

    viewport.addEventListener('pointermove', (e) => {
      if (pointers.has(e.pointerId) && dragging) {
        if (pointers.size === 2) {
          // pinch zoom
          pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
          const pts = [...pointers.values()];
          const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
          if (lastPinchDist) {
            const vr = viewport.getBoundingClientRect();
            const cx = (pts[0].x + pts[1].x) / 2 - vr.left;
            const cy = (pts[0].y + pts[1].y) / 2 - vr.top;
            zoomAt(cx, cy, dist / lastPinchDist);
          }
          lastPinchDist = dist;
          moved += 10;
        } else {
          const prev = pointers.get(e.pointerId);
          const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
          pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
          tx += dx; ty += dy;
          moved += Math.abs(dx) + Math.abs(dy);
          if (moved >= 5) hideTooltip();   // panning — get out of the way
          applyTransform();
        }
      }
      // hover hex badge
      const vr = viewport.getBoundingClientRect();
      const wx = (e.clientX - vr.left - tx) / scale;
      const wy = (e.clientY - vr.top - ty) / scale;
      const hx = hexAt(wx, wy);
      badge.textContent = hx || '';
    });

    const endPointer = (e) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.delete(e.pointerId);
      if (pointers.size === 0) {
        dragging = false;
        if (moved < 5) handleClick(downTarget || e.target);  // click, not drag
        downTarget = null;
      }
      lastPinchDist = null;
    };
    viewport.addEventListener('pointerup', endPointer);
    viewport.addEventListener('pointercancel', endPointer);

    viewport.addEventListener('wheel', (e) => {
      e.preventDefault();
      hideTooltip();                       // zooming — get out of the way
      const vr = viewport.getBoundingClientRect();
      zoomAt(e.clientX - vr.left, e.clientY - vr.top,
             Math.exp(-e.deltaY * 0.0015));
    }, { passive: false });

    viewport.addEventListener('pointerleave', () => { badge.textContent = ''; });
  }

  function zoomAt(cx, cy, factor) {
    const next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * factor));
    factor = next / scale;
    if (factor === 1) return;
    tx = cx - (cx - tx) * factor;
    ty = cy - (cy - ty) * factor;
    scale = next;
    applyTransform();
  }

  // ----------------------------------------------------------------
  // Wiring: preview on .vsav attach
  // ----------------------------------------------------------------

  async function previewVsav(file) {
    const dataUrl = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = reject;
      r.readAsDataURL(file);
    });
    const resp = await fetch('/api/vsav/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vsav: dataUrl }),
    });
    if (!resp.ok) {
      let detail = 'preview failed';
      try { detail = (await resp.json()).detail || detail; } catch (e) { /* noop */ }
      throw new Error(detail);
    }
    return resp.json();
  }

  function initBoardViewer() {
    const fileInput = document.getElementById('vsav-file-input');
    if (!fileInput) return;
    // Additional listener alongside chat-shared.js's attach handler — the
    // shared handler (also used by demo) stays untouched.
    fileInput.addEventListener('change', () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file || !file.name.toLowerCase().endsWith('.vsav')) return;
      previewVsav(file)
        .then((man) => renderManifest(man, file.name))
        .catch((err) => console.warn('Board viewer preview failed:', err.message));
    });
  }

  window.initBoardViewer = initBoardViewer;
})();
