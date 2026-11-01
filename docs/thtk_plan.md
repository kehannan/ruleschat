# TH/TK (To Hit / To Kill) — plan

Goal: deterministic ordnance resolution (ASL Chapter C) with the same shape as
the IFT work — a pure engine, agentic tools, a board-aware resolver, a
standalone page — and **every tool the VASL extension gets must also work in
the website chat** (`/ruleschat`, `/demo`).

## Starting point

- **Parked engine:** `origin/feature/thtk-flow-tree` (`35c7633`) has
  `app/asl/thtk.py` + `thtk_tables.json` + `templates/thtk.html` +
  `app/api/thtk.py`. It has the real C3 To Hit table, the C4 gun/ammo mods, the
  C7.31 AP BASIC TK# per gun, Case D range mods, and a 36-roll flow tree
  (Critical / Multiple / Normal → Burn/Elim, Immob, Shock). Its own docstring
  flags these known gaps: the CH rule is approximate, AP only (no HE/HEAT
  TK), the TK numbers aren't verified, and there's no armor/aspect input. We
  reuse the data and the flow-tree idea, but not the page as-is.
- **Tool plumbing is already shared.** `app/asl/tools.py` (`TOOL_SCHEMAS`,
  `CALC_TOOL_NAMES`, `CONTEXT_TOOLS`) feeds the OpenAI, OpenRouter, and
  subscription-CLI (MCP bridge) paths for both `/api/ask` (the plugin) and the
  chat WS (the website). Website chat already accepts a `.vsav` and passes
  `vsav_state` into tool context. A tool added there reaches both channels for
  free.
- **What the plugin has and the website doesn't:**
  1. `_precompute_attack` + `_calculator_html` in `app/api/ask.py`: the
     server runs the resolver and returns a calculator card. Only `/api/ask`
     does this.
  2. `native_los.facts`, game phase, and selected firers/targets come from the
     extension. The website falls back to the board-file trace and has no
     phase or selection.
  TH/TK has to close gap 1 and live with gap 2. Anything the resolver needs
  must be derivable from the `.vsav` alone, with VASL facts as an upgrade
  rather than a requirement.

## Architecture

```
app/asl/thtk.py              pure engine: TH#, TK#, 36-roll flow tree, HE-vs-infantry → ift.py
app/asl/thtk_tables.json     C3/C4/C5/C6/C7/C8 data (ported from branch, verified)
app/asl/vehicle_data.json    AFV/Gun stats keyed by VASL piece name/gpid (see Decision 1)
app/asl/ordnance/            board-aware resolver, same pattern as app/asl/attack/
    pipeline.py              ordered STEPS
    steps/locate.py          firer Gun/AFV + target from the save; reuse attack LOS steps
    steps/aspect.py          target aspect (front/side/rear, hull/turret) from VCA/TCA rotation
    steps/to_hit.py          target type, basic TH#, C4 mods, C5 firer DRMs, C6 target DRMs
    steps/to_kill.py         ammo → AP/HE/HEAT TK#, Case D, minus aspect AF; vs infantry → IFT
    steps/odds.py            flow tree → outcome distribution
app/services/precompute.py   extracted from ask.py: route question → resolver → card + prompt block
```

**Tools** (added to `TOOL_SCHEMAS` + `CALC_TOOL_NAMES`, so all model paths on both channels get them):

| Tool | When | Inputs |
|---|---|---|
| `thtk_odds` | TH# / TK# already known | final TH#, TK#, AF, DRMs |
| `ordnance_attack` | situation described in words, no save | gun (mm, barrel, nationality), ammo, range, target type, aspect AF or vehicle name, TH DRM items |
| `resolve_ordnance` | `.vsav` attached (`CONTEXT_TOOLS`) | `firing_hex`, `target_hex`, optional `ammo`, `phase`, `firer`/`target` unit filter |

The routing text in `asl_service.py` gets an "ORDNANCE RESOLUTION" block, the
same shape as the CC block: save attached → `resolve_ordnance`; otherwise
`ordnance_attack`; never hand-derive TH#/TK#.

## Phases

### Phase 1 — Engine, verified (no UI) — DONE 2026-10-30 (`feature/thtk`)
Status: `app/asl/thtk.py` `compute_shot` + `thtk_tables.json` were rewritten
and checked against the eASLRB v3.14 divider pages (C3/C4/C7.31-.34/C7.7/C8.31,
National Capabilities TH# color). They pass the rulebook's own examples in
`tests/test_thtk.py`. Fixes over the parked branch:
- Armor is subtracted.
- An Original 12 is not an auto-miss.
- Russian 76L is 13.
- Japanese 75*=12 applies only to the Year-38 type, via override.
- HE, HEAT and APCR/APDS To Kill are added.
- Real CH, Improbable and Multiple Hit rules.
- HD hull-miss.
- HE vs personnel resolves on the IFT.

Still open:
- The C5/C6 TH DRM catalogue. Today the engine takes a free DRM ledger;
  Phase 2's `ordnance_attack` should offer structured cases.
- MG/ATR TK.
- Area Target Type vs vehicles (C1.55).

Original scope:
- Port `thtk.py` and its tables from the branch onto main.
- Verify every table and rule against the rulebook through our own
  `get_section` (C3, C3.7 CH, C3.8 Multiple Hits, C4, C5, C6, C7.31/.32/.33
  AP/HE/HEAT, C7.7 results, C8.31 HE equivalency).
- Fix the flagged simplifications: real CH rule, Multiple Hits per C3.8, HE and
  HEAT TK, APDS/APCR, Smoke can't kill, and unarmored targets.
- Ordnance vs Infantry/Area target resolves on the IFT through `ift.py`, so
  there's no second IFT.
- Tests: hand-worked examples for each target type × ammo, plus the
  2-always-hits and 12-always-misses edges.
- **Done when** the engine tests pass and each table row cites its rule.

### Phase 2 — Manual tools + `/thtk` page (ships to both channels) — BUILT 2026-10-30
Status:
- Done: `thtk.build_th_drm` (C5/C6 case catalogue), `compute_attack`,
  `quick_odds`; the `ordnance_attack` / `thtk_odds` tools (in
  `CALC_TOOL_NAMES`, so every model path on both channels gets them); the
  `app/api/thtk.py` page + API; the `_tools_subnav.html` restore; and routing
  text in the .vsav instruction block (non-save questions route on the tool
  descriptions, like IFT).
- **Not done:** the TH/TK eval questions and a live check of tool choice per
  model path.

Original scope:
- Add `thtk_odds` and `ordnance_attack` to `tools.py`.
- Add `POST /api/thtk/odds` and `POST /api/thtk/attack`.
- Build `templates/thtk.html` with "Attack builder" and "Quick odds" modes,
  matching `/ift`. Restore the tools subnav.
- Add 15–20 TH/TK questions to the eval set and confirm the right tool is
  chosen on the OpenAI, OpenRouter, and CLI paths.
- **Deployable on its own.** Plugin and website both answer described
  situations.

### Phase 3 — Board-aware resolver + shared calculator card
- `vsav_service`: give AFV/Gun pieces structured fields: name, side,
  FreeRotator angle (VCA), turret/TCA state, Motion/BU/CE/Shock/Immob markers,
  and acquisition counters.
- Map each piece to stats through `vehicle_data.json`. Unknown vehicle → the
  tool asks for AF/gun and says "not modelled", like unknown counters today.
- Build the `app/asl/ordnance/` pipeline. Reuse `locate`, `vasl_verdict`,
  `measure_range`, `grid_trace`, and `vasl_facts` from the attack steps, and
  compute aspect geometrically from hex positions and the VCA. Prefer VASL facts
  when present, fall back to the save when not. Results must match on both
  channels, and tests run each case with and without `native_los`.
- Extract `_precompute_attack` / `_calculator_html` into
  `app/services/precompute.py`, which routes to `resolve_ordnance` when the
  firer hex holds a Gun/AFV, else `resolve_attack`.
- Call it from **`chat.py` and `demo.py` too**, and render `calculator_html`
  in `chat-shared.js`. The website then gets the plugin's card for IFT and
  TH/TK alike. This closes gap 1.
- Website-only gap: without phase or selection, the resolver asks or assumes
  and says so (for example "assumed AP, Defensive Fire").
- **Done when** the same `.vsav` + question gives the same card and numbers via
  `/api/ask` and via a chat upload.

### Phase 4 — Extension 0.8 + polish
- Only if Phase 3 finds the save insufficient: extend `native_los.facts` with
  the vehicle VCA/TCA and the firer's gun.
- Let the extension's attack-pair picker include AFV/Gun firers. Bump the
  version and add notes to `docs/vasl_extension.md`.
- Board-state eval cases with AFVs, then deploy (with explicit OK).

### Out of scope for v1 (listed as caveats in the tool output)
ROF / multiple shots, Intensive Fire, B#/X# malfunction, ammo depletion
rolls, Deliberate Immobilization, Spotted Fire, OBA, Shock/UK recovery,
Gun-vs-Gun duels across fire phases.

## Decisions needed

1. **Vehicle/Gun stats source.** AF, CA, gun, and target size come from
   Chapter H. VASL pieces carry names, not stats. Options:
   (a) hand-build `vehicle_data.json` for the ~40 most common AFVs/Guns, keyed
   by VASL piece name, and grow it;
   (b) no table, where the user or model supplies AF/gun every time.
   **Built (2026-11-01):** `scripts/extract_vehicle_listings.py` parses the
   eASLRB Chapter H vehicle listings into the gitignored
   `data/rulebook/vehicles.json` (651 vehicles incl. 55 U.S.: AF, TA → turret AF per
   D1.63-.64, Size, MA gun, GT, ROF, B#, special ammo). `app/asl/vehicles.py`
   resolves names and nicknames. `ordnance_attack` takes
   `target_vehicle` / `firer_vehicle`, and explicit values still win.
   Gaps:
   - The U.S. listing (H32-H33) uses fonts with no character map; it is
     decoded with per-font character tables
     (`scripts/data/us_listing_glyphs.json`, codes in Windows-1252 order).
   - Landing craft.
   - Ordnance (Gun) listings are not parsed yet.
   - Prod needs the JSON copied to `data/rulebook/`, like `sections.json`.

   Original decision (2026-10-30): (a), built later from vehicle info Kevin will
   supply. Until then every tool takes user-supplied AF/gun/size (b).** Design
   the tool inputs and `vehicle_data.json` schema so a table row and
   user-supplied values are interchangeable, and a user value always overrides
   the table. Phase 3 still runs without the table: unknown vehicles ask for
   stats.
2. **Branch handling.** Recommended: cherry-pick only the engine and tables
   onto a fresh `feature/thtk` from main, and rebuild the page. The branch
   predates the history scrub, so its SHAs are stale anyway.
