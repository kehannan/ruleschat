// Shared utilities for ruleschat and demo chat pages.

// ============================================================
// Model pricing
// ============================================================

// USD per 1M tokens (input, output). Keys match the dropdown `value`
// attributes — the COST chip uses these to convert token counts shown in
// the latency-row. OpenRouter prices are approximate; verify against the
// live OpenRouter dashboard if exact COST chips matter.
const MODEL_PRICING = {
    'gpt-5-mini':   { input: 0.25, output: 1.00 },
    'gpt-4.1-mini': { input: 0.40, output: 1.60 },
    'gpt-5.4-mini': { input: 0.25, output: 2.00 },
    'gpt-5.4':      { input: 3.00, output: 15.00 },
    'deepseek-v3':  { input: 0.27, output: 1.10 },
    'mercury-2':    { input: 0.25, output: 1.00 },
    'fable':        { input: 10.00, output: 50.00 },
};

function getModelPricing() {
    const sel = document.getElementById('model-selector');
    const model = sel ? sel.value : 'gpt-5-mini';
    return MODEL_PRICING[model] || MODEL_PRICING['gpt-5-mini'];
}

// ============================================================
// Latency / footer display
// ============================================================

function displayLatencyTimeline(latencyData) {
    const fileSearchMs  = latencyData.file_search_time_ms || 0;
    const ttftMs        = latencyData.ttft_ms || 0;
    const totalMs       = latencyData.total_time_ms || 0;
    const inputTokens   = latencyData.input_tokens || 0;
    const outputTokens  = latencyData.output_tokens || 0;
    // Inference = wall-clock minus the RAG round-trip. Server provides this
    // as `inference_ms`; fall back to derived value if the server hasn't
    // been updated yet (older payloads in the DB, or queries with no RAG).
    const inferenceMs   = latencyData.inference_ms != null
        ? latencyData.inference_ms
        : (totalMs > 0 ? Math.max(0, totalMs - fileSearchMs) : 0);

    const formatTime = (ms) => ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
    const formatTokens = (n) => n >= 1000 ? `${(n / 1000).toFixed(1)}K` : `${n}`;
    const formatCostCents = (d) => {
        const cents = d * 100;
        if (cents < 1)  return `¢${cents.toFixed(2)}`;
        if (cents < 10) return `¢${cents.toFixed(2)}`;
        return `¢${cents.toFixed(1)}`;
    };
    const formatCostDollars = (d) => {
        if (d < 0.001) return `$${(d * 1000).toFixed(2)}m`;
        if (d < 0.01)  return `$${d.toFixed(4)}`;
        return `$${d.toFixed(3)}`;
    };

    // Build chips for the new design (mono 11px). Order: TTFT · RAG · INFER ·
    // TOTAL · TOKENS · COST. The INFER chip is the new breakdown — it makes
    // it obvious how much wall-clock time is retrieval vs the model itself,
    // which matters for OpenRouter models that pay an extra RAG round-trip.
    const chips = [];
    if (ttftMs > 0)       chips.push({ label: 'TTFT',  value: formatTime(ttftMs) });
    if (fileSearchMs > 0) chips.push({ label: 'RAG',   value: formatTime(fileSearchMs) });
    if (inferenceMs > 0)  chips.push({ label: 'INFER', value: formatTime(inferenceMs) });
    if (totalMs > 0)      chips.push({ label: 'TOTAL', value: formatTime(totalMs) });
    if (inputTokens > 0 || outputTokens > 0) {
        const pricing   = getModelPricing();
        const totalCost = (inputTokens / 1_000_000) * pricing.input +
                          (outputTokens / 1_000_000) * pricing.output;
        chips.push({ label: 'TOKENS', value: `${formatTokens(inputTokens)}→${formatTokens(outputTokens)}` });
        chips.push({ label: 'COST',   value: formatCostCents(totalCost) });
    }

    // New surface: per-assistant-message .latency-row block. Populate the most
    // recently-created assistant message (.msg.assistant or legacy .bot-message).
    const lastAssistant = document.querySelector(
        '.msg.assistant:last-of-type, .bot-message:last-of-type'
    );
    if (lastAssistant) {
        let row = lastAssistant.querySelector('.latency-row');
        if (!row) {
            row = document.createElement('div');
            row.className = 'latency-row';
            lastAssistant.appendChild(row);
        }
        row.innerHTML = chips.map(c =>
            `<span><span class="ll-num">${c.label}</span> ${c.value}</span>`
        ).join('');
        row.style.display = chips.length ? 'flex' : 'none';
    }

    // Legacy surface: single #latency-display strip below the input (still used
    // by ruleschat.html until that page is migrated).
    const latencyDisplay = document.getElementById('latency-display');
    if (latencyDisplay) {
        const legacyParts = [];
        if (fileSearchMs > 0) legacyParts.push(`RAG: ${formatTime(fileSearchMs)}`);
        if (ttftMs > 0)       legacyParts.push(`TTFT: ${formatTime(ttftMs)}`);
        if (totalMs > 0)      legacyParts.push(`Total: ${formatTime(totalMs)}`);
        if (inputTokens > 0 || outputTokens > 0) {
            const pricing   = getModelPricing();
            const totalCost = (inputTokens / 1_000_000) * pricing.input +
                              (outputTokens / 1_000_000) * pricing.output;
            legacyParts.push(`${formatTokens(inputTokens)} in / ${formatTokens(outputTokens)} out • Cost: ${formatCostDollars(totalCost)}`);
        }
        if (legacyParts.length > 0) {
            latencyDisplay.textContent = legacyParts.join(' • ');
            latencyDisplay.style.display = 'block';
        } else {
            latencyDisplay.style.display = 'none';
        }
    }
}

// ============================================================
// Section reference links
// ============================================================

let sectionPageMap = null;
fetch('/static/rulebook/section_pages.json')
    .then(r => r.json())
    .then(data => { sectionPageMap = data; })
    .catch(e => console.warn('Could not load section page map:', e));

function makeSectionReferencesClickable(element) {
    const sectionWithPage = /\{([A-Z]?\d+\.\d+(?:\.\d+)?)\|(\d+)\}/g;
    const sectionPattern  = /\b([A-Z]?\d+\.\d+(?:\.\d+)?)\b/g;
    const perrySezPattern = /\bPerry\s+Sez\b/gi;
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);

    const textNodes = [];
    let node;
    while (node = walker.nextNode()) textNodes.push(node);

    textNodes.forEach(textNode => {
        const text = textNode.textContent;
        const all = [];

        for (const m of text.matchAll(sectionWithPage)) {
            all.push({ type: 'sectionPage', start: m.index, end: m.index + m[0].length, section: m[1], page: parseInt(m[2]) });
        }
        for (const m of text.matchAll(perrySezPattern)) {
            all.push({ type: 'perrySez', start: m.index, end: m.index + m[0].length });
        }
        for (const m of text.matchAll(sectionPattern)) {
            all.push({ type: 'section', start: m.index, end: m.index + m[0].length, section: m[0] });
        }

        if (all.length === 0) return;

        all.sort((a, b) => a.start - b.start);

        const filtered = [];
        let lastEnd = -1;
        for (const m of all) {
            if (m.start < lastEnd) continue;
            filtered.push(m);
            lastEnd = m.end;
        }

        const fragment = document.createDocumentFragment();
        let lastIndex = 0;

        for (const m of filtered) {
            if (m.start > lastIndex) fragment.appendChild(document.createTextNode(text.substring(lastIndex, m.start)));

            const link = document.createElement('span');
            link.className = 'cite';

            if (m.type === 'sectionPage') {
                link.textContent = m.section;
                link.setAttribute('data-page', m.page);
                link.onclick = (e) => { e.preventDefault(); openPdfModal(m.section, m.page); };
            } else if (m.type === 'section') {
                link.textContent = m.section;
                link.onclick = (e) => { e.preventDefault(); openPdfModal(m.section); };
            } else if (m.type === 'perrySez') {
                link.textContent = 'PS';
                link.title = 'Perry Sez';
                link.onclick = (e) => { e.preventDefault(); openPerrySezModal(); };
            }

            fragment.appendChild(link);
            lastIndex = m.end;
        }

        if (lastIndex < text.length) fragment.appendChild(document.createTextNode(text.substring(lastIndex)));
        textNode.parentNode.replaceChild(fragment, textNode);
    });
}

// ============================================================
// PDF viewer
// ============================================================

const PDF_SOURCES = {
    rulebook: {
        url: '/static/rulebook/eASLRB_v3_14_INHERIT_ZOOM.pdf',
        title: 'ASL Rulebook (eASLRB v3.14)',
        doc: null,
        preloadPromise: null,
    },
    perrySez: {
        url: '/static/rulebook/Perry-Sez-v34.pdf',
        title: 'Perry Sez (v34)',
        doc: null,
        preloadPromise: null,
    },
};

let pdfDoc = null;
let currentSource = 'rulebook';
let currentPage = 1;
let totalPages = 0;
let scale = 1.5;
const devicePixelRatio = window.devicePixelRatio || 1;

function preloadPdf(source = 'rulebook') {
    const s = PDF_SOURCES[source];
    if (s.doc || s.preloadPromise) return s.preloadPromise;
    s.preloadPromise = pdfjsLib.getDocument(s.url).promise.then(doc => {
        s.doc = doc;
        return doc;
    }).catch(err => {
        console.warn(`PDF: Preload failed for ${source}:`, err);
        s.preloadPromise = null;
    });
    return s.preloadPromise;
}

async function switchPdfSource(source) {
    const s = PDF_SOURCES[source];
    if (!s.doc) {
        if (s.preloadPromise) await s.preloadPromise;
        if (!s.doc) s.doc = await pdfjsLib.getDocument(s.url).promise;
    }
    currentSource = source;
    pdfDoc = s.doc;
    totalPages = pdfDoc.numPages;
    const titleEl = document.querySelector('#pdf-modal .pdf-modal-header h3');
    if (titleEl) titleEl.textContent = s.title;
    return pdfDoc;
}

async function openPdfModal(section, pageNum = null) {
    const modal   = document.getElementById('pdf-modal');
    const loading = document.getElementById('pdf-loading');
    modal.style.display = 'flex';
    loading.classList.add('show');

    try {
        await switchPdfSource('rulebook');
        updatePageInfo();
        if (pageNum)       { currentPage = pageNum; await renderPage(currentPage); }
        else if (section)  { await navigateToSection(section); }
        else               { await renderPage(currentPage); }
        loading.classList.remove('show');
        updateControls();
    } catch (err) {
        console.error('Error loading PDF:', err);
        loading.textContent = 'Error loading PDF. Please try again.';
    }
}

async function openPerrySezModal() {
    const modal   = document.getElementById('pdf-modal');
    const loading = document.getElementById('pdf-loading');
    modal.style.display = 'flex';
    loading.classList.add('show');

    try {
        await switchPdfSource('perrySez');
        currentPage = 1;
        await renderPage(currentPage);
        loading.classList.remove('show');
        updateControls();
    } catch (err) {
        console.error('Error loading Perry Sez PDF:', err);
        loading.textContent = 'Error loading PDF. Please try again.';
    }
}

function closePdfModal() {
    document.getElementById('pdf-modal').style.display = 'none';
}

async function navigateToSection(section) {
    if (!pdfDoc) return;
    if (sectionPageMap && sectionPageMap[section]) {
        currentPage = sectionPageMap[section];
        await renderPage(currentPage);
        await scrollToSectionOnPage(section);
        return;
    }
    // Fallback: text search
    const pattern = new RegExp(`\\b${section.replace(/\./g, '\\.')}\\b`);
    const maxPage = Math.min(totalPages, 700);
    for (let p = 43; p <= maxPage; p++) {
        const page = await pdfDoc.getPage(p);
        const text = (await page.getTextContent()).items.map(i => i.str).join(' ');
        if (pattern.test(text)) { currentPage = p; await renderPage(currentPage); await scrollToSectionOnPage(section); return; }
    }
    currentPage = 1;
    await renderPage(currentPage);
}

async function scrollToSectionOnPage(section) {
    const page = await pdfDoc.getPage(currentPage);
    const textContent = await page.getTextContent();
    const viewport = page.getViewport({ scale });
    const bareSection = section.replace(/^[A-Z]/, '');
    const pattern = new RegExp(`\\b${bareSection.replace(/\./g, '\\.')}\\b`);
    for (const item of textContent.items) {
        if (pattern.test(item.str)) {
            const pageHeight = page.getViewport({ scale: 1 }).height;
            const frac = 1 - (item.transform[5] / pageHeight);
            const canvas = document.getElementById('pdf-canvas');
            const scrollY = frac * canvas.getBoundingClientRect().height;
            document.getElementById('pdf-container').scrollTop = Math.max(0, scrollY - 20);
            return;
        }
    }
    document.getElementById('pdf-container').scrollTop = 0;
}

async function renderPage(pageNum) {
    if (!pdfDoc) return;
    const page     = await pdfDoc.getPage(pageNum);
    const viewport = page.getViewport({ scale });
    const canvas   = document.getElementById('pdf-canvas');
    const ctx      = canvas.getContext('2d');
    const out      = devicePixelRatio;
    canvas.height  = Math.floor(viewport.height * out);
    canvas.width   = Math.floor(viewport.width * out);
    canvas.style.height = Math.floor(viewport.height) + 'px';
    canvas.style.width  = Math.floor(viewport.width) + 'px';
    ctx.scale(out, out);
    await page.render({ canvasContext: ctx, viewport }).promise;
    updatePageInfo();
}

function updatePageInfo() {
    document.getElementById('pdf-page-info').textContent = `Page ${currentPage} of ${totalPages}`;
    document.getElementById('pdf-zoom-level').textContent = `${Math.round(scale * 100)}%`;
}

function updateControls() {
    document.getElementById('pdf-prev').disabled = currentPage <= 1;
    document.getElementById('pdf-next').disabled = currentPage >= totalPages;
}

function pdfPrevPage() { if (currentPage > 1)          { currentPage--; renderPage(currentPage); updateControls(); } }
function pdfNextPage() { if (currentPage < totalPages)  { currentPage++; renderPage(currentPage); updateControls(); } }
function pdfZoomIn()   { scale += 0.5; renderPage(currentPage); updatePageInfo(); }
function pdfZoomOut()  { if (scale > 1.0) { scale -= 0.5; renderPage(currentPage); updatePageInfo(); } }

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modal = document.getElementById('pdf-modal');
        if (modal && modal.style.display !== 'none') closePdfModal();
    }
});

// ============================================================
// Image attachment (clipboard paste)
// ============================================================

let pendingImages = [];                        // array of data URLs (most recent paste appended)
const IMAGE_MAX_DIM = 2048;
const IMAGE_JPEG_QUALITY = 0.85;
const MAX_PENDING_IMAGES = 3;

function getPendingImages() { return pendingImages.slice(); }

async function handleImagePaste(e) {
    if (!e.clipboardData) return;
    const item = [...e.clipboardData.items].find(i => i.type && i.type.startsWith('image/'));
    if (!item) return;
    e.preventDefault();
    if (pendingImages.length >= MAX_PENDING_IMAGES) {
        console.warn(`Max ${MAX_PENDING_IMAGES} images per message; ignoring paste.`);
        return;
    }
    const blob = item.getAsFile();
    if (!blob) return;
    try {
        const dataUrl = await resizeImageToDataUrl(blob, IMAGE_MAX_DIM, IMAGE_JPEG_QUALITY);
        addPendingImage(dataUrl);
    } catch (err) {
        console.error('Image paste failed:', err);
    }
}

async function resizeImageToDataUrl(blob, maxDim, quality) {
    const img = await createImageBitmap(blob);
    const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
    const w = Math.max(1, Math.round(img.width * scale));
    const h = Math.max(1, Math.round(img.height * scale));
    let outBlob;
    if (typeof OffscreenCanvas !== 'undefined') {
        const canvas = new OffscreenCanvas(w, h);
        canvas.getContext('2d').drawImage(img, 0, 0, w, h);
        outBlob = await canvas.convertToBlob({ type: 'image/jpeg', quality });
    } else {
        const canvas = document.createElement('canvas');
        canvas.width = w; canvas.height = h;
        canvas.getContext('2d').drawImage(img, 0, 0, w, h);
        outBlob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', quality));
    }
    return await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(outBlob);
    });
}

function addPendingImage(dataUrl) {
    pendingImages.push(dataUrl);
    renderPendingImages();
    // Image queries get force-overridden to gpt-5.4 server-side; mirror that in the
    // selector so the user sees the model that will actually run.
    const sel = document.getElementById('model-selector');
    if (sel && sel.value !== 'gpt-5.4') {
        const has54 = [...sel.options].some(o => o.value === 'gpt-5.4');
        if (has54) sel.value = 'gpt-5.4';
    }
}

function clearPendingImages() {
    pendingImages = [];
    renderPendingImages();
}

function removePendingImageAt(idx) {
    if (idx < 0 || idx >= pendingImages.length) return;
    pendingImages.splice(idx, 1);
    renderPendingImages();
}

function renderPendingImages() {
    const preview = document.getElementById('image-preview');
    if (!preview) return;
    if (pendingImages.length === 0 && !pendingVsav) {
        preview.style.display = 'none';
        preview.innerHTML = '';
        return;
    }
    preview.style.display = 'flex';
    // Build chips: thumb + remove (×) per image, plus a single label for the count
    preview.innerHTML = '';
    pendingImages.forEach((url, idx) => {
        const item = document.createElement('div');
        item.className = 'image-preview-item';
        const img = document.createElement('img');
        img.src = url;
        img.alt = `Pasted image ${idx + 1}`;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'image-preview-remove';
        btn.title = 'Remove image';
        btn.textContent = '×';
        btn.addEventListener('click', () => removePendingImageAt(idx));
        item.appendChild(img);
        item.appendChild(btn);
        preview.appendChild(item);
    });
    if (pendingVsav) {
        const chip = document.createElement('span');
        chip.className = 'vsav-preview-chip';
        chip.style.cssText =
            'display:inline-flex;align-items:center;gap:6px;padding:2px 8px;' +
            'border:1px solid currentColor;border-radius:12px;font-size:12px;opacity:.85;';
        const name = document.createElement('span');
        name.textContent = `🗺 ${pendingVsav.name}`;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.title = 'Remove VASL save';
        btn.textContent = '×';
        btn.style.cssText =
            'background:none;border:none;cursor:pointer;font:inherit;color:inherit;padding:0;';
        btn.addEventListener('click', clearPendingVsav);
        chip.appendChild(name);
        chip.appendChild(btn);
        preview.appendChild(chip);
    }
    if (pendingImages.length > 0) {
        const label = document.createElement('span');
        label.className = 'image-preview-label';
        label.textContent = pendingImages.length === 1
            ? '1 image attached'
            : `${pendingImages.length} images attached`;
        preview.appendChild(label);
    }
}

function bindImagePasteHandler() {
    const input = document.getElementById('chat-message-input');
    if (input) input.addEventListener('paste', handleImagePaste);
    // Clear any stale state from the static markup; renderPendingImages takes over.
    pendingImages = [];
    renderPendingImages();
}

// ============================================================
// VASL .vsav save attachment (file picker)
// ============================================================

let pendingVsav = null;                        // { name, dataUrl } or null
const VSAV_MAX_BYTES = 2 * 1024 * 1024;        // mirrors server-side cap

function getPendingVsav() { return pendingVsav; }

function clearPendingVsav() {
    pendingVsav = null;
    renderPendingImages();
}

function bindVsavAttachHandler() {
    const btn = document.getElementById('vsav-attach-btn');
    const fileInput = document.getElementById('vsav-file-input');
    if (!btn || !fileInput) return;
    btn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
        const file = fileInput.files && fileInput.files[0];
        fileInput.value = '';   // allow re-picking the same file later
        if (!file) return;
        if (!file.name.toLowerCase().endsWith('.vsav')) {
            alert('Please choose a VASL .vsav save file.');
            return;
        }
        if (file.size > VSAV_MAX_BYTES) {
            alert('.vsav file exceeds the 2 MB limit.');
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            pendingVsav = { name: file.name, dataUrl: reader.result };
            renderPendingImages();
            // Board-state questions usually want the IFT calculator, so default
            // Tools on — only the authed page has the toggle, and only gpt-5.4
            // has function calling wired up. The user can still uncheck it.
            const toolsCheckbox = document.getElementById('agentic-toggle');
            const modelSel = document.getElementById('model-selector');
            if (toolsCheckbox && modelSel && modelSel.value === 'gpt-5.4') {
                toolsCheckbox.checked = true;
            }
        };
        reader.onerror = () => console.error('Failed to read .vsav file');
        reader.readAsDataURL(file);
    });
    pendingVsav = null;
}

// Back-compat shims for callers that still use the singular API.
function getPendingImage() { return pendingImages[0] || null; }
function clearPendingImage() { clearPendingImages(); }
function setPendingImage(dataUrl) {
    pendingImages = [];
    addPendingImage(dataUrl);
}

// ============================================================
// Answer layout: prominent answer up top, de-emphasized details
// ============================================================
// The model is instructed to lead with "**Answer:** ..." and separate the
// supporting detail (steps, references) with a horizontal rule (---), which
// marked renders as <hr>. Split there: everything before the first <hr>
// stays prominent; everything after is wrapped in .msg-details (smaller,
// muted — styled in site-design-system.css). Fallback for answers without
// the rule (older messages, off-format models): if the first element is a
// paragraph starting with "Answer", de-emphasize what follows it.
function applyAnswerLayout(contentEl) {
    if (!contentEl || contentEl.querySelector('.msg-details')) return;
    const children = Array.from(contentEl.children);
    if (!children.length) return;

    let splitAt = children.findIndex(el => el.tagName === 'HR');
    let removeSplitEl = true;
    if (splitAt === -1) {
        const first = children[0];
        const lead = (first.textContent || '').trim();
        if (first.tagName === 'P' && /^answer\b/i.test(lead) && children.length > 1) {
            splitAt = 1;
            removeSplitEl = false;
        }
    }
    if (splitAt === -1 || splitAt >= children.length - (removeSplitEl ? 1 : 0)) return;

    const details = document.createElement('div');
    details.className = 'msg-details';
    const rest = children.slice(splitAt + (removeSplitEl ? 1 : 0));
    if (removeSplitEl) children[splitAt].remove();
    rest.forEach(el => details.appendChild(el));
    contentEl.appendChild(details);
}
