# AskRuleschat — VASL module extension (Phase 0 spike)

A VASSAL module extension (`.vmdx`) for VASL that adds an **Ask LLM** toolbar
button. Phase 0 scope: prove the live-game → `.vsav` → ruleschat-parser
pipeline end to end. Clicking the button snapshots the current game with
`GameState.saveGame(File)` and POSTs the bytes (as a base64 data URL, the
same payload shape the website uses) to `POST /api/vsav/preview`, then shows
the HTTP status and a manifest summary in a dialog.

No LLM question yet — that is Phase 1 (`POST /api/ask` with per-user API
keys and `perspective_side` fog-of-war masking).

## Why this architecture

A running VASSAL game's state **is** the `.vsav` format: `saveGame(File)`
writes the same obfuscated command stream `app/services/vsav_service.py`
already parses (validated 71/71 hexes against the test fixture). So the
Java side stays a thin shell — button, dialog, HTTP — and all parsing and
LLM logic stays in Python on the server.

## Build

Requires a JDK (11+; `brew install openjdk@21`) and a local VASSAL install
(the build compiles against VASSAL's own `Vengine.jar`, so the bytecode is
checked against exactly the API that will load it).

```
./build.sh
```

Produces `dist/AskRuleschat.vmdx`. Overrides: `JAVAC=...` and
`VASSAL_LIB=...` (directory containing `Vengine.jar`).

Bytecode targets `--release 11` to match what VASL itself ships
(VASL 6.7.x builds with `--release 11` against vassal-app 3.7.x).

## Install & test

1. Start the ruleschat dev server on `http://127.0.0.1:8000`.
2. Copy `dist/AskRuleschat.vmdx` into the module's extensions folder
   (e.g. `~/vasl/vasl-6.7.3_ext/` — created by right-clicking the module in
   the VASSAL Module Manager → *Add Extension*, which does the copy for you).
3. Launch VASL. Accept the standard "this module contains custom code"
   warning for the new extension.
4. Open any scenario or saved game, click **Ask LLM** in the toolbar,
   then **Send board to server**.
5. Expect `HTTP 200` and a piece count. `HTTP 429` means the 30/hour/IP
   preview rate limit; `Connection refused` means the dev server isn't up.

The server URL is a persisted extension attribute (editable in the VASSAL
extension editor; default `http://127.0.0.1:8000`).

## Structure

- `src/chat/rules/vasl/AskRuleschatButton.java` — the whole extension: an
  `AbstractConfigurable` that adds a `JButton` to
  `GameModule.getGameModule().getToolBar()` in `addTo()`.
- `buildFile.xml` — extension descriptor. Root element
  `VASSAL.build.module.ModuleExtension` (module=VASL), custom class wrapped
  in a `VASSAL.build.module.ExtensionElement` with empty `target`, i.e.
  attached to the GameModule (same pattern as VASL's OBA Flowchart
  extension, the shipping precedent for custom Java in a `.vmdx`).
- `build.sh` — javac + zip. No Maven; the only compile-time dependency is
  the local VASSAL install.

## Known questions to verify in the live test

- Whether `saveGame(File)` updates the module's "last save file" pointer
  (would make a later Ctrl+S silently target the temp file). If so, Phase 1
  should switch to building the save bytes in memory
  (`GameModule.encode(GameState.getRestoreCommand())` +
  `ObfuscatingOutputStream` + a zip with a `savedGame` entry).
- Whether `saveGame` posts a "game saved" line to the chatter (cosmetic).

## Phase 1 (next)

- Server: extract the shared vsav→parse→prompt orchestration out of
  `app/api/chat.py`/`app/api/demo.py`, add `POST /api/ask`
  (question + vsav + player side), authenticate against `User.api_key`
  (minted by the existing `/generate-api-key`, currently checked by nothing).
- Extension: question box + streamed answer pane, API-key setting stored in
  VASSAL prefs, send the player's side so the server masks the opponent's
  concealed/HIP units.
