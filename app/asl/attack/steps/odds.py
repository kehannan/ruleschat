"""Cowering, target morale, and the IFT odds; assembles the result
(A7.9, A10, E4)."""
import logging
from typing import Any, Dict, List

from app.asl import ift
from app.asl.attack.context import AttackContext
from app.asl.attack_resolver import COWERING_EXEMPT_SIDES, _unit_entrenchment


def cowering(ctx: AttackContext) -> None:
    """Some nationalities never cower (A7.9)."""
    ctx.firer_cowering_exempt = ctx.firing_side in COWERING_EXEMPT_SIDES
    if ctx.firer_cowering_exempt:
        ctx.caveats.append(
            f"{ctx.firing_side} units do not cower (A7.9) [EXC: Conscripts — "
            "assumed none in the stack]."
        )


def target_morale(ctx: AttackContext) -> None:
    """Break/pin odds need a personnel target with a printed morale."""
    if not ctx.target_personnel:
        ctx.warnings.append(
            "No personnel counter with a parseable morale in the target hex — "
            "break/pin odds omitted (use ift_attack with an explicit target)."
        )


def skis(ctx: AttackContext) -> None:
    """Ski mode (E4.2, E4.6) vs skis merely carried (E4.21)."""
    if ctx.skis_worn:
        # E4.2: a unit with its ski counter "Skis" face up is a Skier (ski
        # mode). E4.6 (verified verbatim): "A Skier may not fire any Gun,
        # ordnance SW, or MMG/HMG; he must change to foot mode first."
        ctx.warnings.append(
            "Firing unit(s) have skis WORN (ski mode, E4.2 — they are "
            "Skiers): E4.6 forbids a Skier from firing any Gun, ordnance "
            "SW, or MMG/HMG (he must change to foot mode first) — NOT "
            "enforced here; other Chapter E4 Skier effects are not modeled."
        )
    if ctx.skis_carried:
        ctx.caveats.append(
            "Ski counter(s) with the 'OFF Skis' face up in the firing hex: "
            "the skis are merely carried at a cost of one PP (E4.21) — the "
            "units are normal Infantry for this attack, no E4 Skier "
            "effects apply (the 1 PP portage load is not modeled)."
        )


def _group_target(ctx: AttackContext, group_units):
    """(ift target spec, morale-source note) for one TEM group."""
    pers = [(u, cls) for u, cls in ctx.target_personnel if u in group_units]
    if not pers:
        return None, None
    morales = sorted((cls["morale"] for _, cls in pers), reverse=True)
    morale = morales[0]
    names = ", ".join(u.get("name", "?") for u, _ in pers)
    if len(set(morales)) > 1:
        ctx.warnings.append(
            f"Target units ({names}) have mixed morale {morales}; MC "
            f"odds computed vs the highest ({morale}) — each unit "
            "actually checks against its own morale."
        )
    spec = {"kind": "personnel", "morale": morale,
            "encircled": "Encircled" in ctx.t_markers}
    return spec, (f"Target morale {morale} taken from the printed counter "
                  f"value(s) of: {names}.")


def odds(ctx: AttackContext) -> None:
    """Run the IFT once per distinct target TEM and assemble the result."""
    phase = ctx.phase
    resolutions: List[Dict[str, Any]] = []
    for grp in ctx.tem_groups:
        target, morale_note = _group_target(ctx, grp["units"])
        if morale_note:
            ctx.caveats.append(morale_note)
        res = ift.compute_attack(
            units=ctx.ift_units,
            afph=(phase == "advancing"),
            area_fire_halvings=ctx.area_fire_halvings,
            tem=grp["tem"],
            hindrance=ctx.hindrance,
            ffnam=ctx.ffnam,
            ffmo=ctx.ffmo,
            leadership=ctx.leadership if ctx.leader_directs else 0,
            encircled_firer=ctx.encircled_firer,
            other_drm=ctx.other_drm,
            firer_cowering_exempt=ctx.firer_cowering_exempt,
            target=target,
        )
        res.pop("cells", None)  # UI-only heatmap
        resolutions.append({
            "applies_to": grp["desc"],
            "tem": grp["tem"],
            "drm": res.get("drm"),
            "morale_used": (target or {}).get("morale"),
            "ift": res,
        })
    # FP, column, and cowering are TEM-independent — identical across groups.
    result = resolutions[0]["ift"]
    single = len(resolutions) == 1

    # Attach the per-unit FP audit steps to the named firers.
    for row, fb in zip(ctx.firer_rows, result.get("fp_breakdown", [])):
        row["fp_resolution"] = fb

    if phase == "advancing":
        ctx.caveats.append("Advancing Fire: all FP halved (A7.24).")
        applied = [r["name"] for r, e in zip(ctx.firer_rows, ctx.ift_units)
                   if e.get("assault_fire")]
        if applied:
            ctx.caveats.append(
                "Assault Fire +1 applied (A7.36) for: " + ", ".join(applied)
                + " — capability read deterministically from the counter's "
                "underscored FP factor (VASL counter art / Chapter A "
                "national notes), per A1.21."
            )

    pbf_note = None
    if ctx.pbf_mode == "pbf":
        pbf_note = ("Point Blank Fire: FP doubled — firer ADJACENT to the "
                    "target (A7.21)")
    elif ctx.pbf_mode == "tpbf":
        pbf_note = ("Triple Point Blank Fire: FP tripled — same Location "
                    "(A7.211)")

    out = {
        "firing_hex": ctx.fkey,
        "target_hex": ctx.tkey,
        "phase": phase,
        "firing_side": ctx.firing_side,
        "range": {
            "hexes": ctx.rng,
            "method": ctx.range_method,
            "pbf": ctx.pbf_mode,
            "note": pbf_note,
        },
        # Wall/hedge/bocage hexsides crossed by the straight LOS (same-board
        # only; None = not traced). Each: terrain, between (hex, hex),
        # at_target, at_firer, t (0 firer .. 1 target).
        "los_hexsides": ctx.hexside_crossings,
        "firers": ctx.firer_rows,
        "excluded": ctx.excluded,
        "total_fp": result.get("total_fp"),
        "column": result.get("column"),
        "drm_breakdown": ctx.drm_breakdown,
        # Mixed-TEM target: there is no single DRM — see "resolutions".
        "drm": result.get("drm") if single else None,
        "cowering": result.get("cowering"),
        "target": {
            "units": [
                {"name": u.get("name"), "side": u.get("side"),
                 "markers": u.get("markers") or [],
                 "entrenched_by": _unit_entrenchment(u),
                 "broken": bool(u.get("broken")),
                 "concealed": bool(u.get("concealed_by")),
                 "moved": bool(u.get("moved"))}
                for u in ctx.t_units
            ],
            "terrain": (ctx.tentry.get("terrain") or {}).get("terrain"),
            # True / False / "mixed" (per-unit detail in units[].entrenched_by)
            "entrenched": ctx.entrenched,
            "morale_used": resolutions[0]["morale_used"] if single else None,
        },
        "warnings": ctx.warnings + result.get("warnings", []),
        "assumptions": ctx.caveats.as_list(),
        "ift": result if single else None,
    }
    if not single:
        out["resolutions"] = resolutions
    logging.info(
        "🎯 resolve_attack(%s -> %s, %s): %s FP col %s, DRM %+d, range %d (%s)",
        ctx.fkey, ctx.tkey, phase, result.get("total_fp"), result.get("column"),
        result.get("drm") or 0, ctx.rng, ctx.pbf_mode,
    )
    ctx.result = out
