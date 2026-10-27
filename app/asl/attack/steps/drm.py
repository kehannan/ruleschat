"""The DRM ledger: hindrance, TEM (terrain / hexside / entrenchment), firer
modifiers, FFMO/FFNAM (A4.6, A6.7, B9.31, B27)."""
from app.asl.attack.context import AttackContext
from app.asl.attack_resolver import (
    CX_DRM, ENTRENCHMENT_REPLACES_RULE, ENTRENCHMENT_RULE, ENTRENCHMENT_TEM,
    _all_hex_markers, _terrain_tem, _unit_entrenchment,
)


def hindrance(ctx: AttackContext) -> None:
    """LOS Hindrance DRM from VASL's check or the board trace (A6.7)."""
    if ctx.hindrance:
        ctx.drm_breakdown.append({
            "label": ctx.hindrance_label
                     or f"VASL native LOS hindrance (A6.7) +{ctx.hindrance}",
            "drm": ctx.hindrance,
        })


def tem(ctx: AttackContext) -> None:
    """Target TEM. Hexside TEM is not cumulative with in-hex TEM (B9.31);
    an entrenchment REPLACES hex terrain TEM for the units in it (B27.3,
    B27.13). Units split by TEM are resolved once per group."""
    t_units = ctx.t_units
    ctx.t_markers = _all_hex_markers(ctx.tentry)
    in_ent = [u for u in t_units if _unit_entrenchment(u)]
    out_ent = [u for u in t_units if not _unit_entrenchment(u)]
    ent_kind = ("Trench" if any(_unit_entrenchment(u) == "Trench"
                                for u in in_ent) else "Foxhole")

    value, label = _terrain_tem(ctx.tentry, ctx.warnings, ctx.caveats,
                                hexside_checked=ctx.hexside_crossings is not None)
    if ctx.hexside_tem:
        if ctx.hexside_tem > value:
            if value > 0:
                ctx.caveats.append(
                    f"In-hex TEM ({label} +{value}) "
                    f"is superseded by the higher hexside TEM "
                    f"+{ctx.hexside_tem} (B9.31, not cumulative).")
            value, label = ctx.hexside_tem, ctx.hexside_tem_label
        else:
            ctx.caveats.append(
                f"{ctx.hexside_tem_label} is NOT cumulative with the target's "
                f"in-hex TEM ({label} +{value}); the "
                "in-hex TEM (equal or higher) applies (B9.31).")
    ctx.terrain_tem_value, ctx.terrain_tem_label = value, label

    entrenched = bool(in_ent) and not out_ent
    mixed = bool(in_ent) and bool(out_ent)
    ctx.entrenched = "mixed" if mixed else entrenched

    if entrenched is True:
        ctx.tem_groups = [{"tem": ENTRENCHMENT_TEM, "units": t_units,
                           "desc": f"all target units IN the {ent_kind}"}]
        ctx.drm_breakdown.append({
            "label": (f"{ent_kind} TEM +{ENTRENCHMENT_TEM} "
                      f"({ENTRENCHMENT_RULE}; entrenchment TEM REPLACES hex "
                      f"terrain TEM — not cumulative, the occupant is a "
                      f"separate Location for TEM, {ENTRENCHMENT_REPLACES_RULE})"),
            "drm": ENTRENCHMENT_TEM,
        })
        if value > ENTRENCHMENT_TEM:
            ctx.warnings.append(
                f"Hex terrain TEM ({label}, "
                f"+{value}) exceeds the entrenchment TEM; units "
                "NOT beneath the entrenchment would claim the terrain TEM "
                "instead (B27.13)."
            )
        elif value:
            ctx.drm_breakdown.append({
                "label": f"[replaced] {label} "
                         f"+{value} — superseded by the "
                         "entrenchment TEM (B27.3)",
                "drm": 0,
            })
    elif mixed:
        in_names = ", ".join(u.get("name", "?") for u in in_ent)
        out_names = ", ".join(u.get("name", "?") for u in out_ent)
        ctx.drm_breakdown.append({
            "label": (f"IN {ent_kind} ({in_names}): TEM "
                      f"+{ENTRENCHMENT_TEM} ({ENTRENCHMENT_RULE}; replaces "
                      f"hex terrain TEM, {ENTRENCHMENT_REPLACES_RULE})"),
            "drm": ENTRENCHMENT_TEM,
        })
        ctx.drm_breakdown.append({
            "label": (f"NOT in {ent_kind} ({out_names}): "
                      f"{label} +{value}"),
            "drm": value,
        })
        if value == ENTRENCHMENT_TEM:
            ctx.tem_groups = [{"tem": ENTRENCHMENT_TEM, "units": t_units,
                               "desc": "all target units (entrenchment and "
                                       "terrain TEM happen to be equal)"}]
            ctx.warnings.append(
                f"MIXED occupancy in target hex {ctx.tkey} (stack order): "
                f"{in_names} IN the {ent_kind}, {out_names} NOT — but both "
                f"TEMs equal +{ENTRENCHMENT_TEM}, so a single resolution "
                "covers everyone."
            )
        else:
            ctx.tem_groups = [
                {"tem": ENTRENCHMENT_TEM, "units": in_ent,
                 "desc": f"units IN the {ent_kind} ({in_names})"},
                {"tem": value, "units": out_ent,
                 "desc": f"units NOT in the {ent_kind} ({out_names})"},
            ]
            ctx.warnings.append(
                f"MIXED TEM in target hex {ctx.tkey}: stack order shows "
                f"{in_names} IN the {ent_kind} (+{ENTRENCHMENT_TEM}, "
                f"{ENTRENCHMENT_RULE}) but {out_names} NOT in it "
                f"(+{value}, hex terrain) — the attack is "
                "resolved once per TEM; see the per-group resolutions."
            )
    else:
        ctx.tem_groups = [{"tem": value, "units": t_units,
                           "desc": "all target units (no entrenchment)"}]
        ctx.drm_breakdown.append({"label": f"TEM: {label}", "drm": value})


def firer_modifiers(ctx: AttackContext) -> None:
    """CX (A4.51), leadership direction (A7.531), encircled firer (A7.7)."""
    if ctx.cx_present:
        ctx.other_drm.append({"label": "CX firer: +1 to the IFT DR (A4.51)",
                              "drm": CX_DRM})
        ctx.drm_breakdown.append({"label": "CX firer (A4.51)", "drm": CX_DRM})
    if ctx.leader_directs and ctx.leadership:
        ctx.drm_breakdown.append({
            "label": f"leadership direction {ctx.leadership:+d} (A7.531; also "
                     "prevents cowering, A7.9)",
            "drm": ctx.leadership,
        })
    if ctx.encircled_firer:
        ctx.drm_breakdown.append({"label": "encircled firer +1 (A7.7)", "drm": 1})


def ffmo_ffnam(ctx: AttackContext) -> None:
    """FFMO/FFNAM: Defensive First Fire vs moving units only (A4.6). VASL's
    moved trait is reset by its phase workflow, so it is meaningful here
    but deliberately ignored outside DFPh."""
    phase = ctx.phase
    if phase in ("prep", "advancing"):
        ctx.caveats.append(
            "FFMO/FFNAM not applicable: they exist only in Defensive First "
            "Fire vs moving units (A4.6) — never in "
            f"{'Prep' if phase == 'prep' else 'Advancing'} Fire."
        )
    elif phase == "defensive_first" and ctx.target_is_moving:
        ctx.ffnam = True
        ctx.ffmo = True
        ctx.caveats.append(
            "All selected target counters are marked moved in VASL: "
            "FFNAM is applied and FFMO is attempted (A4.6; FFMO is "
            "automatically negated by positive TEM or LOS hindrance)."
        )
        ctx.drm_breakdown.append({"label": "FFNAM (A4.6)", "drm": -1})
        if len(ctx.tem_groups) > 1:
            ctx.caveats.append(
                "Target counters have different TEM groups: FFMO is checked "
                "separately in each group resolution below."
            )
        elif ctx.terrain_tem_value == 0 and ctx.hindrance == 0:
            ctx.drm_breakdown.append({"label": "FFMO (A4.6)", "drm": -1})
        else:
            ctx.warnings.append(
                "Target is marked moved, but FFMO is negated by positive "
                "TEM or LOS hindrance (A4.6); FFNAM still applies."
            )
    elif phase == "defensive_first" and ctx.moved_target_count:
        ctx.warnings.append(
            "Selected target stack mixes moved and unmoved counters: "
            "FFNAM/FFMO are not auto-applied. Use the Target chooser to "
            "ask about only the moving counters (A4.6)."
        )
    elif phase == "defensive_first":
        ctx.warnings.append(
            "Defensive First Fire: selected target counters are not marked "
            "moved in VASL, so FFNAM/FFMO are not applied (A4.6)."
        )


def encircled_target(ctx: AttackContext) -> None:
    """An encircled target's morale is one lower (A7.7)."""
    if "Encircled" in ctx.t_markers:
        ctx.warnings.append("Target is Encircled: morale lowered by 1 (A7.7) — "
                            "applied to the MC odds below.")
