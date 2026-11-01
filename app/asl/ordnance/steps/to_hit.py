"""To Hit: TH#, DRM ledger, the 36-roll hit tree (C3-C6)."""
from app.asl import thtk
from app.asl.ordnance.context import ShotContext


def th_number(ctx: ShotContext) -> None:
    """Basic TH# by TH# color (C3, A25 chart) + Gun & Ammo mods = Modified TH# (C4)."""
    if ctx.th_color:
        ctx.color = ctx.th_color.lower()
        if ctx.color not in ("black", "red"):
            raise ValueError("th_color must be 'black' or 'red'.")
    else:
        ctx.color, note = thtk.th_color_for(ctx.nationality, ctx.year)
        if note:
            ctx.notes.append(note)
    ctx.mth = thtk.modified_to_hit(ctx.target_type, ctx.range_hexes, ctx.mm, ctx.barrel,
                                   ctx.ammo, ctx.color, ctx.nationality)


def th_drm_ledger(ctx: ShotContext) -> None:
    """The caller's C5/C6 DRM lines, plus TEM as Case Q (C6.8): a TH DRM on the
    Vehicle/Infantry Target Type, but not on the Area Target Type (C3.331) nor
    when the TEM is what makes the AFV Hull Down."""
    ctx.drm_lines = [{"label": str(d.get("label", "DRM")), "drm": int(d.get("drm", 0)),
                      **({"rule": d["rule"]} if d.get("rule") else {})}
                     for d in ctx.th_drm]
    if ctx.tem and ctx.target_type != "area" and not ctx.hull_down:
        q = thtk._case("Q")
        ctx.drm_lines.append({"label": q["label"], "drm": int(ctx.tem), "rule": q["rule"]})
    ctx.total_drm = sum(d["drm"] for d in ctx.drm_lines)


def hit_tree(ctx: ShotContext) -> None:
    """All 36 TH rolls -> miss / hit, CH, location, Multiple Hit (C3.6-.9)."""
    multiple_ok = (15 <= ctx.mm <= 40 and ctx.target_type in ("vehicle", "infantry")
                   and ctx.ammo != "SMOKE")
    ctx.events = thtk._th_events(ctx.target_type, ctx.mth["modified"], ctx.total_drm,
                                 ctx.hull_down, multiple_ok, ctx.double_lower_dr)
    ctx.p_hit = ctx.hits()


def to_hit_summary(ctx: ShotContext) -> None:
    """Report the To Hit half and start the result."""
    mth, drm = ctx.mth, ctx.total_drm
    to_hit = {
        **{k: v for k, v in mth.items() if k != "modified"},
        "modified_th": mth["modified"],
        "drm": ctx.drm_lines,
        "total_drm": drm,
        "needed_original_dr": mth["modified"] - drm,
        "improbable": 2 + drm > mth["modified"],
        "p_hit": round(float(ctx.p_hit), 4),
        "p_critical": round(float(ctx.hits(ch=True)), 4),
        "p_multiple": round(float(ctx.hits(multiple=True)), 4),
        "p_turret": round(float(ctx.hits(loc="turret")), 4),
        "p_hull": round(float(ctx.hits(loc="hull")), 4),
        "p_malfunction": round(sum(1 for c, w in thtk._DICE if c + w >= ctx.b_number) / 36, 4),
        "p_rof_retained": round(sum(1 for c, w in thtk._DICE if c <= ctx.rof) / 36, 4),
    }
    if ctx.double_lower_dr:
        ctx.notes.append("Case C4 Motion Firer: the lower dr of the TH DR is doubled (C5.35).")
    if to_hit["improbable"]:
        ctx.notes.append("Improbable Hit: only an Original 2 followed by a dr of 1-3 hits (C3.6).")
    ctx.result = {
        "gun": f"{ctx.mm}{ctx.barrel}", "mm": ctx.mm, "barrel": ctx.barrel,
        "nationality": ctx.nationality, "ammo": ctx.ammo, "target_type": ctx.target_type,
        "range": ctx.range_hexes, "target": {**ctx.target, "kind": ctx.kind},
        "to_hit": to_hit, "notes": ctx.notes,
    }
    ctx.total["Miss"] += 1 - ctx.p_hit
