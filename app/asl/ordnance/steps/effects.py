"""Effects of the hits: To Kill vs vehicles (C7), the IFT vs personnel (C3.71, C8.31).

Each step handles one kind of target and returns early otherwise; the first
one that applies sets ctx.done (the pipeline then skips the rest).
"""
from fractions import Fraction
from typing import Any, Dict

from app.asl import ift, thtk
from app.asl.ordnance.context import ShotContext


def _hit_miss(ctx: ShotContext, hit_label: str) -> None:
    p = ctx.result["to_hit"]["p_hit"]
    ctx.result["outcomes"] = [{"result": hit_label, "probability": p},
                              {"result": "Miss", "probability": round(1 - p, 4)}]
    ctx.done = True


def smoke(ctx: ShotContext) -> None:
    """SMOKE only places SMOKE (C3.33, C8.5)."""
    if ctx.ammo == "SMOKE":
        _hit_miss(ctx, "SMOKE placed")


def area_vs_vehicle(ctx: ShotContext) -> None:
    """An Area Target Type hit on a vehicle resolves on the IFT (C1.55/C3.332): not modelled."""
    if ctx.target_type == "area" and ctx.kind != "personnel":
        ctx.notes.append("An Area Target Type hit vs a vehicle is resolved on the IFT (C1.55/"
                         "C3.332); not modelled here.")
        _hit_miss(ctx, "Hit")


def _attack_fp(ctx: ShotContext):
    """The IFT FP vs personnel: override, HE FP by caliber, or HE Equivalency (C8.31)."""
    t = thtk._load()
    if ctx.fp is not None:
        return float(ctx.fp), "override"
    if ctx.ammo == "HE":
        full = thtk.he_fp(ctx.mm)
        if full is None:
            raise ValueError(f"{ctx.mm}mm has no HE FP on the IFT (20mm+).")
        return full, "HE FP by caliber (IFT)"
    if ctx.ammo in ("AP", "APCR", "APDS"):
        eq = t["he_equivalency"]
        return (eq["ap_lt37"] if ctx.mm < 37 else eq["ap_ge37"]), "AP HE Equivalency (C8.31)"
    cols = ift.valid_columns()          # HEAT: the column left of the gun's normal HE FP
    normal = thtk._ift_column(thtk.he_fp(ctx.mm) or 0)
    full = cols[cols.index(normal) - 1] if normal and cols.index(normal) > 0 else None
    if full is None:
        raise ValueError("No HEAT HE Equivalency for this gun; pass fp.")
    return full, "HEAT HE Equivalency (C8.31)"


def personnel(ctx: ShotContext) -> None:
    """Hits vs personnel on the IFT: half FP on the Area Target Type (C3.33); a CH
    doubles the full FP and reverses positive TEM (C3.71); Multiple Hits keep the
    better of two Effects DR (C3.8)."""
    if ctx.kind != "personnel":
        return
    full, source = _attack_fp(ctx)
    area = ctx.target_type == "area"
    normal_fp = full / 2 if area else full
    normal_drm = ctx.effects_drm + (ctx.tem if area else 0)
    ch_fp, ch_drm = full * 2, ctx.effects_drm - max(ctx.tem, 0)
    if area:
        ctx.notes.append("Area Target Type: half FP, TEM applies to the Effects DR (C3.33/C3.331).")

    branches = []
    for ch in (False, True):
        p = ctx.hits(ch=ch)
        if not p:
            continue
        fpv, drm = (ch_fp, ch_drm) if ch else (normal_fp, normal_drm)
        col = thtk._ift_column(fpv)
        single = ctx.hits(ch=ch, multiple=False)
        dist = thtk._ift_dist(col, drm, False)
        for r, q in dist.items():
            ctx.total[r] += single * q
        if p - single:
            for r, q in thtk._ift_dist(col, drm, True).items():
                ctx.total[r] += (p - single) * q
        branches.append({"critical": ch, "probability": round(float(p), 4),
                         "fp": fpv, "ift_column": col, "effects_drm": drm,
                         "effects": thtk._fmt(dist)})
    ctx.result["to_kill"] = {"fp_source": source, "full_fp": full, "branches": branches}
    ctx.result["outcomes"] = thtk._fmt(ctx.total)
    ctx.done = True


def _final_tk(ctx: ShotContext, btk: Dict[str, Any], case_d, facing: str,
              ch: bool, loc: str) -> Dict[str, Any]:
    """Basic TK#, x2 on a CH (C7.23), +1 rear (C7.21), Case D (C7.24), minus AF (C7.11)."""
    lines = [{"label": f"Basic TK# ({btk['table']})", "value": btk["basic"]}]
    v = btk["basic"]
    if ch:
        lines.append({"label": "Critical Hit ×2 (C7.23)", "value": v})
        v *= 2
    if facing == "rear":
        lines.append({"label": "Rear Target Facing (C7.21)", "value": 1})
        v += 1
    if case_d is not None and case_d != 0:
        lines.append({"label": "Case D range (C7.24)", "value": case_d})
        v += case_d
    af = int(ctx.target["turret_af" if loc == "turret" else "hull_af"])
    lines.append({"label": f"{loc} AF (C7.11)", "value": -af})
    return {"final": v - af, "lines": lines}


def armored(ctx: ShotContext) -> None:
    """Hits vs an AFV: each (CH?, hull/turret) branch read against C7.7; a Multiple
    Hit takes the better of two TK DR, the first locating the second (C3.8/C3.9)."""
    if ctx.kind != "armored":
        return
    target, ammo, he = ctx.target, ctx.ammo, ctx.ammo == "HE"
    for k in ("hull_af", "turret_af"):
        if target.get(k) is None:
            raise ValueError(f"Armored target needs {k} (AF of the facing being fired on).")
    facing = (target.get("facing") or "front").lower()
    if facing not in ("front", "side", "rear"):
        raise ValueError("target facing must be front, side or rear.")
    if ctx.basic_tk is not None:
        btk = {"basic": int(ctx.basic_tk), "table": ammo, "rule": "override"}
    else:
        btk = thtk.basic_to_kill(ctx.mm, ctx.barrel, ammo, ctx.nationality)
    case_d = None
    if ammo == "AP":
        case_d = thtk._ap_case_d(ctx.mm, ctx.range_hexes)
    elif ammo in ("APCR", "APDS"):
        case_d = thtk._apcr_case_d(ctx.mm, ctx.range_hexes, ammo)
    cannot_kill = ammo in ("AP", "APCR", "APDS") and case_d is None
    if cannot_kill:
        ctx.notes.append(f"Case D is NA at {ctx.range_hexes} hexes: this gun can't kill an AFV "
                         "at that range (C7.24).")

    branches = []
    for ch in (False, True):
        for loc in ("hull", "turret"):
            p = ctx.hits(ch=ch, loc=loc)
            if not p:
                continue
            mult = p - ctx.hits(ch=ch, loc=loc, multiple=False)
            single = p - mult
            tk = _final_tk(ctx, btk, case_d, facing, ch, loc)
            dist = {"No effect": Fraction(1)} if cannot_kill else thtk._tk_dist(tk["final"], loc, he, True)
            for r, q in dist.items():
                ctx.total[r] += single * q
            if mult and cannot_kill:
                ctx.total["No effect"] += mult
            elif mult:
                other = "turret" if loc == "hull" else "hull"
                ftk = {loc: tk["final"], other: _final_tk(ctx, btk, case_d, facing, ch, other)["final"]}
                for r, q in thtk._tk_multiple(ftk, loc, he, True).items():
                    ctx.total[r] += mult * q
            branches.append({
                "critical": ch, "location": loc, "probability": round(float(p), 4),
                "multiple_hit_probability": round(float(mult), 4),
                "final_tk": tk["final"], "tk_lines": tk["lines"],
                "effects": thtk._fmt(dist, thtk.VEHICLE_RESULTS),
            })
    ctx.result["to_kill"] = {"basic_tk": btk, "case_d": case_d, "branches": branches}
    ctx.notes.append("Possible Shock = the AFV takes a NTC; failure means Shock (C7.41).")


def unarmored(ctx: ShotContext) -> None:
    """Hits vs an unarmored vehicle: the table's unarmored Final TK#, x2 on a CH
    (C7.311, C7.321, C7.331, C7.342), Unarmored Vehicle Destruction Table."""
    if ctx.kind != "unarmored":
        return
    he = ctx.ammo == "HE"
    base = thtk.unarmored_final_tk(ctx.mm, ctx.ammo)
    branches = []
    for ch in (False, True):
        p = ctx.hits(ch=ch)
        if not p:
            continue
        ftk = base * 2 if ch else base
        single = ctx.hits(ch=ch, multiple=False)
        dist = thtk._tk_dist(ftk, "hull", he, False)
        for r, q in dist.items():
            ctx.total[r] += single * q
        if p - single:
            for r, q in thtk._tk_multiple({"hull": ftk, "turret": ftk}, "hull", he, False).items():
                ctx.total[r] += (p - single) * q
        branches.append({"critical": ch, "probability": round(float(p), 4),
                         "final_tk": ftk, "effects": thtk._fmt(dist, thtk.VEHICLE_RESULTS)})
    ctx.result["to_kill"] = {"unarmored_final_tk": base, "branches": branches}


def outcomes(ctx: ShotContext) -> None:
    """Overall result distribution (Miss included), most severe first."""
    ctx.result["outcomes"] = thtk._fmt(ctx.total, thtk.VEHICLE_RESULTS + ["Miss"])
