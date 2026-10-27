"""Fire attack resolution as an explicit, ordered list of rule steps.

Each step is a function `step(ctx)` that reads the shared AttackContext,
adds to it (DRM lines, caveats, warnings) or raises NoAttack. The ORDER is
the contract: a step may only rely on what the steps above it set. To add a
rule, write a step (or extend one) and put it where its inputs are ready —
e.g. Wall Advantage (B9.32) would go between `walls` and `tem`.

The output order of warnings, caveats and DRM lines follows step order, so
moving a step changes what users see: keep that in mind and update tests.
"""
from typing import Any, Dict, List, Optional

from app.asl.attack.context import AttackContext, NoAttack  # noqa: F401
from app.asl.attack.steps.drm import (
    encircled_target, ffmo_ffnam, firer_modifiers, hindrance, tem,
)
from app.asl.attack.steps.firers import (
    eligible_firers, fire_strength, firing_side, leadership, point_blank,
)
from app.asl.attack.steps.los import (
    grid_trace, locate, measure_range, overlays, vasl_facts, vasl_verdict,
)
from app.asl.attack.steps.odds import cowering, odds, skis, target_morale
from app.asl.attack.steps.target import concealment, target_units
from app.asl.attack.steps.walls import walls

STEPS = [
    # --- where, and can they see each other ---
    locate,            # find both hexes in the save
    vasl_verdict,      # VASL's LOS check: blocked -> NoAttack (A6)
    measure_range,     # range in hexes
    walls,             # wall/hedge hexsides: target TEM, B9.2 warnings
    grid_trace,        # no VASL check: obstacles + hindrances from board files
    vasl_facts,        # VASL map facts: levels, hexside, roadblocks (wins)
    overlays,          # overlay caveat only if the save has one here
    # --- who fires, with what ---
    firing_side,       # side with most Good Order personnel; Melee warnings
    eligible_firers,   # exclusions; NoAttack if nobody can fire
    point_blank,       # PBF / TPBF (A7.21, A7.211); needs levels
    fire_strength,     # per-unit FP, Long Range, SW manning, Assault Fire
    leadership,        # directing leader (A7.53); needed by cowering
    # --- the target ---
    target_units,      # who is attacked, their state
    concealment,       # Area Fire halving (A7.23)
    # --- DRM ledger (order = order of lines on the card) ---
    hindrance,         # LOS Hindrance (A6.7)
    tem,               # terrain / hexside / entrenchment TEM (B9.31, B27)
    firer_modifiers,   # CX, leadership direction, encircled firer
    ffmo_ffnam,        # needs final TEM + hindrance (A4.6)
    encircled_target,  # morale -1 (A7.7)
    # --- odds ---
    cowering,          # nationality exemption (A7.9)
    target_morale,     # warn when no morale is available
    skis,              # ski-mode notes (E4)
    odds,              # IFT column + 36-roll distribution -> result
]


def resolve(state: Dict[str, Any], firing_hex: str, target_hex: str,
            phase: str = "prep", firing_unit_filter: Optional[str] = None,
            native_los: Optional[Dict[str, Any]] = None,
            selected_firers: Optional[List[str]] = None,
            selected_targets: Optional[List[str]] = None) -> Dict[str, Any]:
    ctx = AttackContext(state=state, firing_hex=firing_hex,
                        target_hex=target_hex, phase=phase,
                        firing_unit_filter=firing_unit_filter,
                        native_los=native_los, selected_firers=selected_firers,
                        selected_targets=selected_targets)
    for step in STEPS:
        step(ctx)
    return ctx.result
