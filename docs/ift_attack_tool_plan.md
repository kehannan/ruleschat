# IFT Attack Builder — design

Status: **design draft** (not implemented). Companion to the existing `/ift` odds
engine (`app/asl/ift.py`), which this tool builds on top of — it does not replace it.

## The gap

The current `ift_odds` tool answers: *"given a final FP column and a total DRM,
what are the result probabilities?"* That leaves all the actual rules work to the
caller (LLM or user):

1. **Upstream — firepower determination (A7.2–7.36):** doubling/halving order,
   fraction retention, per-unit vs per-attack modifiers, assault fire's
   add-then-round-up, picking the right column. This is exactly where the model
   makes arithmetic/rules mistakes today (e.g. the A7.31 example:
   `5 ×2 PBF ÷2 AFPh ÷2 concealed +1 AF = 4 FRU, ×2 squads = 8` — easy to
   misorder).
2. **DRM assembly (A7.6, A4.6, A7.531):** TEM + hindrance + FFNAM/FFMO +
   leadership, each with applicability constraints (FFMO negated by any
   hindrance or in-hex TEM; leadership direction also suppresses cowering).
3. **Downstream — what the result means (A7.301–.306, A7.8, A10.3):** a "1MC"
   is not an outcome; *P(break)* against a 7-morale squad is. Users and the
   model both want "what's the chance this attack breaks the squad."

The new tool closes all three, producing an auditable computation chain the
model can cite verbatim in answers.

## Shape of the tool

One new engine function `app/asl/ift.py::compute_attack(...)`, exposed two ways:

- **Agentic tool** `ift_attack` in `app/asl/tools.py` (kept alongside the
  existing `ift_odds`, which remains the right call when the question already
  states a column/DRM).
- **UI**: `/ift` page gains a second mode — "Attack builder" — above the
  existing quick column+DRM form (which stays as "Quick odds").

Pipeline: **units → adjusted FP → column → DRM ledger → distribution
(existing `compute_distribution`) → target effects**.

### Layer 1 — Firepower resolution (A7.2, A7.21–.24, A7.36, A7.8)

Input: a list of firing units, each with:

| field | type | notes |
|---|---|---|
| `fp` | number | printed FP of the unit or SW (a squad firing a MG it mans is two entries) |
| `pbf` | `"none" \| "pbf" \| "tpbf"` | ×2 / ×3, small-arms/MG/ATR/IFE only (A7.21) |
| `long_range` | bool | ×½ (A7.22) |
| `pinned` | bool | ×½ (A7.8) |
| `assault_fire` | bool | +1 after all modification, then FRU; NA with long-range or opportunity fire (A7.36) |

Attack-wide flags (apply to every unit):

| field | notes |
|---|---|
| `area_fire_halvings` | integer ≥ 0 — concealed target, spraying fire, etc.; each application halves again (A7.23, A9.5) |
| `afph` | ×½ unless `opportunity_fire` (A7.24–.25) |
| `opportunity_fire` | negates the AFPh halving; also makes assault-fire NA |

Per A7.2 fractions are **retained per unit**, modifiers are cumulative, units are
summed, and only then is the total mapped to the rightmost column whose bold FP
≤ total (A7.3). Assault fire's +1-then-FRU happens per unit after its own
modification (A7.36, A7.31 EX).

Output includes a per-unit audit trail:

```json
"fp_breakdown": [
  {"fp": 5, "steps": ["×2 PBF = 10", "÷2 AFPh = 5", "÷2 concealed = 2.5", "+1 assault fire → 4 (FRU)"], "final": 4},
  {"fp": 5, "steps": ["…"], "final": 4}
],
"total_fp": 8,
"column": 8
```

### Layer 2 — DRM ledger (A7.3, A7.6, A4.6, A7.531, A7.7)

Itemized rather than a single number, so the answer can show its work:

| field | notes |
|---|---|
| `tem` | target-Location TEM (caller supplies the value; we don't model terrain) |
| `hindrance` | total LOS hindrance DRM; worst case applies to a whole FG (A7.52) |
| `ffnam` | bool → -1 (non-assault movement, Defensive First Fire only) |
| `ffmo` | bool → -1; **validated**: rejected/warned if `hindrance > 0` or `tem > 0` (A4.6 — FFMO is negated by any hindrance or in-hex TEM) |
| `leadership` | leader DRM, e.g. -2 (A7.531) |
| `encircled_firer` | bool → +1 (A7.7: an encircled unit fires at +1) |
| `other` | list of `{label, drm}` for anything else (air bursts, CX, etc.) |

The ledger sums to the `drm` passed to the existing distribution engine and is
echoed back as `drm_breakdown`.

### Layer 3 — Cowering (auto-derived, A7.9)

Today the caller picks `"none" | "regular" | "double"` by hand. The builder
derives it:

- `leadership` direction present → **none** (A7.9)
- `firer_exempt` flag (SMC, berserk/fanatic, British Elite/1st-line, Finn,
  vehicular/IFE fire, fire lane…) → **none**
- `inexperienced` → **double** (A7.9, A19.33)
- otherwise → **regular**

Manual override remains available (the quick form keeps it as-is).

### Layer 4 — Target effects (A7.301–.308, A7.8, A10.3)

Optional `target` block:

| field | notes |
|---|---|
| `morale` | current morale level (after any leader/encirclement adjustment the caller wants — keep v1 simple) |
| `mc_drm` | DRM on the target's MC DR (e.g. -1 leader in the location) |
| `encircled` | bool → morale -1 vs this attack (A7.7) |

For each IFT result the engine knows the conditional outcome over a second 2d6:

- `#KIA` → eliminated (P = 1)
- `K/#` → casualty reduction **and** the survivor takes a #MC (A7.302)
- `#MC` / `NMC` → break if final MC DR > morale; **pin** if it passes with the
  highest possible passing DR, i.e. final MC DR == morale (A7.8); otherwise OK
- `PTC` → pinned on a failed NTC (final DR > morale) (A7.305)
- `—` / off-table → no effect

Convolving the IFT distribution with the MC distribution gives headline numbers
that are the actual answer to most "how good is this attack?" questions:

```json
"vs_target": {
  "morale": 7,
  "p_eliminated_or_reduced": 0.083,
  "p_broken": 0.291,
  "p_pinned": 0.094,
  "p_no_effect": 0.532
}
```

(`p_broken` here means "broken but not casualty-reduced", categories are
mutually exclusive and sum to 1 with rounding.)

**Unarmored vehicle targets** (A7.308): the ★ vehicle-line kill numbers are
already in `ift_table.json` (`vehicle_columns`). A `target.kind: "vehicle"`
variant returns P(burning wreck) (final DR ≤ ½ kill#), P(eliminated) (< kill#),
P(immobilized) (= kill#) instead of the MC math. Cheap to add since the data
is sitting there unused.

## Agentic tool schema (sketch)

```json
{
  "name": "ift_attack",
  "description": "Build a full IFT attack from firing units and situation: computes adjusted FP per A7.2-.36, the FP column, an itemized DRM, cowering, the result distribution, and (optionally) break/pin/elimination odds vs a target morale. Use whenever a question describes the SITUATION (units, range, terrain, movement) rather than an already-known FP column.",
  "parameters": {
    "units": [{ "fp": 4, "pbf": "none", "long_range": true, "pinned": false, "assault_fire": false }],
    "afph": false, "opportunity_fire": false, "area_fire_halvings": 0,
    "tem": 0, "hindrance": 0, "ffnam": false, "ffmo": false,
    "leadership": 0, "encircled_firer": false,
    "other_drm": [{ "label": "air bursts", "drm": -1 }],
    "inexperienced": false, "firer_cowering_exempt": false,
    "san": null,
    "target": { "kind": "personnel", "morale": 7, "mc_drm": 0, "encircled": false }
  }
}
```

The existing `ift_odds` keeps its niche ("the attack is on the 8 column at +2 —
what's the chance of a 2MC?"); the system prompt / tool descriptions steer the
model: situation described → `ift_attack`; column already known → `ift_odds`.

## UI (`/ift` page)

Tab/segmented control at the top: **Attack builder | Quick odds** (current form
becomes the second tab, unchanged).

Builder layout, same visual language as the current card (serif headings, mono
captions, stepper buttons):

```
┌─ FIRING UNITS ──────────────────────────────────────────────┐
│ [FP 4] [PBF ▾] [☐ long range] [☐ pinned] [☐ assault]  [×]   │
│ [+ add unit]                                                │
│ Attack: [☐ AFPh] [☐ Opp Fire] [concealed/area ×½: 0 −/+]    │
├─ DRM ───────────────────────────────────────────────────────┤
│ TEM [+0 −/+]  Hindrance [+0 −/+]  [☐ FFNAM] [☐ FFMO]        │
│ Leadership [0 −/+]   [☐ firer encircled]   + other…         │
├─ TARGET (optional) ─────────────────────────────────────────┤
│ Morale [7 −/+]  MC DRM [0 −/+]  [☐ encircled]  SAN [– −/+]  │
└─────────────────────────────────────────────────────────────┘

  FP 4 ×½ long range = 2  ·  total 2 → 2 column      ← live math line
  DRM: +2 TEM, −1 FFNAM = +1  ·  cowering: regular

  [ summary cells: BREAK 29% · PIN 9% · CASUALTY 8% · NO EFFECT 53% ]
  [ existing distribution bars + IFT heatmap, unchanged ]
```

The "live math line" is the per-unit `fp_breakdown` rendered inline — it doubles
as a teaching aid, which fits the assistant's purpose.

## Out of scope v1 (note in tool description so the model doesn't assume)

- **IIFT** (A7.37) — standard IFT columns only.
- **LOS/terrain modeling** — caller supplies TEM/hindrance values; we never
  look at a map.
- **Multi-Location FG geometry** (A7.5), encirclement geometry (A7.7),
  fire-lane, residual FP (A8.2), ROF — the caller flags the consequences
  (worst-case DRM, area fire), not the geometry.
- **Multi-unit target stacks / random selection** (A7.301–.302) — v1 models one
  target unit; K/# is treated as "this unit reduced + survivor MC".
- **ELR / Heat of Battle** on failed MCs (A19, A15) — break is the terminal
  modeled state.

## Implementation sketch

- `app/asl/ift.py`: add `compute_attack(...)` — pure function, builds on
  `compute_distribution` for the dice enumeration; MC convolution is another
  36-combo enumeration per result row. No new data files needed
  (`vehicle_columns` already present for the ★ line).
- `app/asl/tools.py`: add `ift_attack` wrapper + schema (strip UI-only fields,
  same pattern as `ift_odds`).
- `app/api/ift.py`: add `POST /api/ift/attack`.
- `templates/ift.html`: add builder tab; reuse distribution/heatmap rendering.
- Tests: golden cases straight from the rulebook examples — A7.31 EX
  (5-4-8 PBF/AFPh/concealed/assault = 8 FP), A7.24 EX (6-6-6 AFPh = 4 FP),
  A7.34 EX (long-range area fire 1½ FP), A7.2 EX DRM ledger (+1 hindrance
  +1 TEM −1 FFNAM).
- Evals: add `ift_attack`-shaped questions to ruleschat-evals so tool-routing
  (attack vs odds) is itself evaluated.
