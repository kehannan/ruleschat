# Multimodal Plan — Board Screenshot Q&A

**Status (2026-05-02):** Phases 1–3 shipped. Plumbing works end-to-end. Open
issue is vision quality on VASL screenshots — see "Vision-quality issue"
section near the bottom for the next planned step (visual terrain legend).

## Goal
Let a user paste a screenshot from VASL into the chat input and ask a
question about it. The model reads the board state, then answers using
rulebook citations.

Scope for v1: clipboard paste only. Drag-drop and file-button can come
later — they share the downstream code.

## Core requirement

The model must do all three of the following **in a single agentic loop**:

1. Look at the user's image (the board screenshot).
2. Call the `file_search` tool against the rulebook vector store to retrieve
   the rule chunks that apply to what's in the image.
3. Reason jointly over the image AND the retrieved rule text to answer.

This is one Responses API call with multimodal input + tool use, not a
two-step "describe image, then RAG on description" pipeline. The latter
loses information (a description can't capture every spatial detail the
rule may hinge on, e.g., exact hex coordinates, counter ordering in a
stack, terrain printed on the map).

## Model

**Vision model: gpt-5.4** (locked in for v1). Confirmed: gpt-5.4 supports
both image input and the `file_search` built-in tool on the Responses API
in the same call.

- [ ] Pull the exact gpt-5.4 model ID via `GET /v1/models`; record it in
      `app/asl/client.py` (or wherever the model name is configured).
- [ ] Record pricing in this doc: per-1M input tokens, per-1M output tokens,
      per-image-token rate. Source: OpenAI pricing page.

## Image detail level

**`high` for v1.** ~2K image tokens per screenshot; needed to read counter
labels, hex coordinates, and stack ordering. `low` (~85 tokens, flat) is
not viable — VASL counters become unreadable. Revisit only if costs become
prohibitive, in which case the fallback is two-stage (`high` only on the
first vision pass that produces a textual scene description).

## Decisions locked in

- **History-replay policy:** image lives ONLY in the turn it was pasted.
  Follow-up turns see the model's prior text output (which must include an
  explicit board-state description, per the system prompt). User can
  re-paste if a follow-up needs the visuals again. Avoids paying image
  tokens on every subsequent response in a multi-turn conversation.
- **Single gpt-5.4 call** for vision + RAG + reasoning, not a two-stage
  pipeline. Two-stage would lose spatial detail (exact hex coords, stack
  ordering, counter alignment). Revisit only if cost becomes a problem.

## Phase 1 — Client-side paste + preview UI (no backend changes) ✅ shipped

Goal: ship the UX so paste + preview + remove works end-to-end before
touching the WebSocket protocol.

- [x] Add `paste` listener on the chat input in `static/js/chat-shared.js`.
      Detect `image/*` clipboard items, intercept, prevent default.
- [x] Implement `resizeImageToDataUrl(blob, maxDim=2048, quality=0.85)` using
      `OffscreenCanvas` (with `<canvas>` fallback) → JPEG data URL. Keeps
      payload < ~500 KB.
- [x] Add image-preview chip above the textarea in `templates/ruleschat.html`:
      thumbnail + label + remove button.
- [x] Track `pendingImage` state in `chat-shared.js`; expose
      `getPendingImage()` for Phase 2; `clearPendingImage()` clears state +
      hides chip.
- [x] Wire `bindImagePasteHandler()` into ruleschat init.
- [ ] Wire same handler into `templates/demo.html` (deferred — ruleschat is
      primary user; demo can be added later).
- [x] Manual browser test: VASL screenshot pastes, thumbnail + label
      render, × button clears, normal text paste still works.

## Phase 2 — WebSocket protocol + persistence ✅ shipped

Goal: image rides along with the user message; server persists it for the
conversation log.

- [x] Client send: when `pendingImage` is set, include `image` in the
      JSON payload. Plain-text path unchanged. Preview clears on send.
- [x] In `app/api/chat.py`, the existing JSON-parsing branch now extracts
      `image` (data URL) from the chat command.
- [x] Validate in `app/services/image_storage.py`: MIME in `{image/jpeg,
      image/png, image/webp}`; decoded size ≤ 5 MB; reject otherwise with a
      `type:"error"` WS frame.
- [x] `save_image_data_url(data_url, conversation_id)` writes to
      `data/uploads/{conv_id}/{uuid}.jpg|png|webp` and returns
      `"{conv_id}/{filename}"` (uuid-based filename, not msg_id, since the
      message hasn't been written yet at save time).
- [x] Added `image_path` column on `chat_messages` (idempotent ALTER on
      startup; SQLite, no migration tool in use).
- [x] Auth-gated retrieval route `GET /api/uploads/{conv_id}/{filename}` —
      verifies conversation ownership; rejects path traversal; serves via
      `FileResponse`. Not under `/static/`.
- [x] Added `data/uploads/` to `.gitignore`.
- [x] Smoke-tested: storage round-trip, GIF/non-data-URL/traversal all
      rejected; auth-gated route returns 401 unauth and 404 for missing.
- [ ] Manual browser test: paste image, send a message, verify (a) the
      thumbnail clears on send, (b) the image lands in
      `data/uploads/{conv_id}/`, (c) the chat_messages row has
      `image_path` populated, (d) `/api/uploads/{conv_id}/{filename}`
      serves it back when authed.

## Phase 3 — Multimodal + tool-using model call ✅ shipped

Goal: model sees the image, calls `file_search` against the rulebook, and
reasons over both — all in one agentic loop.

- [x] WS handler force-overrides model to gpt-5.4 when an image is
      attached (other models on the whitelist may not support vision).
- [x] Build user message as multipart (`input_text` + `input_image` blocks
      with `detail: "high"`) when image attached; text-only path unchanged
      otherwise. Implemented in `_build_multimodal_input` in
      `app/services/asl_service.py`; image is read from disk and inlined as
      a base64 data URL.
- [x] System prompt addendum (`VISION_INSTRUCTIONS_ADDENDUM`) appended to
      the base instructions only when an image is attached: requires the
      model to first describe the board, then call file_search, cite
      sections, refuse to guess unreadable counters, and never make a rule
      claim without a file_search citation.
- [ ] Smoke test in browser: paste a VASL screenshot, ask a rule question,
      confirm BOTH happen in the streamed event log — `file_search` tool
      call fires AND the response cites rule sections and references the
      board state.
- [ ] Verify image is counted in `usage.input_tokens_details.image_tokens`
      on the post-tool-call response (image must persist across iterations).
- [ ] 3–5 few-shot examples of board-screenshot Q&A added to the prompt.
- [ ] Decide failure-mode behavior: if the model declines to call
      file_search and answers from image alone, accept or force retry?
      Default: log low-confidence and surface "no rules cited" UI warning.

## Risks & mitigations specific to image + RAG joint reasoning

- **Risk: model answers from the image without calling file_search.**
  Mitigation: hard requirement in system prompt; eval set with questions
  whose answer is NOT inferable from the image alone (e.g., "is this fire
  group legal?" — needs the FG rules).
- **Risk: model calls file_search with a poor query** (e.g., "rules about
  this image"). Mitigation: prompt it to first articulate the rule topic in
  text, then file_search. If retrieval quality is poor, add a step to
  describe the situation in ASL terminology before searching.
- **Risk: image tokens billed multiple times in a multi-turn conversation.**
  Mitigation: image lives in only the turn it was pasted; follow-up turns
  see the model's prior text output. Confirm with `usage` field telemetry.
- **Risk: model misreads counters, then retrieves correct rules but applies
  them to wrong board state.** Mitigation: prompt requires the model to
  describe the board state explicitly *before* answering — user can spot
  misreads and correct. Surface the description prominently in the UI.

## Phase 4 — Guardrails

- [ ] Per-user daily image-message cap (e.g. 50/day). Count in DB.
- [ ] Log `image_attached: true` and per-message token cost on the message
      row for cost auditing.
- [ ] Alert (email or admin-page banner) if daily image spend > $X.
- [ ] 30-day retention cron for `data/uploads/`.

## Phase 5 — Evals

- [ ] Curate 20–30 VASL screenshots with known-correct answers (LOS,
      stacking, opportunity fire eligibility, rally-check legality).
- [ ] Wire into existing eval suite. LLM-as-judge scores grounding +
      correctness.
- [ ] Manual spot-check first ~50 prod queries with images; tag failure
      modes (misread counter, wrong LOS, hallucinated rule).

## Phase 6 — Admin / debugging

- [ ] Show attached image inline in admin logs viewer (reuse existing
      click-to-expand cell pattern).
- [ ] Add a "rerun without image" button for failed cases to isolate vision
      vs. reasoning failures.

## Sequencing & rough estimate

| Phase | Effort | Risk |
|-------|--------|------|
| 1. Paste + preview UI | 0.5 day | Low |
| 2. WS + persistence | 0.5 day | Low |
| 3. Multimodal model call | 0.5 day | Med — confirm SDK input format |
| 4. Guardrails | 0.5 day | Low |
| 5. Evals | 1–2 days | Low (slow, manual) |
| 6. Admin viewer | 0.5 day | Low |

## Suggested first PR

Phase 1 only — pure client-side paste-and-preview. Safe to ship behind a
feature flag; lets the UX get tested before any model cost is incurred.

## Vision-quality issue (open — picks up here next session)

**What we observed:** First end-to-end test with a real VASL screenshot
(3-3-7 firing on a 4-4-7) — the model correctly identified the units, the
hex labels, called `file_search`, and cited rule sections. But it
**misread the target hex** as "containing a wreck" when the image actually
shows a **building** (gray polygon with internal grid lines for floors). It
also missed orchards on the LOS path that were clearly visible.

**Confirmed not a compression issue.** The saved image was
1058 × 494, well under our 2048 cap (no resize happened). At that size and
JPEG q=0.85, compression is essentially lossless on cartoon-style VASL
graphics. Inspecting the saved file in
`data/uploads/{conv_id}/{uuid}.jpg` showed the building outline clearly.

**Likely root cause:** The model has weak priors on VASL conventions — it
defaulted "gray rectangular structure" → "wreck/AFV hulk" instead of
"building." A second human reviewer made the same mistake, suggesting it's
a legibility/disambiguation problem, not a quality-of-attention problem.

**Next step (agreed direction): visual terrain legend.**

- [ ] Build a single legend PNG: grid of ~12–16 cells, each cell = (VASL
      hex crop, terrain name, TEM, LOS effect). Coverage: building,
      multi-hex building, woods, orchard, brush, grain, wall, hedge, road,
      wreck, blaze, smoke, water, slope, foxhole.
- [ ] Crop hexes from a real VASL render (NOT eASLRB Chapter B) so the
      symbol art matches what users will paste.
- [ ] Wire as a second `input_image` block in `_build_multimodal_input`:
      legend first, user board second.
- [ ] Update `VISION_INSTRUCTIONS_ADDENDUM` to instruct the model to do
      visual matching against the legend rather than rely on priors.
- [ ] Verify image caching behavior on gpt-5.4 — if the legend caches,
      marginal cost per question is just the user's board. If not, ~2K
      extra image tokens per turn.
- [ ] Re-run on the same building/orchard screenshot that just failed; if
      both terrain types now identify correctly, we know the approach
      works.

**Smaller proof-of-concept variant:** crop ~6 hexes (building, wreck,
orchard, woods, wall, road) from existing screenshots in `data/uploads/`,
assemble a quick-and-dirty legend, test on the failing question. If
positive, invest in a polished 16-cell legend.

**Other ideas considered, deferred:**

- *Per-hex enumeration in the prompt:* force the model to list every hex
  on the LOS path with terrain type and counters before answering. Cheap
  to try, may help even without the legend.
- *Few-shot Q&A examples:* 3–5 board screenshots with correct reads in the
  system prompt. Higher token cost; legend is more compact.
- *Higher resolution input:* original screenshot was already ≤ 2048; not
  the bottleneck for this case.

## Deferred

- Wire image paste handler into `templates/demo.html`.
- Drag-drop and file-picker upload paths (share Phase 2+ code).
- Rulebook-figure reasoning (model looks at eASLRB illustrations) — becomes
  a natural extension once the multimodal pipeline is in place; the figure
  is just another image input.
- Physical-board photo support (vs. clean VASL screenshots).
