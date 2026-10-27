"""The target stack: which units, their state, concealment (A7.23, A12)."""
from app.asl.attack.context import AttackContext, NoAttack
from app.asl.attack_resolver import classify_unit


def target_units(ctx: AttackContext) -> None:
    """Target units (optionally narrowed to the selected stack) and notes
    on their state."""
    t_units = ctx.tentry.get("units", [])
    selected = [name.strip().lower() for name in (ctx.selected_targets or [])
                if isinstance(name, str) and name.strip()]
    if selected:
        t_units = [u for u in t_units if any(
            chosen in (u.get("name") or "").lower()
            or (u.get("name") or "").lower() in chosen
            for chosen in selected)]
    if not t_units:
        if selected:
            raise NoAttack("The selected target stack does not match any "
                           f"unit in target hex {ctx.tkey}.")
        if not ctx.tentry.get("empty"):
            raise NoAttack(f"Target hex {ctx.tkey} contains no units in the save.")
        ctx.warnings.append(
            f"Target hex {ctx.tkey} is EMPTY in the save: FP column and DRM are "
            "for a hypothetical unentrenched, unconcealed target there; "
            "break/pin odds need its morale (use ift_attack with a target)."
        )
        if not ctx.tentry.get("terrain"):
            ctx.warnings.append(
                f"No board terrain data for {ctx.tkey}: TEM taken as 0 — verify it."
            )
    if all(u.get("side") == ctx.firing_side for u in t_units if u.get("side")):
        ctx.warnings.append(
            f"Target hex {ctx.tkey} contains only {ctx.firing_side} (friendly) units."
        )
    ctx.t_units = t_units
    ctx.moved_target_count = sum(1 for u in t_units if u.get("moved"))
    ctx.target_is_moving = bool(t_units) and ctx.moved_target_count == len(t_units)

    for u in t_units:
        cls = classify_unit(u)
        if cls["kind"] == "personnel":
            ctx.target_personnel.append((u, cls))
        elif cls["kind"] == "leader" and cls["leadership"] is None:
            ctx.warnings.append(
                f"Leader {u.get('name')} in the target hex: morale/leadership "
                "unknown from the counter name — MC odds below ignore any "
                "leader MC DRM (A10.7)."
            )
        if u.get("broken"):
            ctx.warnings.append(
                f"Target unit {u.get('name')} is BROKEN: further MC failures "
                "cause Casualty Reduction (A10.31); DM/broken status does NOT "
                "change the IFT DR itself."
            )
        if "DM" in (u.get("markers") or []):
            ctx.warnings.append(
                "DM on the target affects rally, not this IFT DR."
            )
        if "Pin" in (u.get("markers") or []):
            ctx.warnings.append(
                "Target is pinned: no change to the IFT DR (pin affects the "
                "target's own actions, A7.8)."
            )


def concealment(ctx: AttackContext) -> None:
    """Fire vs a concealed target is Area Fire: FP halved (A7.23)."""
    conc = [bool(u.get("concealed_by")) for u in ctx.t_units]
    if conc and all(conc):
        ctx.area_fire_halvings = 1
        ctx.warnings.append(
            "All target units are concealed — FP halved as Area Fire (A7.23)."
        )
    elif any(conc):
        ctx.warnings.append(
            "Target hex mixes concealed and unconcealed units: attacks vs "
            "the concealed ones are halved (A7.23); odds below are computed "
            "WITHOUT the halving (vs the unconcealed units)."
        )
