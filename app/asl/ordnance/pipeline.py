"""Ordnance (TH/TK) resolution as an explicit, ordered list of rule steps.

Same pattern as app/asl/attack/pipeline.py: each step is `step(ctx)`, reads the
shared ShotContext, adds to it or raises ValueError. The ORDER is the contract:
a step may only rely on what the steps above it set. To add a rule, write a
step (or extend one) and put it where its inputs are ready, then add a test.

compute_attack runs PREP_STEPS on the described situation first, then the
shot STEPS on the resulting inputs.
"""
from typing import Any, Dict

from app.asl.ordnance.context import AttackPrep, ShotContext
from app.asl.ordnance.steps.effects import (
    area_vs_vehicle, armored, outcomes, personnel, smoke, unarmored,
)
from app.asl.ordnance.steps.prep import listings, th_cases
from app.asl.ordnance.steps.setup import gun_size, legality, target_kind
from app.asl.ordnance.steps.to_hit import hit_tree, th_drm_ledger, th_number, to_hit_summary

PREP_STEPS = [
    listings,          # vehicles / Guns by name -> gun, AF, size, ROF, B#
    th_cases,          # C5/C6 case flags -> TH DRM ledger
]

STEPS = [
    # --- what fires at what ---
    gun_size,          # caliber + barrel; validate ammo, Target Type, range
    target_kind,       # armored / unarmored / personnel; Hull Down
    legality,          # C3.31-.33 Target Type and ammo restrictions
    # --- To Hit ---
    th_number,         # Basic TH# (C3, TH# color) + C4 mods = Modified TH#
    th_drm_ledger,     # caller's C5/C6 lines + TEM as Case Q
    hit_tree,          # 36 rolls: CH, Improbable, Multiple, hull/turret, HD
    to_hit_summary,    # report the To Hit half; start the result
    # --- effects (first applicable step finishes the shot) ---
    smoke,             # SMOKE: placed or missed
    area_vs_vehicle,   # Area Target Type vs a vehicle: IFT, not modelled
    personnel,         # IFT: HE FP / HE Equivalency, CH x2 + reversed TEM
    armored,           # TK tables: Basic TK#, CH, rear, Case D, AF; C7.7
    unarmored,         # unarmored Final TK#; Unarmored Destruction Table
    outcomes,          # overall distribution
]


def run_shot(**inputs: Any) -> Dict[str, Any]:
    ctx = ShotContext(**inputs)
    for step in STEPS:
        if ctx.done:
            break
        step(ctx)
    return ctx.result


def run_attack(**request: Any) -> Dict[str, Any]:
    prep = AttackPrep(kw=dict(request))
    for step in PREP_STEPS:
        step(prep)
    result = run_shot(**prep.kw)
    result["notes"] = prep.notes + prep.warnings + result["notes"]
    if prep.used:
        result["vehicles"] = prep.used
        result["notes"].insert(0, "Vehicle stats from the eASLRB Chapter H listing ("
                               + ", ".join(f"{u['name']} p.{u['page']}"
                                           for u in prep.used.values()) + ").")
    return result
