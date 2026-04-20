// Shared utilities for ruleschat and demo chat pages.

// ============================================================
// Model pricing
// ============================================================

const MODEL_PRICING = {
    'gpt-5-mini':   { input: 0.25, output: 1.00 },
    'gpt-4.1-mini': { input: 0.40, output: 1.60 },
    'gpt-5.4-mini': { input: 0.25, output: 2.00 },
    'gpt-5.4':      { input: 3.00, output: 15.00 },
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
    const latencyDisplay = document.getElementById('latency-display');
    if (!latencyDisplay) return;

    const fileSearchMs  = latencyData.file_search_time_ms || 0;
    const ttftMs        = latencyData.ttft_ms || 0;
    const totalMs       = latencyData.total_time_ms || 0;
    const inputTokens   = latencyData.input_tokens || 0;
    const outputTokens  = latencyData.output_tokens || 0;

    const formatTime = (ms) => ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
    const formatTokens = (n) => n >= 1000 ? `${(n / 1000).toFixed(1)}K` : `${n}`;
    const formatCost = (d) => {
        if (d < 0.001) return `$${(d * 1000).toFixed(2)}m`;
        if (d < 0.01)  return `$${d.toFixed(4)}`;
        return `$${d.toFixed(3)}`;
    };

    const parts = [];
    if (fileSearchMs > 0) parts.push(`RAG: ${formatTime(fileSearchMs)}`);
    if (ttftMs > 0)       parts.push(`TTFT: ${formatTime(ttftMs)}`);
    if (totalMs > 0)      parts.push(`Total: ${formatTime(totalMs)}`);
    if (inputTokens > 0 || outputTokens > 0) {
        const pricing   = getModelPricing();
        const totalCost = (inputTokens / 1_000_000) * pricing.input +
                          (outputTokens / 1_000_000) * pricing.output;
        parts.push(`${formatTokens(inputTokens)} in / ${formatTokens(outputTokens)} out • Cost: ${formatCost(totalCost)}`);
    }

    if (parts.length > 0) {
        latencyDisplay.textContent = parts.join(' • ');
        latencyDisplay.style.display = 'block';
    } else {
        latencyDisplay.style.display = 'none';
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
            link.className = 'section-link';

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
