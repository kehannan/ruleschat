"""Wall / hedge / bocage hexsides on the LOS (B9.2-.3)."""
from app.asl.attack.context import AttackContext
from app.asl.attack_resolver import (
    _HEXSIDE_TEM, _LOS_CLEAR_EXCEPT_HEXSIDES_ASSUMPTION, _los_hexsides,
    _native_los_matches_attack,
)


def walls(ctx: AttackContext) -> None:
    """Trace the hexsides the straight LOS crosses on the board files.

    At the target hexside: hexside TEM (B9.3), used later by `tem`. Elsewhere:
    a same-level Half-Level obstacle (B9.2) is warned about, not enforced.
    VASL's facts (`vasl_facts`, next) can override the target hexside.
    """
    if ctx.fbase != ctx.tbase or ctx.fkey == ctx.tkey:
        return
    ctx.hexside_crossings = _los_hexsides(ctx.state, ctx.fbase, ctx.flabel, ctx.tlabel)
    if ctx.hexside_crossings is None:
        return
    if not ctx.vasl_los:
        ctx.caveats.set("los", _LOS_CLEAR_EXCEPT_HEXSIDES_ASSUMPTION)
    native_los = ctx.native_los
    for c in ctx.hexside_crossings:
        terr = c["terrain"]
        a, b = c["between"]
        side_txt = f"{terr} hexside {a}|{b}"
        if c["at_target"]:
            if terr in _HEXSIDE_TEM:
                tem, rule = _HEXSIDE_TEM[terr]
                if tem > ctx.hexside_tem:
                    ctx.hexside_tem = tem
                    ctx.hexside_tem_label = (
                        f"{terr} hexside TEM +{tem} ({rule}: the LOS "
                        f"enters the target hex {ctx.tlabel} across its "
                        f"{a}|{b} {terr.lower()} hexside)")
                if ctx.rng == 1:
                    ctx.warnings.append(
                        f"Adjacent fire across the {side_txt}: Wall "
                        "Advantage (B9.32) matters — if the FIRER holds "
                        "WA over this hexside and the target does not "
                        "claim it, the target gets NO hexside TEM "
                        "(B9.31). Assumed here: the target receives it.")
                ctx.caveats.append(
                    f"{side_txt} TEM applied per B9.3 (not cumulative "
                    "with positive in-hex TEM — the higher applies, "
                    "B9.31; if the LOS crosses it through a road/gap "
                    "depiction the TEM applies only vs a non-moving "
                    "target, B9.3; PRC never receive it).")
            else:
                ctx.warnings.append(
                    f"LOS enters the target hex across a {side_txt}: "
                    "its hexside TEM is NOT auto-applied (not modeled) — "
                    "check the terrain chapter and add it manually.")
        elif c["at_firer"]:
            ctx.caveats.append(
                f"LOS leaves the firing hex across its own {side_txt}: "
                "no TEM for the target (B9.3 — the TEM belongs to the "
                "Location formed by that hexside) and no LOS block "
                "(B9.2 — the hexside is part of the viewing hex).")
        else:
            fe = (ctx.fentry.get("terrain") or {}).get("elevation")
            te = (ctx.tentry.get("terrain") or {}).get("elevation")
            vasl_clear = bool(
                native_los and not native_los.get("blocked")
                and _native_los_matches_attack(native_los, ctx.fkey, ctx.tkey))
            if fe is not None and te is not None and fe != te:
                # B9.2 blocks only SAME-level LOS; don't invite the model to
                # second-guess an attack between different elevations.
                ctx.caveats.append(
                    f"LOS crosses a {side_txt} that is part of neither "
                    f"hex, but the firer (hex elevation {fe}) and target "
                    f"(hex elevation {te}) are at different levels, so "
                    "the B9.2 same-level Half-Level block does not apply"
                    + (" (VASL's LOS check also reports it clear)."
                       if vasl_clear else "."))
            elif vasl_clear:
                ctx.caveats.append(
                    f"LOS crosses a {side_txt} that is part of neither "
                    "hex; VASL's native LOS check reports this LOS clear, "
                    "so it is taken as existing (B9.2).")
            else:
                ctx.warnings.append(
                    f"LOS crosses a {side_txt} that is part of NEITHER the "
                    "firing nor the target hex: at the same level a "
                    "wall/hedge hexside is a Half-Level obstacle that BLOCKS "
                    "LOS (B9.2) — this attack may be impossible. The "
                    "resolution below assumes LOS exists (e.g. an elevation "
                    "advantage); verify on the map.")
