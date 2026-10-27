"""Shared state for the attack pipeline (see pipeline.py)."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.asl.attack_resolver import _LOS_CLEAR_ASSUMPTION


class NoAttack(ValueError):
    """A step found that there is no legal attack (LOS blocked, no eligible
    firers, ...). Subclasses ValueError so existing callers keep working."""

    def __init__(self, message: str, rule: Optional[str] = None):
        super().__init__(message)
        self.rule = rule


class Caveats:
    """Assumptions reported with the result, in order.

    The six keyed defaults are the standing caveats every attack starts
    with; a step replaces one by key (set) or drops it (set to None) instead
    of overwriting list positions. Unkeyed caveats are appended after them,
    in the order steps add them.
    """

    DEFAULTS: Tuple[Tuple[str, str], ...] = (
        ("los", _LOS_CLEAR_ASSUMPTION),
        ("hindrance",
         "LOS Hindrances in INTERVENING hexes (grain, brush, in-season "
         "orchard, SMOKE, wrecks: +1 each, B.6/A6.7) are NOT included — add "
         "them to the DRM manually if present along the path."),
        ("overlays", "Map overlays are NOT applied to terrain."),
        ("entrenchment",
         "Entrenchment containment is read from VASL stack order: a unit "
         "BELOW a Foxhole/Trench counter in its stack is IN it; a unit above "
         "the counter (or not in its stack) is NOT (B27)."),
        ("levels",
         "Same-level fire assumed except where hex base elevation says "
         "otherwise; building upper levels are not modeled."),
        ("fire_group",
         "Fire group = all eligible units in the single firing hex; "
         "multi-hex Fire Groups (A7.55) are not supported."),
    )

    def __init__(self):
        self._keyed: Dict[str, Optional[str]] = dict(self.DEFAULTS)
        self._extra: List[str] = []

    def set(self, key: str, text: Optional[str]) -> None:
        if key not in self._keyed:
            raise KeyError(f"unknown caveat {key!r}")
        self._keyed[key] = text

    def append(self, text: str) -> None:
        self._extra.append(text)

    def as_list(self) -> List[str]:
        return [t for t in list(self._keyed.values()) + self._extra if t]


@dataclass
class AttackContext:
    # ---- inputs ----
    state: Dict[str, Any]
    firing_hex: str
    target_hex: str
    phase: str
    firing_unit_filter: Optional[str] = None
    native_los: Optional[Dict[str, Any]] = None
    selected_firers: Optional[List[str]] = None
    selected_targets: Optional[List[str]] = None

    # ---- reporting ----
    warnings: List[str] = field(default_factory=list)
    caveats: Caveats = field(default_factory=Caveats)

    # ---- locate ----
    fkey: str = ""
    tkey: str = ""
    fentry: Dict[str, Any] = field(default_factory=dict)
    tentry: Dict[str, Any] = field(default_factory=dict)
    fbase: str = ""
    flabel: str = ""
    tbase: str = ""
    tlabel: str = ""

    # ---- LOS ----
    vasl_los: bool = False                    # VASL checked this pair: clear
    facts: Optional[Dict[str, Any]] = None    # extension >= 0.5 map facts
    hindrance: int = 0
    hindrance_label: Optional[str] = None
    hexside_crossings: Optional[List[Dict[str, Any]]] = None
    hexside_tem: int = 0
    hexside_tem_label: Optional[str] = None

    # ---- range ----
    rng: int = 0
    range_method: str = ""
    pbf_mode: str = "none"

    # ---- firers ----
    firing_side: str = ""
    units: List[Dict[str, Any]] = field(default_factory=list)
    personnel: List[Tuple[Dict[str, Any], Dict[str, Any]]] = field(default_factory=list)
    sws: List[Tuple[Dict[str, Any], Dict[str, Any]]] = field(default_factory=list)
    leaders: List[Dict[str, Any]] = field(default_factory=list)
    excluded: List[Dict[str, Any]] = field(default_factory=list)
    skis_worn: bool = False
    skis_carried: bool = False
    ift_units: List[Dict[str, Any]] = field(default_factory=list)
    firer_rows: List[Dict[str, Any]] = field(default_factory=list)
    cx_present: bool = False
    encircled_firer: bool = False
    leadership: int = 0
    leader_directs: bool = False

    # ---- target ----
    t_units: List[Dict[str, Any]] = field(default_factory=list)
    target_personnel: List[Tuple[Dict[str, Any], Dict[str, Any]]] = field(default_factory=list)
    moved_target_count: int = 0
    target_is_moving: bool = False
    area_fire_halvings: int = 0
    t_markers: Any = field(default_factory=list)

    # ---- DRM ----
    drm_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    other_drm: List[Dict[str, Any]] = field(default_factory=list)
    terrain_tem_value: int = 0
    terrain_tem_label: Optional[str] = None
    tem_groups: List[Dict[str, Any]] = field(default_factory=list)
    entrenched: Any = False
    ffnam: bool = False
    ffmo: bool = False
    firer_cowering_exempt: bool = False

    # ---- output ----
    result: Optional[Dict[str, Any]] = None

    def exclude(self, unit: Dict[str, Any], reason: str) -> None:
        self.excluded.append({"name": unit.get("name"), "side": unit.get("side"),
                              "reason": reason})
