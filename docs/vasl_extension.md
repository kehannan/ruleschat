# VASL "Ask LLM" extension

The extension (`vasl-extension/`, a VASSAL `.vmdx` for VASL) adds an **Ask LLM**
button to VASL. A question goes to ruleschat's `/api/ask/stream` together with
a snapshot of the game and what VASL's own map model knows about the attack
being asked about. The server does the rules math; the model explains it.

```
VASL extension ──facts──▶ /api/ask ──▶ resolve_attack ◀── counter_rules (data)
 (reports, no rules)                        │        ◀── board-file trace (fallback)
                                            ├──▶ calculator card (fixed numbers)
                                            └──▶ LLM (explains, cites rules)
                                                        │
                                   answer in VASL ◀─────┘  card first, then explanation
```

The split is deliberate:

- **The extension is the sensor.** It runs inside VASL, where the live map
  already has overlays, SSR terrain changes, counters and levels applied, and
  VASL's own LOS engine. It reports what it sees and interprets nothing.
- **The server is the rules engine.** `resolve_attack` turns facts into FP,
  column, DRM and odds. What each counter *does* lives in one data table.
- **The model is the explainer.** It never re-derives the numbers.

---

## 1. The payload

`POST /api/ask/stream` with `Authorization: Bearer <ruleschat API key>`.

A fire question ("W6 fires at T4 – IFT and DRM?") from extension 0.6, during
Prep Fire, with a roadblock in U5. Lists of hexsides are shown for one hex only;
every hex in `facts.hexes` carries all six.

```jsonc
{
  "question": "W6 fires at T4 - what is the IFT and DRM?",
  "rich": true,                         // extension >= 0.4: wants calculator_html
  "vsav": "data:application/octet-stream;base64,UEsDBBQ...",   // whole game save
  "side": "German",                     // perspective for fog of war
  "player": "Kevin",                    // VASSAL display name (maps to a side)
  "model": "claude-fable-sub",          // registry key; server may override
  "game_phase": "PFPh",                 // VASL phase wheel: PFPh/MPh/DFPh/AFPh
  "history": [                          // last <= 6 [question, answer] pairs
    ["S3 fires at W4 - IFT and DRM?", "The 5-4-8 attacks on the 4 FP column..."]
  ],

  // Present when the question names two hexes. The first is the firer, the
  // second the target ("S3 fires at V4"; "41-S3" for a multi-board map).
  "native_los": {
    // --- VASL's own LOS check, between the two hex-center Locations ---
    "source": "W6",
    "target": "T4",
    "blocked": false,
    "reason": "",                       // VASL's text when blocked
    "hindrance": 1,                     // total hindrance DRM VASL counted
    "range": 3,
    "source_level": 0,                  // level WITHIN the hex (0 = ground),
    "target_level": 0,                  //   not elevation: a hill hex is still 0

    // --- extension >= 0.5: what VASL's map model knows (raw facts) ---
    "facts": {
      "source_height": 0,               // absolute heights (hill + level in hex)
      "target_height": 0,
      "source_terrain": "Paved Road",   // terrain at each Location as on the map
      "target_terrain": "Paved Road",   //   now (overlays/SSR applied)
      "source_base": 0,
      "target_base": 0,

      "path": [                         // hexes crossed, firer -> target
        {"hex": "V5", "base": 0, "terrain": "Paved Road", "counters": []},
        {"hex": "U5", "base": -1, "terrain": "Shallow Stream", "counters": []}
      ],
      "target_counters": ["vehicle: PzKw IIA vca3 tca2 BU"],
                                        // SMOKE / vehicles / wrecks VASL counts

      "target_entry": {                 // the hexside the LOS enters the target by
        "neighbor": "U5", "side": 2, "terrain": ""   // "Wall", "Hedge", ...
      },
      "first_hindrance_hex": "U5",      // where VASL found the first hindrance
      "blocked_at_hex": null,           // where VASL blocked the LOS, if it did

      // --- extension >= 0.6: every non-unit counter, uninterpreted ---
      "hexes": [
        {"hex": "W6", "role": "firer",  "base": 0,  "terrain": "Paved Road",
         "sides": ["..."], "counters": [{"name": "Prep Fire", "side": -1,
                                        "angle": null, "arrow_level": null}]},
        {"hex": "V5", "role": "path",   "base": 0,  "terrain": "Paved Road",
         "sides": ["..."], "counters": []},
        {"hex": "U5", "role": "path",   "base": -1, "terrain": "Shallow Stream",
         "sides": [
           {"side": 0, "bearing": 0,   "neighbor": "U4", "hexside_terrain": "", "location_terrain": "Shallow Stream"},
           {"side": 1, "bearing": 60,  "neighbor": "V4", "hexside_terrain": "", "location_terrain": "Open Ground"},
           {"side": 2, "bearing": 120, "neighbor": "V5", "hexside_terrain": "", "location_terrain": "Paved Road"},
           {"side": 3, "bearing": 180, "neighbor": "U6", "hexside_terrain": "", "location_terrain": "Shallow Stream"},
           {"side": 4, "bearing": 240, "neighbor": "T5", "hexside_terrain": "", "location_terrain": "Open Ground"},
           {"side": 5, "bearing": 300, "neighbor": "T4", "hexside_terrain": "", "location_terrain": "Paved Road"}
         ],
         "counters": [
           {"name": "Roadblock",                 // counter's VASSAL name
            "side": -1,                          // hexside it sits on, -1 = in the hex
            "angle": -120.0,                     // VASSAL FreeRotator angle
            "arrow_level": null}                 // DO roadblock "Arrow" layer (1 down, 2 up)
         ]},
        {"hex": "T4", "role": "target", "base": 0,  "terrain": "Paved Road",
         "sides": ["..."], "counters": []}
      ]
    }
  }
}
```

### Field reference

| Field | From | Notes |
|---|---|---|
| `question` | text box | ≤ 4,000 chars. Hexes named in it pick the attack pair. |
| `vsav` | VASL's live game, saved to bytes | The server parses units, markers, boards and SSRs from it (`app/services/vsav_service.py`). Sent when "Attach board" is on. |
| `side` / `player` | VASL player roster / VASSAL display name (`RealName` pref) | Fog of war: the server hides the other side's concealed/HIP units. "Solo: full view" sends neither. The VASSAL password pref (`getUserId`) is never sent. |
| `model` | Settings dropdown | Non-admin keys fall back to the server default; subscription models are admin-only. |
| `game_phase` | phase wheel | Maps to the fire phase (`PFPh` → Prep, `MPh` → Defensive First, `DFPh` → Final, `AFPh` → Advancing). |
| `history` | transcript | Context only; `/api/ask` is stateless. |
| `native_los.*` | `Map.LOS(...)` in VASL | The verdict from VASL's own LOS engine (it ignores roadblocks; see below). |
| `facts.*_height` | `Location.getAbsoluteHeight()` | Absolute levels: use these, not `*_level`. |
| `facts.target_entry` | LOS line + `Hex.getHexsideTerrain(side)` | Decides hexside TEM (B9.3) at the target. |
| `facts.path[].counters` | `VASLGameInterface.getSmoke/getVehicles/getWreck` | What VASL counts as hindrances, per hex. |
| `facts.first_hindrance_hex` | `LOSResult.firstHindranceAt()` | Names the hindrance when nothing else explains it. |
| `facts.hexes[].counters` | every piece in the hex | Units and anything invisible/obscured to this player are never sent. |
| `facts.hexes[].sides` | `Hex.getHexsideLocation/getAdjacentHex/getHexsideTerrain` | `bearing`: 0 = up, clockwise. Lets the server turn a counter's arrow into a hexside. |

Older extensions send less, and the server copes: 0.3 sends no `facts`, and
0.5 sends `facts` without `hexes` (with a pre-digested `roadblocks` list
instead).

---

## 2. The response stream

NDJSON, one JSON object per line, in this order:

| Line | Meaning |
|---|---|
| `{"calculator_html": "<table…>"}` | The calculator card: firers, column, DRM line items, odds. Rendered by the server as Swing-safe HTML 3.2 and shown above the answer. Fire questions only. |
| `{"status": "Resolving the attack from the save file"}` | Progress: tool being run, repeated at least every 10 s (keeps nginx's 60 s timeout from cutting the stream). |
| `{"delta": "…"}` | Answer text (markdown). |
| `{"error": "…"}` | Mid-stream failure; the stream ends. |
| `{"done": true, "model": …, "elapsed_seconds": …, "tokens_in": …, "tokens_out": …, "cost_usd": …, "remaining_today": …}` | Final metadata line. |

Errors before the stream starts (bad key, daily limit, unreadable save) are
ordinary HTTP errors with `{"detail": "…"}`.

The answer text follows a fixed format (`ASK_ANSWER_FORMAT` in
`app/api/ask.py`): one sentence, at most three rule-cited bullets, then
`Assumptions:` and `Rules:` sections that the extension folds behind
"▸ 3 assumptions · ▸ 5 rules cited".

---

## 3. What the server does with it

1. **Parse the save** (`vsav_service.parse_vsav`) → units, markers, boards,
   SSR transforms, overlays; mask hidden units for the requesting side.
2. **Precompute the attack** when the question is a fire question and
   `native_los` names the pair (`_precompute_attack` in `app/api/ask.py`).
   `resolve_attack` (`app/asl/attack_resolver.py`) derives FP, range, PBF, TEM
   and DRM, then IFT odds via `app/asl/ift.py`.
3. **Prefer VASL's facts** (`_apply_vasl_facts`): target hexside TEM from
   `target_entry`, heights, hindrance sources, and counter effects from
   `counter_rules`. Without facts, `board_terrain.py` traces the board files
   instead (fallback: no overlays, no counters).
4. **Render the card** (`_calculator_html`) and stream it first.
5. **Ask the model** with the full resolution marked authoritative: explain
   each line, cite rules, relay assumptions. It must not recompute the numbers.

If VASL reports the LOS blocked, there is no attack: the card says so and
names the obstacle, using `blocked_at_hex` plus a terrain trace.

---

## 4. Counter rules: where edge cases go

`app/asl/counter_rules.py` is the single table of what a counter does. Each
counter in `facts.hexes` is classified, first match wins:

| Kind | Meaning | Examples |
|---|---|---|
| `hexside_wall` | Wall-like hexside obstacle VASL's LOS ignores: TEM when the LOS enters the target across it; a Half-Level Obstacle to same-level LOS elsewhere (B9.2) | Roadblock (B29) |
| `handled` | The resolver already reads it from the save | Foxhole, Trench, concealment `?` |
| `no_effect` | No effect on LOS or TEM | Prep Fire, CX, Pin, Wire, minefields |
| `vasl_los` | VASL's LOS engine already counts it (`data/vasl_counter_metadata.json`, from VASL's `CounterMetadata.xml`) | SMOKE, WP, wrecks, blazes, rubble, bridges, crests |
| `unmodeled` | Anything else | Pillbox, … |

`unmodeled` counters on the LOS or in the target hex are listed in the answer
as **not modelled — check**, so a new edge case shows up the first time it
appears instead of being silently ignored.

**Adding an edge case is a row in `COUNTER_RULES` plus a test** in
`tests/test_attack_resolver.py`, not new code in the extension.

A hexside counter's hexside comes from where it sits (`side >= 0`), otherwise
from its arrow. Roadblock art points **down** at rotation 0, and VASSAL draws
`FreeRotator` at −angle, so the arrow's bearing is `arrow_base − angle`
(`arrow_base` 180; 0 when a DO roadblock's Arrow layer is set to up). The
road hexside is preferred as a tie-break (B29.1).

---

## 5. Building, installing, debugging

- **Build:** `vasl-extension/build.sh` → `dist/AskRuleschat-<version>.vmdx`.
  It compiles against the local VASSAL and VASL jars with `--release 11`. Bump
  the version in `AskRuleschatButton.java`, `buildFile.xml` and `extensiondata`.
- **Install:** close VASL, remove the old extension in the Module Manager, add
  the new `.vmdx`, relaunch. Extensions load only at module start.
- **When an answer looks wrong:** prod logs every request's facts. Check them
  first:
  `ssh mydigitalocean 'journalctl -u uvicorn.service --since "-1 hour" | grep "VASL LOS facts"'`.
  They show what VASL saw (terrain, hexsides, counters, roadblock angle), which
  separates a sensing problem (extension) from a rules problem (resolver or
  table).
- **Tests:** `tests/test_ask_api.py` (endpoint, card, stream),
  `tests/test_attack_resolver.py` (resolver, facts, counter rules).
