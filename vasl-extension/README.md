# AskRuleschat — VASL module extension

A VASSAL module extension (`.vmdx`) for VASL that adds an **Ask LLM** toolbar
button. It opens a dialog where the player types a question; the extension
snapshots the current game with `GameState.saveGame(File)` and sends
question + save + player side to ruleschat's `POST /api/ask`, then shows
the answer. With no game loaded it still works as a plain rules Q&A.

## Credentials (one field, auto-detected by the server)

- **ruleschat account key** — minted on the ruleschat `/profile` page.
  LLM calls run on the server's provider keys; usage is capped per day
  (`ASK_DAILY_LIMIT`, default 50).
- **OpenRouter pass-through** — the user's own `sk-or-...` key. Generation
  is billed to their OpenRouter account; the server's OpenAI key is used
  only for retrieval. The key is per-request only, never stored. The model
  field must then be an OpenRouter slug (e.g. `deepseek/deepseek-v4-flash`,
  the default).

## Fog of war

The request carries `PlayerRoster.getMySide()` (VASL: "Axis"/"Allied") and
the VASSAL user id. The server resolves a perspective side — exact match
against the save's unit sides first, then the save's player→side map — and
masks the opponent's concealed units to "?" and drops their HIP units. An
unresolvable side masks BOTH sides' hidden units (fails closed, never
leaks). Only a request with neither field gets the full-information view.

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
4. Open a scenario or saved game, click **Ask LLM**, fill in the API key
   (ruleschat profile key or `sk-or-...`), type a question, hit Enter.

Server URL / API key / model are persisted extension attributes (defaults
editable in the VASSAL extension editor) and editable per-session in the
dialog itself.

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

## Later phases

- Streaming answers (the endpoint is one-shot; long agentic answers can
  take a minute-plus — the dialog just waits with the button disabled).
- Unify the site's two websocket orchestrations (`app/api/chat.py`,
  `app/api/demo.py`) with `/api/ask` behind one shared service function.
- Hash account API keys at rest (currently plaintext in the users table).
- In-memory save building instead of `saveGame(File)` if the live test
  shows side effects (see above).
