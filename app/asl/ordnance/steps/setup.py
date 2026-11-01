"""The firing gun, the target, and whether the shot is legal (C2, C3.31-.33)."""
from app.asl import thtk
from app.asl.ordnance.context import ShotContext


def gun_size(ctx: ShotContext) -> None:
    """Caliber + barrel from '75L' (or mm/barrel); validate ammo, Target Type, range."""
    t = thtk._load()
    if ctx.gun:
        ctx.mm, ctx.barrel = thtk.parse_gun(ctx.gun)
    if not ctx.mm or ctx.mm <= 0:
        raise ValueError("Give the gun size, e.g. gun='75L' or mm=75, barrel='L'.")
    ctx.barrel = (ctx.barrel or "").upper()
    if ctx.barrel not in t["barrels"]:
        raise ValueError(f"Invalid barrel {ctx.barrel!r}. One of {t['barrels']}.")
    ctx.ammo = (ctx.ammo or "").upper()
    if ctx.ammo not in t["ammo_types"]:
        raise ValueError(f"Invalid ammo {ctx.ammo!r}. One of {t['ammo_types']}.")
    if ctx.target_type not in t["target_types"]:
        raise ValueError(f"Invalid target_type {ctx.target_type!r}. One of {t['target_types']}.")
    if ctx.range_hexes is None or ctx.range_hexes < 0:
        raise ValueError("range_hexes must be >= 0.")


def target_kind(ctx: ShotContext) -> None:
    """armored | unarmored | personnel; Hull Down only counts for an AFV."""
    ctx.target = dict(ctx.target or {})
    ctx.kind = ctx.target.get("kind") or (
        "armored" if ctx.target_type == "vehicle" else "personnel")
    ctx.hull_down = bool(ctx.target.get("hull_down")) and ctx.kind == "armored"


def legality(ctx: ShotContext) -> None:
    """Target Type / ammo restrictions (C3.31 Vehicle, C3.32 Infantry, C3.33 Area)."""
    tt, ammo, kind = ctx.target_type, ctx.ammo, ctx.kind
    if ammo == "SMOKE" and tt != "area":
        raise ValueError("SMOKE must use the Area Target Type (C3.33).")
    if tt == "area" and ammo not in ("HE", "SMOKE"):
        raise ValueError("The Area Target Type is for HE or SMOKE only (C3.33).")
    if tt == "area" and ctx.range_hexes == 0:
        raise ValueError("The Area Target Type can't be used in the firer's own hex (C3.33).")
    if tt == "infantry" and kind == "armored":
        raise ValueError("The Infantry Target Type can't attack an AFV (C3.32/C3.41).")
    if tt == "vehicle" and kind == "personnel":
        raise ValueError("The Vehicle Target Type fires only at a vehicle (C3.31).")
    if tt == "infantry" and ammo != "HE" and kind == "personnel" and ammo != "SMOKE":
        ctx.notes.append("AP/HEAT on the Infantry Target Type is only allowed vs an unarmored "
                         "target (C3.32); HEAT vs Infantry also needs wall/building/rubble/"
                         "pillbox TEM (C8.31).")
