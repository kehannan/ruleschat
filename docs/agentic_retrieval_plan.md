# Agentic Retrieval Plan — `get_section`, deterministic cite-check, multi-hop search

**Status:** proposed · 2026-07-09 (rev 2: production model is gpt-5.4 or cheaper)
**Goal:** close the recall-failure gap on the production model tier by grounding
every cited rule section in its exact text — via deterministic code first, model
agency second — then measure against a same-model baseline.

---

## 1. Why (evidence)

**Production model constraint:** fable-5 is too expensive for regular use; the
service runs gpt-5.4 or cheaper. That makes the baseline and the failure profile
worse than the fable numbers, and it changes what mechanisms we can rely on.

Reviewed evals (human-reviewed, judge gpt-5-mini):

| run | model | overall | calc | recall |
|---|---|---|---|---|
| easy-AB v1.1 | gpt-5.4 (calc auto-routed to tools; recall plain RAG) | **73/85** | 28/35 | 45/50 |
| easy-AB v1.1 | claude-fable-5 | 84/85 | 35/35 | 49/50 |
| med-ABCD v1.1 | claude-fable-5 | 56/60 | 15/15 | 41/45 |
| med-ABCD v1.1 | gpt-5.4 | **not yet run** — required baseline (T8 Run A) | | |

Two failure modes on the production tier:

1. **Recall misses (both models, worse on gpt-5.4):** the model states the headline
   rule but misses a qualifying clause in a cross-referenced section (fable med
   examples: A9.73 "could fire it", A12.14 out-of-LOS, C13.31 second-PF, D5.53
   once-per-MPh). Single-shot retrieval grabs the main section; the exception in a
   linked section never enters context.
2. **Garbage-in on calc (gpt-5.4 only):** 7 easy-calc failures happened *with the
   calculators force-routed* — the model passed wrong TEM/DRM/FP inputs it "knew"
   from memory instead of looking them up. The tools computed correctly on wrong
   inputs.

Both modes have the same root cause — the model asserting rule content it never
read verbatim — and the same fix: **fetch the exact text of every section that
matters** (deterministic lookup, not lossy vector search).

**Design consequence of the model tier:** gpt-5.4 demonstrably does not follow
tool-use protocols voluntarily (the auto-router + forced `tool_choice` in
`tool_router.py` exists precisely because it "rarely calls these tools unprompted").
So a prompt-only "fetch before you cite" protocol is not a reliability mechanism on
this tier. The backbone must be **deterministic and code-driven**: after the model
drafts an answer, *code* extracts the cited section IDs, fetches their exact text,
and forces one grounded revision turn. Model-initiated `get_section` calls remain
available mid-loop as a secondary assist (they cost nothing when unused).

## 2. What exists today (orientation for agents)

- `app/services/asl_service.py` — `ASLService.get_answer()` with three agentic loops:
  `_handle_agentic_response` (OpenAI non-streaming), `_handle_agentic_streaming_response`
  (OpenAI streaming), `_openrouter_agentic_answer` (OpenRouter Chat Completions).
  All dispatch tools through `execute_tool(name, args, context=...)`.
- `app/asl/tools.py` — tool functions + `TOOL_SCHEMAS` (Responses API format).
  `TOOL_SCHEMAS_CHAT` is **auto-derived** via `to_chat_completions_tools()`; add a
  schema once and both formats stay in lock-step. `execute_tool()` is the dispatcher.
- `app/asl/retrieval.py` — `retrieve_chunks()` client-side vector-store search
  (used by the OpenRouter path; the OpenAI path uses the hosted `file_search` tool).
- `app/asl/policy.py` — `build_instructions()`; per-mode addenda live as constants in
  `asl_service.py` (`VISION_INSTRUCTIONS_ADDENDUM`, `VSAV_INSTRUCTIONS_ADDENDUM`).
- `app/asl/tool_router.py` — `classify_tool()` routes calc questions to
  `ift_attack`/`cc_attack`, else returns `None` (which currently means *no tools at
  all* on the OpenRouter path).
- `static/rulebook/section_pages.json` — **2,781 section IDs → PDF page** (e.g.
  `"A12.14": 87`). The rulebook PDFs sit next to it (gitignored).
- Eval runner lives in the sibling repo: `../ruleschat-evals/evals/src/scripts/asl_evals.py`
  (supports `--agentic`, `--force-tool`, `--auto-route-tools`).

## 3. Design decisions (fixed — do not relitigate per-task)

1. **Section text store:** `data/rulebook/sections.json`, built offline by a script
   from the eASLRB PDF. **Gitignored** (copyrighted content, same policy as the PDFs).
   Shape (the contract every task codes against):

   ```json
   {
     "meta": {"source_pdf": "eASLRB_v3_14_INHERIT_ZOOM.pdf", "built": "...", "sections": 2781},
     "sections": {
       "A12.14": {"text": "full rule text ...", "page": 87},
       "A12.141": {"text": "...", "page": 87}
     }
   }
   ```

   Keys match `section_pages.json` exactly (uppercase letter prefix, dotted numbers,
   no trailing period).

2. **Two new function tools**, split into a named group so they can be exposed
   independently of the calculators:
   - `get_section(section, include_subsections=False)` — deterministic lookup.
   - `search_rules(query, max_results=8)` — follow-up vector search. Exposed **only on
     the OpenRouter path** (the OpenAI path already has hosted `file_search` available
     mid-loop; a duplicate search tool there just confuses tool choice).
   - In `tools.py`, define `LOOKUP_TOOL_NAMES = {"get_section", "search_rules"}` and
     `CALC_TOOL_NAMES = {...existing five...}` so callers can filter `TOOL_SCHEMAS`.

3. **Router behavior change:** when the tool router classifies a question as `none`
   (no calculator), lookup tools are **still exposed** — `none` stops meaning
   "plain RAG, no tools".

4. **Primary mechanism — deterministic cite-check pass (code-driven, forced):**
   after the model produces its draft answer, *code* (not the model) does:
   1. regex-extract cited section IDs from the draft (pattern `[A-Z]{1,2}\d+(\.\d+)*`,
      validated against `section_pages.json` keys to kill false positives);
   2. fetch each via `rules_lookup.get_section` (plus each section ID that appears
      *inside* the fetched texts — one level of cross-reference expansion, capped);
   3. run **one** forced revision turn: draft answer + exact section texts +
      *"revise if any fetched text contains a qualifier, exception (EXC:), or NA
      clause that contradicts the draft; otherwise return the draft unchanged"*.

   This does not depend on the model choosing to call tools, works identically on
   the OpenAI and OpenRouter paths, and costs one extra model turn only. It is the
   reliability backbone for gpt-5.4-and-cheaper.

5. **Secondary mechanism — model-initiated lookup tools:** `get_section` (+
   `search_rules` on OpenRouter, per §3.2) stay exposed in the agentic loop, with a
   short instructions addendum encouraging fetch-before-cite. On weaker models
   compliance will be spotty; that is acceptable because the cite-check pass (§3.4)
   catches what the model didn't fetch. Do not build reliability assumptions on
   this mechanism.

6. **Calc garbage-in mitigation** rides on §3.4: the revision turn also re-checks
   the *inputs* the model quoted for calculator calls (TEM/DRM values must appear in
   a fetched section text). No separate mechanism.

7. **`max_iterations` goes from 5 → 8** in all three loops when lookup tools are
   exposed (multi-hop needs turns; calculators alone keep the old budget).

8. **The Q&A/errata doc is in scope** (`static/rulebook/ASL-QA-v31.pdf`, 207 pp;
   the copy in `../ruleschat-evals/rulebook/` is byte-identical — always read the
   ruleschat one). Entries are keyed by section ID at line start
   (`A7.51 & D6.64 <question> ... A. <answer> [source]`), so they parse into a
   deterministic `section → Q&A entries` map. Contract
   (`data/rulebook/qa_entries.json`, gitignored like the rulebook text):

   ```json
   {
     "meta": {"source_pdf": "ASL-QA-v31.pdf", "built": "...", "entries": 0},
     "by_section": {
       "A7.51": [{"sections": ["A7.51", "D6.64"], "text": "Q ... A. ... [BRTG; Mw24H]",
                   "kind": "official-qa|unofficial-qa|errata", "page": 5}]
     }
   }
   ```

   An entry keyed to N sections appears under all N keys. `get_section` returns
   Q&A entries alongside rule text; the cite-check pass includes them in the
   revision context. Scenario-specific Q&A (keyed by scenario name, not section
   ID) is skipped in v1.

9. **Fixture-first testing:** unit tests never read `data/rulebook/sections.json`
   (it's gitignored and machine-local). A small committed fixture with **invented**
   rule text (not copied from the rulebook) stands in:
   `tests/fixtures/rulebook_sections_fixture.json`.

## 4. Tasks

> **STATUS 2026-07-09: T1, T1b, T2, T3, T4, T5, T6, T7 all built and tested**
> (182 unit tests green; new suites: test_rules_lookup.py 12, test_cite_check.py 11,
> test_agentic_tools.py grew to 12). Key artifacts: `scripts/extract_qa_entries.py`
> (2,378 Q&A/errata entries, 989 sections covered), `app/asl/rules_lookup.py`,
> `app/asl/cite_check.py`, `get_section`/`search_rules` registered in
> `app/asl/tools.py`, service wiring incl. `use_cite_check` on
> `ASLService.get_answer`, and `--cite-check` in the ruleschat-evals runner.
> **Remaining: T8 (eval runs)** — needs API keys + spend, run order per the T8
> section (Run A baseline first: gpt-5.4 med-ABCD `--agentic --auto-route-tools`).

Each task is atomic: one agent, one branch/worktree, no shared files with tasks in
the same lane except where the dependency graph says otherwise.

```
T1 (extract) ✅   T1b (Q&A extract)   T4 (search_rules ctx)
   \                 /                  |
    T2 (lookup lib) —                   |
       \                                |
        T3 (register tools)
             |
    ┌────────┼──────────────────┐
   T5 (instructions)  T6 (wiring/router)  T7 (cite-check pass — CORE)
    └────────┴──────────────────┘
             |
            T8 (eval)
```

Parallel lanes at the start: **T1**, **T2** (against the fixture), **T4** can all run
simultaneously. T5/T6/T7 in parallel after T3 (T7 only needs T2, actually — it can
start in wave 2). **T7 is the highest-value task on the gpt-5.4 tier**; if you run
agents serially, order it right after T2.

---

### T1 — Rulebook section extraction script — ✅ DONE 2026-07-09

> Built as `scripts/extract_rulebook_sections.py`. The heading finder was then
> turned on the ENTIRE index (all 2,781 entries, not a sample): **2,763 (99.4%)
> verified — heading mechanically confirmed on the mapped page**. 53 entries
> had off-by-one pages (consecutive clusters — systematic builder drift, not
> hallucination); those were **patched in `static/rulebook/section_pages.json`**
> (also fixes the PDF viewer for those sections). 18 remain unresolved: at
> least one phantom (`A7.30` — no such printed section) plus headings mangled
> in extraction (e.g. `F7.1` prose-style, `W10.44` interleaved); parent
> fallback covers all of them. Coverage of the built store: **2,763/2,781 =
> 99.4%** (bar was 95%). Extraction is word-based column reconstruction (NOT
> bbox cropping — the column gutter varies per page); headings match WITHOUT
> the chapter letter (the PDF prints `6.21`, not `A6.21`); `#KIA:`/em-dash/
> `[EXC:`/digit-leading titles and glued headings handled. Spot-checked clean
> on A12.14, A9.73, C13.31, A21.13, B27.1, A7.302, B28.1, B34.4. Note: the
> med-eval citation "D5.53" does not exist in the rulebook (eval-file
> mis-citation — chapter D jumps 5.5 → 5.6).

**Deliverable:** `scripts/extract_rulebook_sections.py` + generated
`data/rulebook/sections.json` + `.gitignore` entry for `data/rulebook/`.

- Input: `static/rulebook/eASLRB_v3_14_INHERIT_ZOOM.pdf` and
  `static/rulebook/section_pages.json` (2,781 section→page anchors).
- Extract per-page text (pypdf or pdfplumber; add to `requirements.txt` as a
  dev/script-only dependency if not present). For each section ID, slice text from
  its own heading match to the next section's heading (section IDs in the eASLRB
  appear as bold headers like `A12.14`; match with a regex anchored to the known
  next key in `section_pages.json` page order). A section whose heading isn't found
  gets `"text": null` and is counted as a miss.
- Print a coverage report: total sections, extracted, misses (list the first 50 miss
  IDs). **Acceptance: ≥95% of the 2,781 sections extract non-empty text**, and a
  spot-check flag `--show A12.14` prints one section for eyeballing.
- Do NOT commit `sections.json` or any rulebook text. Commit the script, the
  gitignore entry, and the coverage numbers in the PR description only.

**Depends on:** nothing. **Files touched:** new script, `.gitignore`, possibly `requirements.txt`.

### T1b — Q&A/errata entry extraction

**Deliverable:** `scripts/extract_qa_entries.py` + generated
`data/rulebook/qa_entries.json` (gitignored — same policy as the rulebook text).

- Input: `static/rulebook/ASL-QA-v31.pdf` (207 pages, two-column — REUSE
  `page_column_text` from `scripts/extract_rulebook_sections.py`, which already
  solves the variable-gutter column problem; import it, don't copy it).
- An entry starts at a line-start section-ID token (`A7.51`, optionally
  `& D6.64`-joined) **validated against `section_pages.json` keys** — that
  validation is what kills false positives — and runs until the next entry start.
  Track which part of the doc the page belongs to (Official Q&A / Unofficial Q&A /
  errata chapters) from the part headers ("Official Q&A: Rules", "Unofficial
  Q&A", chapter errata sections; the table of contents on pp. 1–2 gives the page
  ranges) and stamp it as `kind`.
- Skip: the table of contents, the introduction, and scenario-keyed Q&A (entries
  whose key is a scenario name, not a section ID — they simply won't match the
  ID validation).
- Pure pointer entries ("A15.2 See A7.302, A10.31 …") are kept — their text tells
  the model where to look next.
- Emit the §3.8 contract; entries keyed to multiple sections are duplicated under
  each key. Coverage report: total entries parsed, per-kind counts, entries
  dropped for failing ID validation (print the first 30 for eyeballing).
- Acceptance: ≥500 entries parsed (the doc has thousands; 500 is the sanity
  floor), spot-check `--show A7.53` prints a clean single entry, and no entry
  text longer than ~2,500 chars (longer = boundary detection failed; report them).

**Depends on:** nothing (parallel with T1/T2/T4). **Files touched:** new script,
new gitignored output. The T2 fixture gains a small
`tests/fixtures/qa_entries_fixture.json` with invented entries.

### T2 — Section lookup library

**Deliverable:** `app/asl/rules_lookup.py` + `tests/test_rules_lookup.py` +
`tests/fixtures/rulebook_sections_fixture.json` (~8 fake sections incl. a parent/child
chain like `Z1`, `Z1.1`, `Z1.11`, with **invented** text).

- Public surface:
  ```python
  load_sections(path: str | None = None, qa_path: str | None = None) -> None
      # lazy, module-level cache; defaults data/rulebook/sections.json + qa_entries.json
  get_section(section: str, include_subsections: bool = False, include_qa: bool = True) -> dict
  ```
- `get_section` returns `{"section", "text", "page", "subsections": [...]?, "qa": [...]?}`
  on hit. `qa` holds the §3.8 entries for that section (cap: 5 entries / 4,000
  chars, `"qa_truncated": true` beyond). A missing/absent `qa_entries.json`
  degrades to `qa: []` — never an error (the store is optional).
- Normalization: strip whitespace and trailing periods, uppercase the letter prefix
  (`a12.14` → `A12.14`).
- Miss behavior (critical for model ergonomics): walk up the dotted hierarchy
  (`A12.147` → `A12.14` → `A12.1` → `A12`) and return the nearest existing ancestor
  with `"note": "requested A12.147 not found; returning parent A12.14"`. If no
  ancestor exists, return `{"error": "...", "did_you_mean": [...]}` with up to 5
  prefix-near keys.
- `include_subsections=True` appends all direct children (one dotted level deeper),
  capped at 4,000 chars total with a `"truncated": true` flag.
- Missing/absent `sections.json` (e.g., fresh checkout) → every call returns a clean
  `{"error": "section database not built; run scripts/extract_rulebook_sections.py"}`,
  never an exception.
- Tests run entirely against the fixture (pass `path=` to `load_sections`).

**Depends on:** the §3.1 JSON contract only — runs in parallel with T1.
**Files touched:** new module, new test, new fixture.

### T3 — Register `get_section` + `search_rules` as function tools

**Deliverable:** edits to `app/asl/tools.py` + tests in `tests/test_agentic_tools.py`.

- Add tool functions:
  - `get_section(...)` — thin wrapper over `app.asl.rules_lookup.get_section`.
  - `search_rules(query, max_results=8)` — wraps `app.asl.retrieval.retrieve_chunks`
    over `self.config.all_vector_store_ids`. Since `tools.py` functions are stateless,
    accept the OpenAI client + store IDs via the existing `context` dict that
    `execute_tool(name, args, context=...)` already threads (same pattern as
    `vsav_state`): `context["retrieval_client"]`, `context["vector_store_ids"]`.
    Return `{"chunks": [{"text", "filename", "score"}, ...]}` capped at
    `max_results`, each chunk trimmed to 1,500 chars.
- Add both schemas to `TOOL_SCHEMAS` (Responses format; `TOOL_SCHEMAS_CHAT` derives
  automatically). Keep descriptions short and directive — the model reads them.
- Add `LOOKUP_TOOL_NAMES` / `CALC_TOOL_NAMES` constants and a helper
  `lookup_tool_schemas()` / `calc_tool_schemas()` returning the filtered lists (both
  formats: add a `chat=False` kwarg or two helpers — implementer's choice).
- Extend `execute_tool()` dispatch. Unknown-tool behavior unchanged.
- Tests: schema round-trip through `to_chat_completions_tools()`, dispatch of both new
  tools (lookup against the T2 fixture; `search_rules` with a stubbed client).

**Depends on:** T2 (imports it). **Files touched:** `app/asl/tools.py`,
`tests/test_agentic_tools.py`.

### T4 — `search_rules` context plumbing

**Deliverable:** edits to `app/services/asl_service.py` only.

- Everywhere `tool_context` is built (3 sites: `get_answer` OpenAI branch, OpenRouter
  agentic branch, and the streaming call path), include
  `"retrieval_client": self.retrieval_client` and
  `"vector_store_ids": self.config.all_vector_store_ids` alongside the existing
  `vsav_state`. Build the dict once in `get_answer` and thread it.
- No behavior change beyond the enriched context; existing tests must stay green.

**Depends on:** nothing (context keys are just dict entries; T3 consumes them).
Runs in parallel with T1/T2. **Files touched:** `app/services/asl_service.py`.

### T5 — Cite-verification instructions addendum (supporting, not load-bearing)

**Deliverable:** edits to `app/services/asl_service.py` (constant + wiring) only.

- Add `CITE_VERIFICATION_ADDENDUM` (see §3.5; write it in the same imperative style
  as `VSAV_INSTRUCTIONS_ADDENDUM`). Key clauses: fetch-before-cite via `get_section`;
  follow cross-references that could qualify the answer (exceptions, "unless",
  "EXC:", "NA" clauses); on the OpenRouter path use `search_rules` when you don't
  know the section number; when quoting a TEM/DRM value into a calculator call,
  fetch the section that states it first; sections returned with a `note`/`error`
  must be re-fetched or the uncertainty stated.
- Append it in `get_answer` whenever lookup tools will be exposed (gate on the same
  condition T6 introduces — coordinate on a single helper
  `_lookup_tools_enabled(...) -> bool` that T6 owns; if T6 hasn't landed, gate on
  `use_agentic` and let T6 refine).
- Keep it SHORT (≤ 12 lines). On gpt-5.4 compliance is best-effort; the cite-check
  pass (T7) is the reliability mechanism. Do not attempt to prompt-engineer
  reliability into this addendum.

**Depends on:** T3 merged (references tool names), coordinates with T6 on one helper.
**Files touched:** `app/services/asl_service.py`.

### T6 — Expose lookup tools in the three loops + router change

**Deliverable:** edits to `app/services/asl_service.py`, `app/asl/tool_router.py`
docstring, and `app/api/chat.py` if needed.

- OpenAI agentic path (`get_answer`, `use_agentic=True`): extend `tools` with
  `lookup_tool_schemas()` — but **not** `search_rules` (hosted `file_search` covers it;
  §3.2). So: `get_section` only.
- OpenRouter agentic path: expose `get_section` + `search_rules`. Router result
  `none` no longer sets `expose_tools = False`; instead it exposes **only** lookup
  tools (no calculators, no forced tool). Router results `ift_attack`/`cc_attack`
  keep today's behavior plus lookup tools in the tool list.
- Bump `max_iterations` 5 → 8 in all three loops when lookup tools are in the list.
- `tools_called` accounting already generalizes — verify `get_section` shows up in
  timing dicts on all three paths (extend an existing test or add one).
- Do NOT change the default `agentic` UI toggle behavior in `chat.py` in this task —
  the toggle still controls whether the loop runs at all. (Flipping the default is a
  product decision for after T8.)

**Depends on:** T3 + T4. **Files touched:** `app/services/asl_service.py`,
`app/asl/tool_router.py`, tests.

### T7 — Deterministic cite-check pass (CORE on the gpt-5.4 tier)

**Deliverable:** `app/asl/cite_check.py` + wiring in `app/services/asl_service.py` +
`tests/test_cite_check.py`.

- New module `app/asl/cite_check.py`, pure functions, no service state:
  ```python
  extract_section_ids(text: str, valid_ids: set[str]) -> list[str]
  build_cite_check_context(draft: str, max_sections: int = 12, max_chars: int = 16000) -> dict
  ```
  - `extract_section_ids`: regex `\b[A-Z]{1,2}\d+(?:\.\d+)*\b` over the draft,
    filtered against `valid_ids` (the keys of `section_pages.json` /
    `rules_lookup`) — the filter is what kills false positives like unit IDs.
  - `build_cite_check_context`: fetch each cited ID via `rules_lookup.get_section`
    (with `include_qa=True` — official Q&A/errata for a cited section goes into
    the revision context; it is the highest-signal "qualifier that flips the
    answer" material); then extract section IDs *inside* those fetched texts and
    fetch those too (one level only). Dedupe, apply the caps (drop
    lowest-priority = deepest cross-references first, then Q&A beyond the
    per-section cap), return `{"sections": {id: text}, "dropped": [...],
    "missing": [...]}`.
- Service wiring: add `use_cite_check: bool = False` to `get_answer`. When on and
  the draft cites ≥1 valid section, run **one** revision turn (same model, same
  path — Responses API with `previous_response_id` on the OpenAI side, one more
  Chat Completions turn on OpenRouter):
  *"Below are the exact texts of the sections your answer cites (plus sections they
  cross-reference). Revise your answer ONLY if a fetched text contains a qualifier,
  exception (EXC:), or NA clause that contradicts it, or if a numeric value you used
  (TEM, DRM, FP) disagrees with the fetched text. Otherwise return the answer
  unchanged. Keep all citations."*
  If the draft cites no valid sections, skip the pass (and log — a citation-free
  answer is itself a signal).
- Non-streaming first (eval harness + the `stream=False` path). Streaming-UX wiring
  is deliberately out of scope until T8 shows the accuracy gain justifies the
  latency (it delays the visible answer by one model turn).
- Also thread a `use_cite_check` CLI flag through
  `../ruleschat-evals/evals/src/scripts/asl_evals.py` → `eval_kwargs` (mirror how
  `--agentic` is passed).
- Tests: extraction against tricky drafts (unit designations, hex IDs like `57-H8`,
  lowercase refs), cross-reference expansion + caps against the T2 fixture, and the
  skip path.

**Depends on:** T2 only (uses `rules_lookup`; independent of T3–T6 — can start in
wave 2). **Files touched:** new module, `app/services/asl_service.py`,
`asl_evals.py` (sibling repo), tests.

### T8 — Measurement (gates everything)

**Deliverable:** the eval runs below + a short results note appended to this doc.

- Runner: `../ruleschat-evals/evals/src/scripts/asl_evals.py`, judge `gpt-5-mini`,
  med-AB + med-CD v1.1 files (60 questions), **model = gpt-5.4** (the production
  tier; the fable-5 56/60 is a reference ceiling, not the baseline).
- **Run A (required baseline, doesn't exist yet):** gpt-5.4 on med-ABCD, current
  main, `--agentic --auto-route-tools` (today's production configuration).
- **Run B:** Run A config + T7 cite-check (`--use-cite-check`) — isolates the value
  of the deterministic pass alone.
- **Run C:** Run B + T3–T6 merged (lookup tools exposed in-loop + addendum) —
  measures what model-initiated fetching adds on top.
- Optional **Run D:** repeat B with a cheaper model (e.g. gpt-5-mini) to see whether
  the cite-check pass lets a cheaper tier hit gpt-5.4-baseline accuracy — that's the
  real cost win if it works.
- Report per run: pass/fail by question type, flips vs Run A (both directions),
  `tools_called` distribution, cite-check trigger rate and revision rate, latency
  delta (p50/p95), token delta (the cite-check turn should be the dominant cost).
- **Success criteria:** Run B strictly reduces recall failures vs Run A with no calc
  regressions; p95 latency < 2× Run A. Run C justifies its complexity only if it
  beats Run B — if it doesn't, T5/T6 stay merged but the addendum gets shortened and
  the project stops there.

**Depends on:** T3–T6 merged (T1's `sections.json` built on the machine running evals).

## 5. Out of scope (explicitly)

- Migrating the hand-rolled loops to the OpenAI Agents SDK (deferred until the
  three-loop duplication causes a real bug; see discussion 2026-07-09).
- Flipping the chat UI `agentic` toggle default, demo-mode exposure, and any UI for
  showing fetched sections — product decisions after T8 numbers exist.
- Committing rulebook text anywhere, in any form. Fixtures use invented text only.
