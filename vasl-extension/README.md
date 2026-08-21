# AskRuleschat — VASL module extension

A VASSAL module extension (`.vmdx`) for VASL that adds an **Ask LLM** toolbar
button opening a chat dialog styled after ruleschat.com (same palette,
message layout and input dock): streaming answers rendered from Markdown,
follow-up context (recent Q/A pairs ride along with each request), and
agentic-tool progress shown inline, and a footer per answer with model, latency, tokens in/out and list-price cost (from the server's `done` line). Each question ships an in-memory
snapshot of the current game — the same bytes as a `.vsav` save, built via
`GameModule.encode(GameState.getRestoreCommand())` + `ObfuscatingOutputStream`,
so the module's save state, dirty flag, and last-save pointer are untouched.
With no game loaded (or "Attach board" unchecked) it is a plain rules Q&A.

Settings (server URL, API key, model dropdown) live behind the **Settings** button in
the header and are stored in **`AskRuleschat.properties` in VASSAL's prefs
directory** (macOS: `~/Library/Application Support/VASSAL/prefs/`). That is
our own file, written immediately on OK, so the key is entered once and
survives VASSAL restarts — 0.2.0 kept these in VASSAL's module `Prefs`,
which did not persist them across sessions. Default server:
`https://ruleschat.com`.

## Credentials (one field, auto-detected by the server)

- **ruleschat account key** — minted on the ruleschat `/profile` page.
  LLM calls run on the server's provider keys; usage is capped per day
  (`ASK_DAILY_LIMIT`, default 50).
- **OpenRouter pass-through** — the user's own `sk-or-...` key. Generation
  is billed to their OpenRouter account; the server's OpenAI key is used
  only for retrieval. The key is per-request only, never stored. The model
  dropdown sends Ox Alpha as `stealth/ox-alpha` for pass-through keys.

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

1. In the VASSAL Module Manager, right-click the VASL module ->
   *Add Extension* -> pick `dist/AskRuleschat.vmdx`. If your VASL loads
   extensions from a shared folder (e.g. `~/vasl/extensions/`), copy the
   file there instead — check the startup log for which folder is read.
2. Launch VASL and accept the standard "this module contains custom code"
   warning for the new extension.
3. Click **Ask LLM**; the Settings dialog opens on first use — paste your
   API key (generated on your ruleschat profile page, or your own OpenRouter
   `sk-or-...` key). It is saved to `AskRuleschat.properties` in VASSAL's
   prefs directory and never asked for again.
4. Open a scenario or saved game, type a question, hit Enter. Leave
   "Attach board" checked to ask about the current position; check
   "Solo: full view" in solo games (default when you haven't joined a
   side), uncheck it in two-player games so hidden units stay hidden.

Local development: run the dev server and set the Server URL to
`http://127.0.0.1:8000` in Settings.

## Structure

- `src/chat/rules/vasl/AskRuleschatButton.java` — the whole extension: an
  `AbstractConfigurable` that adds a `JButton` to
  `GameModule.getGameModule().getToolBar()` in `addTo()`. The dialog is a
  `JEditorPane` (HTML) transcript re-rendered from a small message model
  (`Message`), a `Md` Markdown-to-HTML converter for the subset the answers
  use, and a self-painted `FlatButton` (Aqua ignores `setBackground`).
  `buildContent()` returns the whole UI as a panel so it can be rendered
  off-screen for previews.
- `buildFile.xml` — extension descriptor. Root element
  `VASSAL.build.module.ModuleExtension` (module=VASL), custom class wrapped
  in a `VASSAL.build.module.ExtensionElement` with empty `target`, i.e.
  attached to the GameModule (same pattern as VASL's OBA Flowchart
  extension, the shipping precedent for custom Java in a `.vmdx`).
- `build.sh` — javac + zip. No Maven; the only compile-time dependency is
  the local VASSAL install.

## Later

- Unify the site's two websocket orchestrations (`app/api/chat.py`,
  `app/api/demo.py`) with `/api/ask` behind one shared service function.
- Hash account API keys at rest (currently plaintext in the users table).
- Optional: echo answers into the VASSAL chatter.
