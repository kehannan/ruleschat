"""Locate the hexes, measure range, and settle LOS (A6, A7.21-.22)."""
from app.asl.attack.context import AttackContext, NoAttack
from app.asl.attack_resolver import (
    VALID_PHASES, _apply_vasl_facts, _bare, _blocked_los_detail,
    _empty_hex_entry, _find_hex_key, _grid_los, _native_los_matches_attack,
    _split_hex_key, _unmodeled_board_ssrs_for_attack, hex_range_cross_board,
    hex_range_same_board,
)


def locate(ctx: AttackContext) -> None:
    """Validate the inputs and find both hexes in the save."""
    if ctx.phase not in VALID_PHASES:
        raise ValueError(f"Invalid phase {ctx.phase!r}. Must be one of {VALID_PHASES}.")
    if not isinstance(ctx.state, dict) or "hexes" not in ctx.state:
        raise ValueError("state does not look like a parse_vsav() result.")
    ctx.fkey = _find_hex_key(ctx.state, ctx.firing_hex)
    try:
        ctx.tkey = _find_hex_key(ctx.state, ctx.target_hex)
        ctx.tentry = ctx.state["hexes"][ctx.tkey]
    except ValueError as e:
        if "no units" not in str(e):
            raise
        ctx.tkey, ctx.tentry = _empty_hex_entry(ctx.state, ctx.target_hex)
    if ctx.fkey == ctx.tkey:
        ctx.warnings.append(
            "Firing hex equals target hex: IFT attacks within the same "
            "Location are rarely legal (A7.21/A7.211) — treated as TPBF."
        )
    ctx.fentry = ctx.state["hexes"][ctx.fkey]
    ctx.fbase, ctx.flabel = _split_hex_key(ctx.fkey)
    ctx.tbase, ctx.tlabel = _split_hex_key(ctx.tkey)


def vasl_verdict(ctx: AttackContext) -> None:
    """VASL's own LOS check for this pair: blocked ends the attack (A6)."""
    native_los = ctx.native_los
    if not native_los:
        return
    if not _native_los_matches_attack(native_los, ctx.fkey, ctx.tkey):
        ctx.warnings.append(
            "Ignored VASL native LOS payload because its endpoints did not "
            f"unambiguously match this attack ({ctx.fkey} -> {ctx.tkey})."
        )
        return
    if native_los.get("blocked"):
        unmodeled_ssrs = _unmodeled_board_ssrs_for_attack(ctx.state, ctx.fkey, ctx.tkey)
        if unmodeled_ssrs:
            ctx.warnings.append(
                "VASL native LOS reported blocked, but this board has "
                "unmodeled visual/overlay SSR terrain changes active "
                f"({', '.join(unmodeled_ssrs)}). Ignoring the blocked "
                "LOS result and resolving the attack with terrain "
                "caveats; verify the LOS on the visible map."
            )
        else:
            facts = native_los.get("facts") or {}
            raise NoAttack(
                "LOS is blocked (VASL native LOS check)" + (
                    f": {native_los.get('reason')}"
                    if native_los.get("reason") else "") + "."
                + (f" VASL places the block in {_bare(facts.get('blocked_at_hex'))}."
                   if facts.get("blocked_at_hex") else "")
                + _blocked_los_detail(ctx.state, ctx.fkey, ctx.tkey, ctx.fentry,
                                      ctx.tentry, native_los),
                rule="A6")
        return
    try:
        ctx.hindrance = max(0, int(native_los.get("hindrance", 0)))
    except (TypeError, ValueError):
        ctx.hindrance = 0
    ctx.vasl_los = True
    ctx.caveats.set("los",
                    "LOS checked by VASL's native LOS engine using the open game "
                    "map, overlays, and counters: clear.")
    ctx.caveats.set("hindrance",
                    f"Intervening LOS Hindrances counted by VASL's native LOS "
                    f"check: +{ctx.hindrance} (A6.7).")


def measure_range(ctx: AttackContext) -> None:
    """Range in hexes: exact on one board, map geometry across boards."""
    if ctx.fbase == ctx.tbase:
        ctx.rng = hex_range_same_board(ctx.flabel, ctx.tlabel)
        ctx.range_method = "same-board hex geometry (exact)"
        return
    ctx.rng = hex_range_cross_board(ctx.state, ctx.fbase, ctx.flabel,
                                    ctx.tbase, ctx.tlabel)
    ctx.range_method = "cross-board map geometry (best effort)"
    ctx.warnings.append(
        "Firing and target hexes are on different boards — range computed "
        "from map-pixel geometry; verify on the map."
    )
    if ctx.rng is None:
        raise ValueError(
            f"Could not compute cross-board range {ctx.fkey} -> {ctx.tkey} "
            "(board metadata missing)."
        )


def grid_trace(ctx: AttackContext) -> None:
    """Without VASL's check: obstacles + Hindrances from the board files."""
    if ctx.vasl_los or ctx.fbase != ctx.tbase or ctx.fkey == ctx.tkey:
        return
    out = {}
    _grid_los(ctx.state, ctx.fbase, ctx.flabel, ctx.tlabel, ctx.fentry,
              ctx.tentry, ctx.caveats, ctx.warnings, out)
    if out.get("hindrance"):
        ctx.hindrance = out["hindrance"]
        ctx.hindrance_label = out["label"]


def vasl_facts(ctx: AttackContext) -> None:
    """VASL's map facts (extension >= 0.5): levels, target hexside,
    roadblocks, hindrance sources. They win over the board files."""
    native_los = ctx.native_los
    facts = (native_los or {}).get("facts") if ctx.vasl_los else None
    if not isinstance(facts, dict):
        return
    ctx.facts = facts
    if facts.get("source_height") is not None and facts.get("target_height") is not None:
        def _where(prefix):
            loc = native_los.get(f"{prefix}_location")
            known = native_los.get(f"{prefix}_from_units")
            if known is None:   # extension < 0.6.1: always the hex's ground level
                return "ground level of the hex"
            return (f"{loc}, where its units are" if known
                    else f"{loc} (no visible units: ground level)")
        ctx.caveats.set("levels",
                        f"Levels from VASL's map: firer {ctx.fkey} at level "
                        f"{facts['source_height']} ({_where('source')}), target "
                        f"{ctx.tkey} at level {facts['target_height']} "
                        f"({_where('target')}).")
    ctx.hexside_tem, ctx.hexside_tem_label, label = _apply_vasl_facts(
        facts, ctx.fbase, ctx.flabel, ctx.tlabel, ctx.tentry, ctx.hindrance,
        ctx.hexside_tem, ctx.hexside_tem_label, ctx.caveats, ctx.warnings)
    if label:
        ctx.hindrance_label = label


def overlays(ctx: AttackContext) -> None:
    """Only worth a caveat when the save has an overlay on these boards."""
    here = {str(ctx.fbase), str(ctx.tbase)}
    ovs = [o for o in ctx.state.get("overlays") or []
           if str(o.get("board", "")).lstrip("r") in here]
    ctx.caveats.set("overlays", (
        "Map overlay(s) on this board (" + ", ".join(
            str(o.get("spec")) for o in ovs) + "): the target's terrain/TEM is "
        "read from the base board" + ("; VASL's LOS check includes them."
                                      if ctx.vasl_los else ".")
    ) if ovs else None)
