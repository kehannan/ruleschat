"""
ASL Rules Assistant Service

This service provides a unified interface for getting ASL rule answers.
Used by both the web application and evaluation scripts to ensure consistency.
"""
import base64
import os
import json
import logging
import time
from pathlib import Path
from typing import Optional, Generator, Tuple, Any, Dict, List, Union

from openai import OpenAI

from app.asl.config import load_asl_config, ASLConfig
from app.asl.client import OpenAIResponsesClient
from app.asl.openrouter_client import build_openrouter_client_from_env
from app.asl.retrieval import retrieve_chunks, format_chunks_as_context
from app.asl.policy import build_instructions
from app.asl.postprocess import (
    extract_response_text,
    compute_timing_metrics
)
from app.asl.tools import (
    TOOL_SCHEMAS_CHAT,
    calc_tool_schemas,
    lookup_tool_schemas,
    execute_tool,
)
from app.asl import rules_lookup

_IMAGE_MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

_TERRAIN_LEGEND_PATH = Path(__file__).resolve().parents[2] / "static" / "img" / "terrain_legend.png"


def _load_terrain_legend_data_url() -> Optional[str]:
    if not _TERRAIN_LEGEND_PATH.is_file():
        logging.warning("Terrain legend not found at %s; multimodal calls will skip it", _TERRAIN_LEGEND_PATH)
        return None
    b64 = base64.b64encode(_TERRAIN_LEGEND_PATH.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


_TERRAIN_LEGEND_DATA_URL = _load_terrain_legend_data_url()

VISION_INSTRUCTIONS_ADDENDUM = """

Multiple images are attached. The FIRST image is a fixed VASL terrain legend showing labeled examples of 12 terrain types: Open Ground, Road (Dirt), Road (Paved), Woods, Wooden Building, Stone Building, Wall, Hedge, Grain, Brush, Orchard, and Hill. The REMAINING image(s) are the user's board screenshot(s) - the user may attach more than one view of the same situation (for example: a wide view plus a zoomed-in detail of specific counters). Treat all user images as views of the same game situation unless they obviously depict different scenes.

Before naming any terrain on the user's board, do visual matching against the legend - compare each board hex's color, pattern, and shape to the legend cells, and pick the closest match. Do not rely on prior assumptions about VASL conventions; the legend is the source of truth for what each terrain looks like. Distinguish Wooden Building (+2 TEM) from Stone Building (+3 TEM) by color/texture - Wooden is reddish-brown, Stone is gray. Distinguish Road (Dirt) from Road (Paved) similarly.

Counters in VASL frequently appear ROTATED at angles (commonly 30-60 degrees) when a unit has moved, fired, or is in a special state - this is normal VASL behavior, not image corruption. Rotation flips the counter to show its "moved" / "fired" / "CX" face. Read counter labels (firepower-range-morale, gun caliber like 75LL, MA value, vehicle ID, leadership) regardless of orientation; mentally rotate the text. AFV counters carry small numeric details (Basic TH#, MA, Target Size) that are critical for to-hit calculations - extract them when readable, and explicitly say which fields are unreadable when they are not. When multiple user views are attached, prefer the highest-detail view for reading small counter labels.

Then: describe what you see across the user's board view(s) (hexes visible, counters and their state - broken/CX/disrupted/pinned, apparent LOS lines, terrain identified via the legend). Call file_search for the rule sections that govern the situation depicted. Cite specific rule sections (e.g., A6.4) in your answer. Reason over both the image(s) and the retrieved rules. If a counter, hex, or detail is unreadable, say so explicitly rather than guessing. Never make a rule claim without a file_search citation."""


VSAV_INSTRUCTIONS_ADDENDUM = """

The user attached a VASL .vsav save file, and a BOARD STATE block parsed directly from it is appended to the question. Treat the BOARD STATE block as GROUND TRUTH for which units and counters exist and which hex each occupies - it is exact, machine-parsed data from the save file, not vision output, so prefer it over any screenshot when they disagree about unit identities or positions. Unit entries carry state flags in [..] (BROKEN, concealed, HIP, label) and per-unit markers in {..} (DM, Melee, Pin, Prep Fire, etc.); a ski counter on a unit shows its decoded face - '{Skis: worn}' means the unit is a Skier in ski mode (E4.2, E4.5 CC DRMs and the Melee-lock exemption apply) while '{Skis: carried}' means the skis are merely carried at 1 PP (E4.21) and the unit is normal Infantry with NO E4 Skier effects; board-level SSR transforms (e.g. Winter, NoGrain, GrainToBrush) change terrain effects and must be applied. VASL stacking order is meaningful and the parser applies it: a marker or entrenchment counter affects only the units BELOW it in the stack, so a unit whose {..} shows a marker (or 'Foxhole: in' / 'Trench: in') is under that counter and in/affected by it, while units in the same hex WITHOUT that annotation are not - e.g. a unit listed without 'Foxhole: in' is outside the foxhole even when another unit in its hex is in one. Anything on a hex's 'hex markers' list applies to no listed unit (the marker sits at the bottom of a stack or alone in the hex).

When local board data is available, each OCCUPIED hex carries its terrain in [..] right after the hex ID (e.g. "57-H8 [Orchard, road]") - read from VASL's own per-board terrain grid with the save's SSR terrain transforms (NoGrain, GrainToBrush, ...) already applied, including elevation when non-zero. Treat these terrain annotations as reliable. They cover occupied hexes only, and the block still does NOT contain line-of-sight data: when your answer depends on LOS, hindrances, or the terrain of unoccupied intervening hexes, state those assumptions explicitly and invite the user to confirm them, ask about specific hexes, or attach a screenshot of the relevant area. If a hex has no [terrain] annotation (or the block says terrain is unavailable for a board), terrain data was missing for that board - fall back to stating assumptions. Hexes near map overlays may have modified terrain (noted in the block when detected).

FIRE ATTACK RESOLUTION: when the question asks to resolve a fire attack between hexes of the attached save (who fires, final FP, DRM, IFT column, break/kill odds), and function tools are available, call the resolve_attack tool with the '<board>-<hex>' IDs from the BOARD STATE block (e.g. firing_hex "57-H9", target_hex "57-H8") and the fire phase. Do NOT derive firepower, range doubling, TEM, or DRM yourself - the tool computes them deterministically from the parsed save and returns an itemized derivation. Present its derivation faithfully: the per-unit FP breakdown, the range/PBF note, every DRM line item, and ALL of its listed assumptions and warnings (especially that LOS is assumed clear and intervening hindrances are not counted - invite the user to confirm those). Still call file_search for the governing rules it cites (e.g., A7.21 PBF, B27 entrenchments) and cite the sections in your explanation. Assault Fire (A7.36) is detected from the counter art and applied automatically in the advancing phase (a warning names any unit whose capability is unknown); Spraying Fire capability (A7.34) is surfaced as a note but never auto-applied. If resolve_attack returns an error, or the situation needs inputs it cannot know (moving target FFNAM/FFMO, hindrances the user described, a Spraying Fire two-Location attack), fall back to ift_attack with explicit inputs. For fire questions WITHOUT an attached save, keep using ift_attack.

CLOSE COMBAT / MELEE RESOLUTION: when the question asks about Close Combat or Melee in a hex of the attached save (CC odds, kill numbers, the DR needed to eliminate/reduce a unit, who can attack whom in a Melee), and function tools are available, call the resolve_cc tool with the '<board>-<hex>' ID from the BOARD STATE block (e.g. hex_id "57-G9"), plus attacker_side and optional unit filters if the user singled units out. NEVER hand-derive CC firepower, odds ratios, CCT kill numbers, or CC DRM - A11 arithmetic (SMC inherent FP = 1, SW/ordnance excluded, odds rounded DOWN to the printed CCT column, Final DR < KN vs = KN semantics) is exactly where hand derivation goes wrong. Present the tool's derivation faithfully: the per-unit CC FP ledger for each side, BOTH directions (CC is simultaneous - always show the defender's counter-attack too), every DRM line item, the eliminate/Casualty-Reduction thresholds (and that CR eliminates a HS/crew outright per A7.302 when the tool says so), its Melee note (Ambush NA in an existing Melee), and ALL of its assumptions and warnings (no Ambush dr derived, Hand-to-Hand not applied, withdrawal not modeled, either side may split its attacks differently). Still call file_search for the governing rules it cites (A11.11, A11.13, A11.14, A11.16, A11.19) and cite them in your explanation. If resolve_cc returns an error or no save is attached, fall back to the cc_attack tool, supplying the attackers' total CC FP, the defenders' CC FP, and the CC DRMs (it returns the same odds / Kill-Number / eliminate-vs-Casualty-Reduction math). Still call file_search for the governing rules and cite them, and note that attaching the .vsav save would let resolve_cc derive the firepower and DRMs for you. For CC questions WITHOUT an attached save, use cc_attack.

MANDATORY CALCULATOR USE: Any question that requires computing an Infantry Fire Table result MUST be answered by calling ift_attack, and any Close Combat result by calling cc_attack - even when it looks simple, and even with no save attached. Trigger cases include: final/adjusted FP, the FP column, a net DRM total, Residual FP, break/pin/Casualty or kill odds; and for CC: the odds ratio, the Kill Number, or the DR needed to eliminate/Casualty-Reduce a unit. Do NOT hand-derive this arithmetic - it is exactly where hand derivation goes wrong. Instead use file_search to confirm which DRMs/TEM apply, then pass the situation to the tool (FP plus pbf/pinned/assault_fire/afph flags, and the range_to_target plus each firer's normal_range so the tool derives Long Range itself rather than you hand-judging it, TEM, hindrance, FFMO/FFNAM, leadership for ift_attack; attacker_fp, defense_fp and the CC DRMs for cc_attack) and report the tool's numbers verbatim. These two tools cover ONLY IFT and CC math: for movement-point costs, LOS/blind-hex geometry, Morale/Task-Check thresholds, rally/self-rally, concealment dr, sniper checks, To-Hit, and the like there is no calculator - derive those by hand with file_search citations.

Never quote a TEM or DRM value from memory - look it up with file_search first, even for terrain and markers that seem familiar. This applies equally to values you pass INTO ift_attack (the tool trusts whatever TEM you give it). In particular, entrenchments shown as 'Foxhole: in' / 'Trench: in' in {..} have their own TEM rules (B27) distinct from the hex's terrain TEM - and they protect ONLY the units annotated as in them, and similar-sounding features differ (e.g., foxholes and shellholes have different TEMs); verify which applies and cite the section."""


CITE_VERIFICATION_ADDENDUM = """

RULE VERIFICATION: A get_section tool is available. Before answering, call get_section for EVERY rule section you intend to cite — its exact text plus any official Q&A/errata comes back. If a fetched section cross-references another section that could qualify your answer (exceptions, "unless", "EXC:", "NA" clauses), fetch that section too. Never quote a TEM, DRM, or numeric limit from memory — fetch the section that states it, including values you pass into the calculator tools. If get_section returns a note that it fell back to a parent section, or an error, either re-fetch a better ID (use search_rules to find it when available) or state the uncertainty explicitly. Do not cite a section whose text you have not fetched this turn."""


# When the deterministic lookup tools are exposed, the loop needs more turns:
# fetch-verify-answer is a multi-hop pattern. Calculators alone keep the
# tighter budget.
MAX_ITER_DEFAULT = 5
MAX_ITER_WITH_LOOKUP = 8


# User-facing progress labels for the streaming agentic loop. The generator
# yields {"status": <label>} dicts between text deltas; the WebSocket layer
# forwards them as typed messages and the UI shows them in the searching pill.
_TOOL_STATUS_LABELS = {
    "ift_odds": "Calculating IFT odds",
    "ift_attack": "Calculating the IFT attack",
    "cc_attack": "Calculating close combat",
    "resolve_attack": "Resolving the attack from the save file",
    "resolve_cc": "Resolving close combat from the save file",
    "search_rules": "Searching the rules",
}


def _tool_status_label(name: str, args: Optional[Dict[str, Any]]) -> str:
    if name == "get_section":
        sec = (args or {}).get("section")
        return f"Checking rule {sec}" if sec else "Checking rule text"
    return _TOOL_STATUS_LABELS.get(name, f"Running {name}")


def _batch_status_label(calls: List[Dict[str, Any]]) -> str:
    """One pill label covering a whole turn's tool calls.

    The tools themselves are near-instant local lookups, so a per-call label
    would flash for ~1ms. Instead the batch label is shown while the model
    reads the results in the next turn — get_section calls merge into
    "Checking rules D7.1, A8.31 & D7.2", other tools keep their own label.
    """
    sections: List[str] = []
    other_labels: List[str] = []
    for fc in calls:
        raw = fc.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            args = {}
        if fc.get("name") == "get_section":
            sec = args.get("section")
            if sec and sec not in sections:
                sections.append(sec)
        else:
            label = _tool_status_label(fc.get("name", ""), args)
            if label not in other_labels:
                other_labels.append(label)

    parts = list(other_labels)
    if len(sections) == 1:
        parts.append(f"Checking rule {sections[0]}")
    elif sections:
        shown = sections[:4]
        listed = ", ".join(shown[:-1]) + f" & {shown[-1]}"
        extra = f" (+{len(sections) - 4} more)" if len(sections) > 4 else ""
        parts.append(f"Checking rules {listed}{extra}")
    return " · ".join(parts) if parts else "Working on the answer"


def _lookup_tools_available() -> bool:
    """True when the extracted rulebook store exists on this deployment."""
    try:
        return bool(rules_lookup.valid_section_ids())
    except Exception:
        return False


def _read_image_as_data_url(image_path: str) -> str:
    """Decode a stored image file into a base64 data URL for the Responses API."""
    p = Path(image_path)
    if not p.is_absolute():
        p = Path("data/uploads") / image_path
    if not p.is_file():
        raise FileNotFoundError(f"Image not found: {p}")
    mime = _IMAGE_MIME_BY_EXT.get(p.suffix.lower())
    if not mime:
        raise ValueError(f"Unsupported image extension: {p.suffix}")
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def _build_multimodal_input(question: str, image_paths: List[str]) -> list:
    """Build the Responses API multipart input for one or more attached images.

    Order of input_image blocks: terrain legend first (if available, as a
    fixed visual reference), then each user image in the order pasted.
    """
    if not image_paths:
        raise ValueError("_build_multimodal_input requires at least one image_path")

    content: List[Dict[str, Any]] = [{"type": "input_text", "text": question}]
    if _TERRAIN_LEGEND_DATA_URL is not None:
        content.append({"type": "input_image", "image_url": _TERRAIN_LEGEND_DATA_URL, "detail": "high"})
    for path in image_paths:
        data_url = _read_image_as_data_url(path)
        content.append({"type": "input_image", "image_url": data_url, "detail": "high"})
    return [{"role": "user", "content": content}]


def _get(obj, key, default=None):
    """Read a field from a dict or an SDK object uniformly."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _output_function_calls(output) -> List[Dict[str, Any]]:
    """Pull function_call blocks out of a Responses API `output` list."""
    calls: List[Dict[str, Any]] = []
    for block in output or []:
        if _get(block, "type") == "function_call":
            calls.append({
                "call_id": _get(block, "call_id"),
                "name": _get(block, "name"),
                "arguments": _get(block, "arguments", "{}"),
            })
    return calls


def _extract_rag_sources_from_output(output) -> List[Dict[str, Any]]:
    """Extract file_search vector-store results from a Responses API `output` list."""
    rag_results: List[Dict[str, Any]] = []
    for item in output or []:
        if _get(item, "type") != "file_search_call":
            continue
        for r in _get(item, "results", []) or []:
            attributes = _get(r, "attributes", None)
            text = _get(r, "text", None) or _get(r, "content", None)
            rag_results.append({
                "index": len(rag_results) + 1,
                "file_id": _get(r, "file_id"),
                "score": _get(r, "score"),
                "content": text or "",
                "attributes": attributes if isinstance(attributes, dict) else (
                    attributes.__dict__ if hasattr(attributes, "__dict__") else None
                ),
                "filename": _get(r, "filename", None) or "Unknown",
            })
    return rag_results


_PUA_LO, _PUA_HI = 0xE000, 0xF8FF  # Unicode Basic Multilingual Plane Private Use Area


def _is_pua(ch: str) -> bool:
    return _PUA_LO <= ord(ch) <= _PUA_HI


class _CitationStripper:
    """Remove OpenAI file_search citation markers from a streamed token sequence.

    The Responses API emits inline citations like
    ``fileciteturn0file3`` — Private Use Area delimiter
    characters wrapping an alphanumeric citation token, with no embedded
    whitespace. Normal rulebook text never contains PUA characters, so a PUA
    char reliably marks the start of one. This is stateful so a marker split
    across streaming chunks is still removed; whitespace immediately preceding a
    marker is dropped so the text doesn't end up with double spaces.
    """

    def __init__(self) -> None:
        self._in_marker = False
        self._pending_ws = ""

    def feed(self, text: str) -> str:
        out: List[str] = []
        for ch in text:
            if self._in_marker:
                if _is_pua(ch) or ch.isalnum():
                    continue  # still inside the marker
                self._in_marker = False  # terminator — handle it below
            if _is_pua(ch):
                self._in_marker = True
                self._pending_ws = ""  # drop whitespace that preceded the marker
            elif ch.isspace():
                self._pending_ws += ch
            else:
                if self._pending_ws:
                    out.append(self._pending_ws)
                    self._pending_ws = ""
                out.append(ch)
        return "".join(out)

    def flush(self) -> str:
        ws, self._pending_ws = self._pending_ws, ""
        return ws


def _strip_citation_markers(text: str) -> str:
    """One-shot citation-marker strip for non-streamed (complete) text."""
    if not text:
        return text
    s = _CitationStripper()
    return s.feed(text) + s.flush()


# Per-model OpenRouter defaults for models that are unusable interactively at
# their out-of-the-box settings. Applied when no env override is set, so a model
# is safe by default without depending on server env config. Env vars
# (OPENROUTER_REASONING_* / OPENROUTER_PROVIDER_*) still override these globally.
#
# z-ai/glm-5.2: a reasoning model that, unbounded, emits ~65k hidden reasoning
# tokens and stalls ~13 min/question; OpenRouter also spreads it across ~24
# providers of uneven speed (slow ones cause connection drops / multi-minute
# hangs). effort=low keeps answers fast and accurate; sort=throughput steers to
# fast, reliable providers.
_MODEL_OPENROUTER_DEFAULTS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "z-ai/glm-5.2": {
        "reasoning": {"effort": "low"},
        "provider": {"sort": "throughput"},
    },
}


def _openrouter_reasoning_config(model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Build OpenRouter's `reasoning` control from env, a per-model default, or None.

    Reasoning models (e.g. z-ai/glm-5.2) reason at full effort by default,
    emitting tens of thousands of hidden reasoning tokens per question — minutes
    of latency that `max_tokens` does not bound. Set one of:
      * OPENROUTER_REASONING_EFFORT     = low | medium | high
      * OPENROUTER_REASONING_MAX_TOKENS = <int>   (hard reasoning-token cap)
      * OPENROUTER_REASONING_ENABLED    = false   (disable reasoning entirely)
    Effort takes precedence, then max_tokens, then the disable flag. With no env
    set, falls back to the per-model default in _MODEL_OPENROUTER_DEFAULTS (so
    known reasoning-heavy models stay usable without server env config), else
    None => the model's default reasoning behavior (unchanged).
    """
    effort = os.getenv("OPENROUTER_REASONING_EFFORT")
    if effort:
        return {"effort": effort.strip().lower()}
    max_tokens = os.getenv("OPENROUTER_REASONING_MAX_TOKENS")
    if max_tokens:
        return {"max_tokens": int(max_tokens)}
    if os.getenv("OPENROUTER_REASONING_ENABLED", "").strip().lower() == "false":
        return {"enabled": False}
    default = _MODEL_OPENROUTER_DEFAULTS.get(model or "", {}).get("reasoning")
    return dict(default) if default else None


def _openrouter_provider_config(model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Build OpenRouter's `provider` routing control from env, a per-model default, or None.

    OpenRouter load-balances a model across many providers of varying speed and
    reliability; the slow/flaky ones cause minute-long stalls and connection
    drops. Pin or sort to avoid them:
      * OPENROUTER_PROVIDER_ORDER = comma-separated slugs (e.g. "deepinfra,novita")
                                    => {"order": [...], "allow_fallbacks": True}
      * OPENROUTER_PROVIDER_SORT  = price | throughput | latency
    Both may be set; order is applied first, then sort across the rest. With no
    env set, falls back to the per-model default in _MODEL_OPENROUTER_DEFAULTS.
    """
    cfg: Dict[str, Any] = {}
    order = os.getenv("OPENROUTER_PROVIDER_ORDER")
    if order:
        cfg["order"] = [s.strip() for s in order.split(",") if s.strip()]
        cfg["allow_fallbacks"] = True
    sort = os.getenv("OPENROUTER_PROVIDER_SORT")
    if sort:
        cfg["sort"] = sort.strip().lower()
    if cfg:
        return cfg
    default = _MODEL_OPENROUTER_DEFAULTS.get(model or "", {}).get("provider")
    return dict(default) if default else None


class ASLService:
    """Service for getting ASL rule answers via Responses API."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        vector_store_id: Optional[str] = None,
        config_file: Optional[str] = None
    ):
        """
        Initialize ASL Service.
        
        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            vector_store_id: Vector store ID (defaults to loading from config file)
            config_file: Path to responses_api_config.json (defaults to ./responses_api_config.json)
        """
        self.config = load_asl_config(api_key, vector_store_id, config_file)
        self.client = OpenAIResponsesClient(self.config)
        # Plain OpenAI client for client-side vector-store search (used when
        # the LLM call goes through OpenRouter and we can't piggyback on the
        # Responses API's server-side file_search).
        self.retrieval_client = OpenAI(api_key=self.config.api_key)
        # OpenRouter client — None if OPENROUTER_API_KEY isn't set.
        # Selecting a "/" model when this is None raises a clear error.
        self.openrouter_client = build_openrouter_client_from_env()

        logging.info(f"ASL Service initialized with vector store: {self.config.vector_store_id}")
        if self.openrouter_client:
            logging.info("OpenRouter client initialized (/-prefixed model names route here)")
    
    def _verify_answer(
        self, 
        question: str, 
        initial_answer: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> str:
        """
        Verify and potentially correct an initial answer.
        
        Args:
            question: The original question
            initial_answer: The initial answer to verify
            model: Model to use for verification
            temperature: Temperature for verification (use 0 for deterministic)
            
        Returns:
            Verified/corrected answer
        """
        verification_prompt = f"""VERIFICATION TASK

Original Question: {question}

Initial Answer: {initial_answer}

Your task is to verify this answer for completeness and correctness. Check:

1. COMPLETENESS - Did the answer consider ALL applicable modifiers?
   - For blind hexes: elevation advantage, range, obstacle height
   - For DRMs: terrain, leadership, range, unit status, special cases
   - For Residual FP: division by 2, column shifts for hindrances
   - For any calculation: all relevant rules and exceptions

2. CALCULATION ACCURACY - Were all math steps shown and correct?
   - Check each arithmetic operation
   - Verify column shifts were applied correctly
   - Confirm division/multiplication steps

3. RULE APPLICATION - Were rules cited and applied correctly?
   - Check section references are accurate
   - Verify rule interpretation matches the question
   - Look for missing or misapplied rules

4. PERSPECTIVE/DIRECTION - Was the question answered from the correct viewpoint?
   - Attacking FROM vs being attacked IN
   - Firer vs target
   - Moving unit vs stationary unit

If you find ANY errors or omissions, provide the CORRECTED answer with:
- Explanation of what was wrong/missing
- Complete corrected calculation
- Final corrected answer

If the answer is correct and complete, respond with:
"VERIFIED: The initial answer is correct and complete."

Your response:"""

        logging.info("🔍 Running verification pass...")
        
        try:
            response = self.client.create_response(
                model=model or self.config.model,
                input=verification_prompt,
                instructions=self.config.system_instructions,
                temperature=0.0,  # Use 0 for deterministic verification
                stream=False,
                tools=[{
                    "type": "file_search",
                    "vector_store_ids": self.config.all_vector_store_ids,
                }]
            )
            
            verified_answer = _strip_citation_markers(extract_response_text(response))
            
            # Check if verification found issues
            if "VERIFIED:" in verified_answer and "correct and complete" in verified_answer.lower():
                logging.info("✅ Verification passed - initial answer is correct")
                return initial_answer
            else:
                logging.info("⚠️ Verification found issues - using corrected answer")
                return verified_answer
                
        except Exception as e:
            logging.error(f"❌ Verification failed: {e}")
            # Fall back to initial answer if verification fails
            return initial_answer
    
    def get_answer(
        self,
        question: str,
        stream: bool = False,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        return_timing: bool = False,
        force_web_search: bool = False,
        use_verification: bool = False,
        use_agentic: bool = False,
        max_chunks: Optional[int] = None,
        image_paths: Optional[List[str]] = None,
        board_state: Optional[str] = None,
        vsav_state: Optional[Dict[str, Any]] = None,
        force_tool: Optional[str] = None,
        auto_route_tools: bool = False,
        route_model: str = "gpt-4.1-mini",
        use_cite_check: bool = False,
    ):
        """Public entry point. See _get_answer_impl for the full contract.

        use_cite_check adds the deterministic cite-check pass (non-streaming
        only): code extracts the sections the draft answer cites, fetches
        their exact text + Q&A via rules_lookup, and one forced revision turn
        corrects the draft against them (docs/agentic_retrieval_plan.md §3.4).
        On any cite-check failure the draft is returned unchanged.
        """
        if use_cite_check and stream:
            raise ValueError(
                "use_cite_check is only supported in non-streaming mode (stream=False)"
            )
        result = self._get_answer_impl(
            question=question,
            stream=stream,
            model=model,
            temperature=temperature,
            return_timing=return_timing,
            force_web_search=force_web_search,
            use_verification=use_verification,
            use_agentic=use_agentic,
            max_chunks=max_chunks,
            image_paths=image_paths,
            board_state=board_state,
            vsav_state=vsav_state,
            force_tool=force_tool,
            auto_route_tools=auto_route_tools,
            route_model=route_model,
        )
        if not use_cite_check:
            return result
        resolved_model = model or self.config.model
        if isinstance(result, tuple):
            text, timing = result
            revised, info = self._apply_cite_check(question, text, resolved_model, temperature)
            if isinstance(timing, dict):
                timing["cite_check"] = info
            return revised, timing
        revised, _info = self._apply_cite_check(question, result, resolved_model, temperature)
        return revised

    def _apply_cite_check(
        self,
        question: str,
        draft: str,
        model: str,
        temperature: Optional[float],
    ) -> Tuple[str, Dict[str, Any]]:
        """Run the deterministic cite-check revision turn on a drafted answer.

        Never raises: any failure returns the draft unchanged with the error
        recorded in the info dict.
        """
        from app.asl.cite_check import build_cite_check_context, build_revision_prompt

        info: Dict[str, Any] = {"applied": False}
        try:
            ctx = build_cite_check_context(draft or "")
            info.update({k: ctx[k] for k in ("cited", "cross_refs", "dropped", "missing")})
            if not ctx["sections"]:
                info["skipped"] = "draft cites no known sections"
                logging.info("🔎 cite-check skipped: draft cites no known sections")
                return draft, info

            prompt = build_revision_prompt(question, draft, ctx)
            t0 = time.time()
            if "/" in str(model):
                if self.openrouter_client is None:
                    raise RuntimeError("OpenRouter client unavailable for cite-check")
                resp = self.openrouter_client.create_chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": self.config.system_instructions},
                        {"role": "user", "content": prompt},
                    ],
                    stream=False,
                    temperature=temperature,
                    max_tokens=int(os.getenv("OPENROUTER_MAX_TOKENS", "8192")),
                    reasoning=_openrouter_reasoning_config(model),
                    provider=_openrouter_provider_config(model),
                )
                revised = (resp.choices[0].message.content or "").strip()
            else:
                supports_temp = not str(model).startswith("gpt-5")
                resp = self.client.create_response(
                    model=model,
                    input=prompt,
                    instructions=self.config.system_instructions,
                    stream=False,
                    tools=[],
                    temperature=(temperature if (supports_temp and temperature is not None) else None),
                )
                revised = _strip_citation_markers(extract_response_text(resp)).strip()

            info["applied"] = True
            info["revised"] = bool(revised) and revised != (draft or "").strip()
            info["ms"] = round((time.time() - t0) * 1000, 1)
            logging.info(
                "🔎 cite-check: %d sections (%d cross-refs), revised=%s (%sms)",
                len(ctx["sections"]), len(ctx["cross_refs"]), info["revised"], info["ms"],
            )
            return (revised or draft), info
        except Exception as e:
            logging.error("cite-check failed; keeping draft: %s", e)
            info["error"] = str(e)
            return draft, info

    def _get_answer_impl(
        self,
        question: str,
        stream: bool = False,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        return_timing: bool = False,
        force_web_search: bool = False,
        use_verification: bool = False,
        use_agentic: bool = False,
        max_chunks: Optional[int] = None,
        image_paths: Optional[List[str]] = None,
        board_state: Optional[str] = None,
        vsav_state: Optional[Dict[str, Any]] = None,
        force_tool: Optional[str] = None,
        auto_route_tools: bool = False,
        route_model: str = "gpt-4.1-mini",
    ):
        """
        Get an answer to an ASL question.
        
        Args:
            question: The ASL question to ask
            stream: Whether to stream the response (returns generator if True)
            model: Override default model
            temperature: Override default temperature
            return_timing: If True and stream=True, returns tuple (generator, timing_data)
            force_web_search: If True, emphasizes web search usage in instructions
            use_verification: If True, uses two-pass verification to check answer
            use_agentic: If True, exposes the IFT function tools. Works in
                both streaming and non-streaming modes (streaming resolves tool
                calls, then streams the final answer).
            board_state: Optional rendered BOARD STATE text block (from
                vsav_service.render_board_state). Appended to the question
                text and accompanied by VSAV_INSTRUCTIONS_ADDENDUM. Plain
                text, so it works on every model path (incl. OpenRouter).
            vsav_state: Optional PARSED .vsav state dict (from
                vsav_service.parse_vsav). Never sent to the model; threaded
                into agentic tool execution so the resolve_attack tool can
                derive attacks from exact board state server-side.

        Returns:
            The answer as a string (or generator if stream=True)
            If return_timing=True and stream=True, returns (generator, timing_data)

        Note:
            use_verification requires stream=False.
        """
        if not question or not question.strip():
            raise ValueError("Question cannot be empty")
        
        # Validation for special modes
        if use_verification and stream:
            raise ValueError("Verification is only supported in non-streaming mode (stream=False)")
        
        model = model or self.config.model
        temperature = temperature if temperature is not None else self.config.temperature

        # Inject parsed .vsav board state as plain text. Folding it into the
        # question (rather than a separate content block) means every path —
        # OpenAI streaming/agentic, multimodal, and OpenRouter — sees it.
        if board_state:
            question = f"{question}\n\n{board_state}"

        # Build instructions
        instructions = build_instructions(
            self.config.system_instructions,
            question,
            force_web_search=force_web_search
        )
        if image_paths:
            instructions = instructions + VISION_INSTRUCTIONS_ADDENDUM
        if board_state:
            instructions = instructions + VSAV_INSTRUCTIONS_ADDENDUM
        # Deterministic lookup tools ride with agentic mode, but only when the
        # extracted rulebook store is built on this deployment.
        lookup_enabled = use_agentic and _lookup_tools_available()
        if lookup_enabled:
            instructions = instructions + CITE_VERIFICATION_ADDENDUM

        # Build input — multimodal if image(s) attached, else plain string
        if image_paths:
            api_input = _build_multimodal_input(question, image_paths)
            logging.info(f"🖼️  Multimodal input built for {len(image_paths)} image(s): {image_paths}")
        else:
            api_input = question
        
        # Server-side tool-execution context: never model-controlled. The
        # retrieval client + store IDs power the search_rules tool; vsav_state
        # powers resolve_attack/resolve_cc.
        tool_context: Dict[str, Any] = {
            "retrieval_client": self.retrieval_client,
            "vector_store_ids": self.config.all_vector_store_ids,
        }
        if vsav_state:
            tool_context["vsav_state"] = vsav_state

        # Start timing for RAG latency measurement
        api_call_start_time = time.time()
        logging.info(f"[RAG Latency] Question: {question[:100]}{'...' if len(question) > 100 else ''}")
        logging.info(f"[RAG Latency] API call started at: {api_call_start_time:.3f}")
        
        try:
            num_chunks = max_chunks if max_chunks is not None else int(os.getenv("RAG_MAX_CHUNKS", "20"))

            # OpenRouter path: model names like "deepseek/deepseek-v3.2" go here.
            # We do retrieval client-side via the OpenAI vector store, bake the
            # chunks into the system prompt, and call OpenRouter for inference.
            # Always non-streaming for now — the chat WebSocket gets the full
            # answer as one delta. Image inputs aren't supported on this path.
            if "/" in model:
                if self.openrouter_client is None:
                    raise RuntimeError(
                        f"Model '{model}' requires OpenRouter, but OPENROUTER_API_KEY "
                        "is not set on this deployment."
                    )
                if image_paths:
                    raise ValueError(
                        f"Model '{model}' (OpenRouter) does not support image inputs."
                    )
                if use_verification:
                    raise ValueError(
                        "use_verification is not supported on the OpenRouter path."
                    )
                if use_agentic:
                    # Agentic OpenRouter: same IFT/CC calculator loop the OpenAI
                    # path uses, but over Chat Completions function calling. The
                    # auto-router forces the right calculator on the first turn
                    # (these models, like gpt-5.4, rarely call it unprompted).
                    #
                    # When auto-routing, the classifier decides per question:
                    #   ift_attack / cc_attack → calc + lookup tools, force that one
                    #   none                   → lookup tools only, no calc, no force
                    # Lookup tools (get_section + search_rules) are exposed on
                    # every agentic call when the extracted store is built.
                    tools_chat = calc_tool_schemas(chat=True)
                    if auto_route_tools and not force_tool:
                        from app.asl.tool_router import classify_tool
                        force_tool = classify_tool(question, model=route_model)
                        if force_tool:
                            logging.info(f"🧭 Auto-routed to tool: {force_tool}")
                        else:
                            tools_chat = []
                            logging.info("🧭 Auto-routed to: none (no calculators forced)")
                    if lookup_enabled:
                        tools_chat = tools_chat + lookup_tool_schemas(chat=True)
                    return self._openrouter_agentic_answer(
                        question=question,
                        model=model,
                        temperature=temperature,
                        instructions=instructions,
                        num_chunks=num_chunks,
                        api_call_start_time=api_call_start_time,
                        return_timing=return_timing,
                        force_tool=force_tool,
                        tool_context=tool_context,
                        tools_chat=tools_chat,
                        max_iterations=MAX_ITER_WITH_LOOKUP if lookup_enabled else MAX_ITER_DEFAULT,
                    )
                return self._openrouter_answer(
                    question=question,
                    model=model,
                    temperature=temperature,
                    instructions=instructions,
                    num_chunks=num_chunks,
                    api_call_start_time=api_call_start_time,
                    stream=stream,
                    return_timing=return_timing,
                )

            # Build tools - base tools
            tools = [
                {
                    "type": "file_search",
                    "vector_store_ids": self.config.all_vector_store_ids,
                    "max_num_results": num_chunks
                }
            ]
            
            # Add function tools if agentic mode is enabled. OpenAI path gets
            # the calculators plus get_section — but NOT search_rules, because
            # the hosted file_search tool already covers mid-loop search here.
            if use_agentic:
                tools.extend(calc_tool_schemas())
                if lookup_enabled:
                    tools.extend(lookup_tool_schemas(include_search=False))
                logging.info(f"🤖 Agentic mode enabled - added {len(tools) - 1} function tools"
                             + (" (with parsed .vsav state in tool context)" if vsav_state else "")
                             + (" (+ get_section lookup)" if lookup_enabled else ""))
                # Auto-route calc questions to the right calculator (force it on
                # the first turn). gpt-5.4 rarely calls these tools on its own.
                if auto_route_tools and not force_tool:
                    from app.asl.tool_router import classify_tool
                    force_tool = classify_tool(question, model=route_model)
                    if force_tool:
                        logging.info(f"🧭 Auto-routed to tool: {force_tool}")
            
            # Build common API kwargs — some models (e.g. gpt-5-mini) don't support temperature
            api_kwargs = {
                "model": model,
                "input": api_input,
                "instructions": instructions,
                "tools": tools,
            }
            # Only include temperature for models that support it. The whole
            # GPT-5 family only supports the default temperature; sending the
            # param errors (create_response/agentic both omit a None temp).
            _no_temp_models = {"gpt-5-mini", "gpt-5-mini-2025-08-07", "gpt-5.4-mini"}
            supports_temp = not str(model).startswith("gpt-5") and model not in _no_temp_models
            effective_temp = temperature if supports_temp else None
            if effective_temp is not None:
                api_kwargs["temperature"] = effective_temp

            if stream:
                if use_agentic:
                    # Resolve tool calls, then stream the final answer. Same
                    # (generator, timing_data) contract as the plain stream path.
                    return self._handle_agentic_streaming_response(
                        input_data=api_input,
                        instructions=instructions,
                        model=model,
                        temperature=api_kwargs.get("temperature"),
                        tools=tools,
                        api_call_start_time=api_call_start_time,
                        return_timing=return_timing,
                        tool_context=tool_context,
                        max_iterations=MAX_ITER_WITH_LOOKUP if lookup_enabled else MAX_ITER_DEFAULT,
                    )
                # Use stream_response for true streaming with final response access
                stream_manager = self.client.stream_response(**api_kwargs)
                return self._handle_streaming_response(
                    stream_manager,
                    api_call_start_time,
                    return_timing
                )
            else:
                # Non-streaming mode
                if use_agentic:
                    # Use agentic handler with tool execution loop
                    return self._handle_agentic_response(
                        question=question,
                        instructions=instructions,
                        model=model,
                        temperature=effective_temp,
                        tools=tools,
                        api_call_start_time=api_call_start_time,
                        use_verification=use_verification,
                        tool_context=tool_context,
                        return_timing=return_timing,
                        force_tool=force_tool,
                        max_iterations=MAX_ITER_WITH_LOOKUP if lookup_enabled else MAX_ITER_DEFAULT,
                    )
                else:
                    # Standard non-streaming
                    api_kwargs["stream"] = False
                    response = self.client.create_response(**api_kwargs)
                    return self._handle_non_streaming_response(
                        response,
                        api_call_start_time,
                        question,
                        model,
                        temperature,
                        use_verification
                    )
                
        except Exception as e:
            error_msg = f"Error getting response: {str(e)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _openrouter_answer(
        self,
        question: str,
        model: str,
        temperature: float,
        instructions: str,
        num_chunks: int,
        api_call_start_time: float,
        stream: bool,
        return_timing: bool,
    ):
        """
        OpenRouter path: client-side retrieval + non-streaming inference.

        Returns the same shape the OpenAI path returns:
          * stream=True  → (generator, timing_data). The generator yields the
                           whole answer as ONE delta (we're non-streaming under
                           the hood; the chat WebSocket sees a single chunk
                           after the call completes).
          * stream=False → answer string.

        timing_data is populated *before* the generator is consumed (the
        whole call is synchronous), unlike the OpenAI streaming path where
        timing_data fills in during iteration.
        """
        # 1. Retrieval — OpenAI vector store search.
        retrieval_start = time.time()
        chunks = retrieve_chunks(
            self.retrieval_client,
            self.config.all_vector_store_ids,
            query=question,
            max_results_per_store=num_chunks,
        )
        context_block = format_chunks_as_context(chunks)
        retrieval_ms = (time.time() - retrieval_start) * 1000
        logging.info(f"[RAG Latency] OpenRouter retrieval: {retrieval_ms:.1f}ms ({len(chunks)} chunks)")

        # 2. Build messages with retrieved context baked into the system prompt.
        sys_with_context = instructions
        if context_block:
            sys_with_context = (
                instructions
                + "\n\nUse the following retrieved rulebook excerpts as your "
                "primary source. Cite rule sections (e.g., A6.4) from these "
                "excerpts in your answer.\n\n"
                + context_block
            )
        messages = [
            {"role": "system", "content": sys_with_context},
            {"role": "user", "content": question},
        ]

        # 3. Inference via OpenRouter.
        inference_start = time.time()
        # Cap max_tokens: OpenRouter pre-authorizes max_tokens × the model's
        # output rate against the credit balance, and an unset cap means the
        # model's full ceiling (65K on some models) — which 402s on expensive
        # models even though real answers are ~1K tokens.
        response = self.openrouter_client.create_chat(
            model=model,
            messages=messages,
            stream=False,
            temperature=temperature,
            max_tokens=int(os.getenv("OPENROUTER_MAX_TOKENS", "8192")),
            reasoning=_openrouter_reasoning_config(model),
            provider=_openrouter_provider_config(model),
        )
        inference_ms = (time.time() - inference_start) * 1000
        total_ms = retrieval_ms + inference_ms
        logging.info(
            f"[RAG Latency] OpenRouter inference: {inference_ms:.1f}ms · total {total_ms:.1f}ms"
        )

        text = (response.choices[0].message.content or "").strip()
        usage = getattr(response, "usage", None)
        # OpenRouter normalizes to OpenAI's prompt_tokens/completion_tokens, but
        # some upstream providers (notably Anthropic) leak through with their
        # native input_tokens/output_tokens field names. Accept either.
        def _u(field_openai, field_anthropic):
            if usage is None:
                return 0
            for name in (field_openai, field_anthropic):
                v = getattr(usage, name, None)
                if v is None and isinstance(usage, dict):
                    v = usage.get(name)
                if v:
                    return v
            return 0
        input_tokens = _u("prompt_tokens", "input_tokens")
        output_tokens = _u("completion_tokens", "output_tokens")
        if usage is not None and input_tokens == 0 and output_tokens == 0:
            logging.warning(f"OpenRouter usage missing tokens — raw usage: {usage!r}")
        else:
            logging.info(f"📊 Tokens (OpenRouter): {input_tokens} in / {output_tokens} out")

        timing_data: Dict[str, Any] = {
            "retrieval_ms": round(retrieval_ms, 1),
            "inference_ms": round(inference_ms, 1),
            # Aliases to keep the existing UI / persistence layer working —
            # the latency-row JS reads file_search_time_ms for the RAG chip.
            "file_search_time_ms": round(retrieval_ms, 1),
            "ttft_ms": round(total_ms, 1),     # non-streaming: TTFT == TOTAL
            "total_time_ms": round(total_ms, 1),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "rag_chunks": len(chunks),
            # No file_citation metadata from OpenRouter; rule references will
            # still be clickable client-side via makeSectionReferencesClickable.
            "rag_sources": [],
        }

        if stream:
            def one_shot_generator():
                yield text
            if return_timing:
                return one_shot_generator(), timing_data
            return one_shot_generator(), []

        return text

    def _openrouter_agentic_answer(
        self,
        question: str,
        model: str,
        temperature: float,
        instructions: str,
        num_chunks: int,
        api_call_start_time: float,
        return_timing: bool,
        force_tool: Optional[str] = None,
        tool_context: Optional[Dict[str, Any]] = None,
        max_iterations: int = 5,
        tools_chat: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Agentic OpenRouter path: client-side retrieval + a Chat Completions
        tool-calling loop.

        tools_chat is the Chat-format tool list to expose (None falls back to
        TOOL_SCHEMAS_CHAT, i.e. everything). An empty list makes this a plain
        RAG call (no tools offered) while keeping the same (text, timing_data)
        return contract.

        Mirrors `_handle_agentic_response` (the OpenAI Responses path) but over
        OpenRouter's Chat Completions API, so a `/`-named model (e.g.
        "z-ai/glm-5.2") gets the same tool-assisted accuracy as gpt-5.4. The
        force_tool / tool_context contract and the return shape match: a string,
        or — when return_timing=True — a (text, timing_data) tuple whose keys
        line up with what the eval harness reads (input/output tokens,
        retrieval/inference split, tools_called).

        Always non-streaming: the loop must see each turn's tool calls before it
        can emit the final answer.
        """
        import json as json_module

        # 1. Retrieval — client-side, identical to the plain OpenRouter path.
        retrieval_start = time.time()
        chunks = retrieve_chunks(
            self.retrieval_client,
            self.config.all_vector_store_ids,
            query=question,
            max_results_per_store=num_chunks,
        )
        context_block = format_chunks_as_context(chunks)
        retrieval_ms = (time.time() - retrieval_start) * 1000
        logging.info(
            f"[RAG Latency] OpenRouter(agentic) retrieval: {retrieval_ms:.1f}ms "
            f"({len(chunks)} chunks)"
        )

        sys_with_context = instructions
        if context_block:
            sys_with_context = (
                instructions
                + "\n\nUse the following retrieved rulebook excerpts as your "
                "primary source. Cite rule sections (e.g., A6.4) from these "
                "excerpts in your answer.\n\n"
                + context_block
            )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": sys_with_context},
            {"role": "user", "content": question},
        ]

        max_tokens = int(os.getenv("OPENROUTER_MAX_TOKENS", "8192"))
        reasoning = _openrouter_reasoning_config(model)
        provider = _openrouter_provider_config(model)
        total_input_tokens = 0
        total_output_tokens = 0
        tools_called: List[str] = []
        final_text = ""

        inference_start = time.time()
        for iteration in range(max_iterations):
            logging.info(f"🔄 OpenRouter agentic iteration {iteration + 1}/{max_iterations}")

            # Force the named function on the FIRST turn only; later turns use
            # "auto" so the model can stop calling tools and emit the answer.
            call_kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "reasoning": reasoning,
                "provider": provider,
            }
            exposed = TOOL_SCHEMAS_CHAT if tools_chat is None else tools_chat
            if exposed:
                call_kwargs["tools"] = exposed
                call_kwargs["tool_choice"] = (
                    {"type": "function", "function": {"name": force_tool}}
                    if force_tool and iteration == 0 else "auto"
                )
            response = self.openrouter_client.create_chat(**call_kwargs)

            usage = getattr(response, "usage", None)
            if usage is not None:
                total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                total_output_tokens += getattr(usage, "completion_tokens", 0) or 0

            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []

            # No tool calls → final answer.
            if not tool_calls:
                final_text = (msg.content or "").strip()
                logging.info(
                    f"✅ OpenRouter agentic loop completed after {iteration + 1} iteration(s)"
                )
                break

            # Append the assistant turn (with its tool_calls) verbatim, then a
            # tool message per call — the Chat Completions tool protocol.
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })
            logging.info(f"🔧 Executing {len(tool_calls)} tool call(s)...")
            for tc in tool_calls:
                name = tc.function.name
                args_raw = tc.function.arguments
                tools_called.append(name)
                try:
                    args = json_module.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                    logging.info(f"  📞 {name}({args})")
                    result = execute_tool(name, args, context=tool_context)
                    output = json_module.dumps(result)
                    logging.info(f"  ✅ Result: {output[:100]}...")
                except Exception as e:
                    logging.error(f"  ❌ Tool error: {e}")
                    output = json_module.dumps({"error": str(e)})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,
                })
        else:
            logging.warning("⚠️ OpenRouter agentic max iterations reached")
            # final_text stays as the last assistant content (possibly empty).

        inference_ms = (time.time() - inference_start) * 1000
        total_ms = retrieval_ms + inference_ms
        logging.info(
            f"[RAG Latency] OpenRouter(agentic) inference: {inference_ms:.1f}ms · "
            f"total {total_ms:.1f}ms · tools={tools_called or 'none'}"
        )
        if total_input_tokens or total_output_tokens:
            logging.info(
                f"📊 Tokens (OpenRouter agentic): {total_input_tokens} in / "
                f"{total_output_tokens} out"
            )

        final_text = _strip_citation_markers(final_text)
        if not return_timing:
            return final_text
        return final_text, {
            "response_time_ms": round(total_ms, 1),
            "retrieval_ms": round(retrieval_ms, 1),
            "inference_ms": round(inference_ms, 1),
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "tools_called": tools_called,
        }

    def _handle_streaming_response(
        self,
        stream_manager,
        api_call_start_time: float,
        return_timing: bool
    ) -> Tuple[Generator[str, None, None], Optional[Dict[str, Any]]]:
        """
        Handle streaming response (true streaming + final response capture).
        
        Returns:
            Tuple of (stream_generator, timing_data) if return_timing=True
            Otherwise (stream_generator, empty list)
        """
        def _extract_rag_sources_from_final(final_response) -> list:
            """Extract vector store results from final response.output."""
            output = getattr(final_response, "output", None)
            if not output:
                return []

            def _get(obj, key, default=None):
                if isinstance(obj, dict):
                    return obj.get(key, default)
                return getattr(obj, key, default)

            rag_results: list = []
            for item in output:
                if _get(item, "type") != "file_search_call":
                    continue
                results = _get(item, "results", []) or []
                for r in results:
                    # openai-python Result fields:
                    # - text: retrieved chunk text
                    # - filename: original uploaded filename (if available)
                    # - attributes: metadata dict (if any)
                    attributes = _get(r, "attributes", None)
                    filename = _get(r, "filename", None)
                    text = _get(r, "text", None)
                    # Backwards/alternate field fallbacks
                    if text is None:
                        text = _get(r, "content", None)

                    rag_results.append(
                        {
                            "index": len(rag_results) + 1,
                            "file_id": _get(r, "file_id"),
                            "score": _get(r, "score"),
                            "content": text or "",
                            "attributes": attributes if isinstance(attributes, dict) else (attributes.__dict__ if hasattr(attributes, "__dict__") else None),
                            "filename": filename or "Unknown",
                        }
                    )
            return rag_results

        # Use a mutable dict to capture timing data from the generator closure
        timing_data: Dict[str, Any] = {} if return_timing else {}

        def stream_generator():
            first_event_time = None
            file_search_complete_time = None
            first_delta_time = None
            stripper = _CitationStripper()

            with stream_manager as stream:
                for event in stream:
                    if first_event_time is None:
                        first_event_time = time.time()
                        first_event_ms = (first_event_time - api_call_start_time) * 1000
                        logging.info(
                            f"[RAG Latency] First event received: {first_event_ms:.1f}ms (type: {getattr(event, 'type', 'unknown')})"
                        )

                    if file_search_complete_time is None and hasattr(event, 'type') and event.type == 'response.file_search_call.completed':
                        file_search_complete_time = time.time()
                        file_search_time_ms = (file_search_complete_time - api_call_start_time) * 1000
                        logging.info(f"[RAG Latency] File search completed: {file_search_time_ms:.1f}ms")
                    
                    if first_delta_time is None and hasattr(event, 'type') and event.type == 'response.output_text.delta':
                        first_delta_time = time.time()
                        ttft_ms = (first_delta_time - api_call_start_time) * 1000
                        logging.info(f"[RAG Latency] First token (TTFT): {ttft_ms:.1f}ms")
                    
                    # Yield deltas immediately for true streaming (citation
                    # markers stripped on the way out).
                    if hasattr(event, 'type') and event.type == 'response.output_text.delta':
                        delta = getattr(event, 'delta', None)
                        if delta:
                            cleaned = stripper.feed(delta)
                            if cleaned:
                                yield cleaned

                # Flush any whitespace held back by the stripper.
                tail = stripper.flush()
                if tail:
                    yield tail

                # After stream completes, extract RAG sources from final response
                stream_end_time = time.time()
                if return_timing:
                    timing_data.update(compute_timing_metrics(
                        api_call_start_time,
                        first_event_time,
                        file_search_complete_time,
                        first_delta_time,
                        stream_end_time
                    ))

                    try:
                        final = stream.get_final_response()
                        rag_sources = _extract_rag_sources_from_final(final)
                        timing_data["rag_sources"] = rag_sources
                        logging.info(f"📚 Extracted {len(rag_sources)} RAG sources from final response")

                        # Extract token usage
                        if hasattr(final, 'usage') and final.usage:
                            timing_data["input_tokens"] = getattr(final.usage, 'input_tokens', 0)
                            timing_data["output_tokens"] = getattr(final.usage, 'output_tokens', 0)
                            logging.info(f"📊 Tokens: {timing_data['input_tokens']} in / {timing_data['output_tokens']} out")
                    except Exception as e:
                        logging.warning(f"⚠️ Failed to extract RAG sources from final response: {e}", exc_info=True)
                        timing_data["rag_sources"] = []

        generator = stream_generator()
        if return_timing:
            # Note: timing_data will be populated after generator is fully consumed
            # The caller must consume the generator first, then timing_data will be available
            return generator, timing_data
        return generator, []
    
    def _handle_agentic_streaming_response(
        self,
        input_data,
        instructions: str,
        model: str,
        temperature: Optional[float],
        tools: List[Dict[str, Any]],
        api_call_start_time: float,
        return_timing: bool,
        max_iterations: int = 5,
        tool_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Generator[str, None, None], Any]:
        """
        Agentic answer that preserves streaming: resolve any tool calls, then
        stream the final answer.

        Each turn is streamed. Turns where the model calls a function carry no
        user-visible text (the model emits function_call items, not prose), so
        forwarding output_text deltas as they arrive yields a clean
        "tools resolve, then the answer streams" experience. After a turn, any
        function_call blocks are executed locally and their outputs submitted
        via previous_response_id; a turn with no function calls is the final
        answer.

        Returns (generator, timing_data) — same contract as
        _handle_streaming_response: timing_data fills in once the generator is
        fully consumed.

        The generator yields two item types: str (answer text deltas) and
        dict ({"status": <user-facing progress label>}) at phase boundaries
        (turn start, each tool call). Consumers that only want text must
        filter dicts.
        """
        import json as json_module

        timing_data: Dict[str, Any] = {}

        def stream_generator():
            prev_id: Optional[str] = None
            current_input = input_data
            stripper = _CitationStripper()
            first_delta_time: Optional[float] = None
            file_search_complete_time: Optional[float] = None
            total_input_tokens = 0
            total_output_tokens = 0
            rag_sources: List[Dict[str, Any]] = []
            tools_called: List[str] = []

            for iteration in range(max_iterations):
                # Progress event for the UI pill. Only turn 0 sets a label
                # here — later turns keep the batch tool label yielded below,
                # so "Checking rules …" stays up while the model reads the
                # tool results (the tools themselves finish in ~1ms).
                if iteration == 0:
                    yield {"status": "Searching the rulebook"}
                stream_manager = self.client.stream_response(
                    model=model,
                    input=current_input,
                    instructions=instructions,
                    temperature=temperature,
                    tools=tools,
                    previous_response_id=prev_id,
                )
                with stream_manager as stream:
                    for event in stream:
                        etype = getattr(event, "type", None)
                        if file_search_complete_time is None and etype == "response.file_search_call.completed":
                            file_search_complete_time = time.time()
                        if etype == "response.output_text.delta":
                            delta = getattr(event, "delta", None)
                            if delta:
                                if first_delta_time is None:
                                    first_delta_time = time.time()
                                cleaned = stripper.feed(delta)
                                if cleaned:
                                    yield cleaned
                    final = stream.get_final_response()

                prev_id = getattr(final, "id", None)
                usage = getattr(final, "usage", None)
                if usage:
                    total_input_tokens += getattr(usage, "input_tokens", 0) or 0
                    total_output_tokens += getattr(usage, "output_tokens", 0) or 0
                output = getattr(final, "output", []) or []
                rag_sources.extend(_extract_rag_sources_from_output(output))

                calls = _output_function_calls(output)
                if not calls:
                    logging.info("🤖 Agentic(stream) finished after %d iteration(s)", iteration + 1)
                    tail = stripper.flush()
                    if tail:
                        yield tail
                    break

                tools_called.extend(c["name"] for c in calls)
                logging.info(
                    "🔧 Agentic(stream) iter %d: executing %d tool call(s): %s",
                    iteration + 1, len(calls), [c["name"] for c in calls],
                )
                yield {"status": _batch_status_label(calls)}
                function_results = []
                for fc in calls:
                    try:
                        raw = fc.get("arguments")
                        args = json_module.loads(raw) if isinstance(raw, str) else (raw or {})
                        logging.info("  📞 %s(%s)", fc["name"], args)
                        output_json = json_module.dumps(
                            execute_tool(fc["name"], args, context=tool_context)
                        )
                    except Exception as e:
                        logging.error("  ❌ Tool error in %s: %s", fc.get("name"), e)
                        output_json = json_module.dumps({"error": str(e)})
                    function_results.append({
                        "type": "function_call_output",
                        "call_id": fc["call_id"],
                        "output": output_json,
                    })
                current_input = function_results
            else:
                logging.warning("⚠️ Agentic(stream) reached max_iterations=%d", max_iterations)

            if return_timing:
                stream_end_time = time.time()
                for i, r in enumerate(rag_sources, 1):
                    r["index"] = i
                timing_data.update({
                    "ttft_ms": round((first_delta_time - api_call_start_time) * 1000, 1) if first_delta_time else None,
                    "file_search_time_ms": round((file_search_complete_time - api_call_start_time) * 1000, 1) if file_search_complete_time else None,
                    "total_time_ms": round((stream_end_time - api_call_start_time) * 1000, 1),
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "rag_sources": rag_sources,
                    "tools_called": tools_called,
                })

        generator = stream_generator()
        if return_timing:
            return generator, timing_data
        return generator, []

    def _handle_non_streaming_response(
        self,
        response,
        api_call_start_time: float,
        question: str,
        model: Optional[str],
        temperature: Optional[float],
        use_verification: bool
    ) -> str:
        """Handle non-streaming response."""
        response_start_time = time.time()
        response_text = _strip_citation_markers(extract_response_text(response))
        response_end_time = time.time()
                
        total_time_ms = (response_end_time - api_call_start_time) * 1000
        logging.info(f"[RAG Latency] Total response time (non-streaming): {total_time_ms:.1f}ms")
                
        # Apply verification if enabled
        if use_verification:
            logging.info("🔍 Verification enabled - running second pass...")
            response_text = self._verify_answer(question, response_text, model, temperature)
        
        return response_text
    
    def _handle_agentic_response(
        self,
        question: str,
        instructions: str,
        model: str,
        temperature: float,
        tools: List[Dict[str, Any]],
        api_call_start_time: float,
        use_verification: bool,
        max_iterations: int = 5,
        tool_context: Optional[Dict[str, Any]] = None,
        return_timing: bool = False,
        force_tool: Optional[str] = None,
    ):
        """
        Handle agentic response with multi-turn tool execution loop.

        force_tool: when set to a function name (e.g. "ift_attack"), the model
        is required to call that function on the first turn (tool_choice); later
        turns revert to 'auto'. Used to measure tool-assisted accuracy.

        Returns the answer string, or — when return_timing=True — a
        (text, timing_data) tuple (token totals across the loop, wall-clock
        time, and the tools called), mirroring the streaming timing contract.
        """
        import json as json_module

        logging.info("🤖 Starting agentic response loop...")

        # Track previous response ID for context
        previous_response_id = None
        input_data = question
        total_input_tokens = 0
        total_output_tokens = 0
        tools_called: List[str] = []

        def _result(text):
            text = _strip_citation_markers(text)
            if not return_timing:
                return text
            elapsed = (time.time() - api_call_start_time) * 1000
            return text, {
                "response_time_ms": elapsed,
                "retrieval_ms": None,      # server-side file_search; not separable
                "inference_ms": elapsed,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "tools_called": tools_called,
                "iterations": iteration + 1,
            }

        for iteration in range(max_iterations):
            logging.info(f"🔄 Agentic iteration {iteration + 1}/{max_iterations}")

            # Force the named function on the FIRST turn only; later turns use
            # 'auto' so the model can stop calling tools and emit the answer.
            tc = ({"type": "function", "name": force_tool}
                  if force_tool and iteration == 0 else None)

            # Make API call (use previous_response_id if available)
            if previous_response_id:
                response = self.client.create_response(
                    model=model,
                    input=input_data,
                    previous_response_id=previous_response_id,
                    instructions=instructions,
                    temperature=temperature,
                    stream=False,
                    tools=tools,
                    tool_choice=tc,
                )
            else:
                response = self.client.create_response(
                    model=model,
                    input=input_data,
                    instructions=instructions,
                    temperature=temperature,
                    stream=False,
                    tools=tools,
                    tool_choice=tc,
                )
            
            # Store response ID for next iteration
            previous_response_id = getattr(response, "id", None)

            # Accumulate token usage across the loop
            usage = getattr(response, "usage", None)
            if usage is not None:
                total_input_tokens += getattr(usage, "input_tokens", 0) or 0
                total_output_tokens += getattr(usage, "output_tokens", 0) or 0

            # Extract output blocks
            output_blocks = getattr(response, "output", [])

            # Find function calls and extract final text
            function_calls = []
            final_text = None

            for block in output_blocks:
                b_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)

                if b_type == "function_call":
                    function_calls.append({
                        "call_id": block.get("call_id") if isinstance(block, dict) else getattr(block, "call_id", None),
                        "name": block.get("name") if isinstance(block, dict) else getattr(block, "name", None),
                        "arguments": block.get("arguments") if isinstance(block, dict) else getattr(block, "arguments", "{}")
                    })
                elif b_type == "message":
                    # Extract text output
                    content = block.get("content") if isinstance(block, dict) else getattr(block, "content", [])
                    for item in content:
                        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
                        if item_type == "output_text":
                            final_text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            
            tools_called.extend(fc["name"] for fc in function_calls if fc.get("name"))

            # If no function calls, we have our final answer
            if not function_calls:
                logging.info(f"✅ Agentic loop completed after {iteration + 1} iterations")
                if final_text is None:
                    final_text = extract_response_text(response)

                response_end_time = time.time()
                total_time_ms = (response_end_time - api_call_start_time) * 1000
                logging.info(f"[RAG Latency] Total agentic response time: {total_time_ms:.1f}ms")

                if use_verification:
                    logging.info("🔍 Verification enabled - running second pass...")
                    final_text = self._verify_answer(question, final_text, model, temperature)

                return _result(final_text)
            
            # 2. Execute function calls and build input for next iteration
            logging.info(f"🔧 Executing {len(function_calls)} function call(s)...")

            # Build array of function results
            function_results = []
            for fc in function_calls:
                call_id = fc.get("call_id")
                name = fc.get("name")
                args_raw = fc.get("arguments")

                try:
                    args = json_module.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    logging.info(f"  📞 {name}({args})")
                    result = execute_tool(name, args, context=tool_context)
                    result_json = json_module.dumps(result)

                    function_results.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result_json
                    })
                    logging.info(f"  ✅ Result: {result_json[:100]}...")
                except Exception as e:
                    logging.error(f"  ❌ Tool error: {e}")
                    function_results.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json_module.dumps({"error": str(e)})
                    })

            # Set input_data to function results for next iteration
            input_data = function_results
        
        logging.warning("⚠️ Max iterations reached")
        return _result(final_text or extract_response_text(response))


# Global service instance (lazy initialization)
_global_service: Optional[ASLService] = None


def get_asl_service(
    api_key: Optional[str] = None,
    vector_store_id: Optional[str] = None,
    config_file: Optional[str] = None
) -> ASLService:
    """
    Get the global ASL service instance (singleton pattern).
    
    Args:
        api_key: Optional API key (only used if creating new instance)
        vector_store_id: Optional vector store ID (only used if creating new instance)
        config_file: Optional config file path (only used if creating new instance)
        
    Returns:
        ASLService instance
    """
    global _global_service
    
    if _global_service is None:
        _global_service = ASLService(
            api_key=api_key,
            vector_store_id=vector_store_id,
            config_file=config_file
        )
    
    return _global_service


def reset_service():
    """Reset the global service instance (useful for testing)."""
    global _global_service
    _global_service = None
