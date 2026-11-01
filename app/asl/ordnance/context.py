"""Shared state for the ordnance shot pipeline (see pipeline.py)."""
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class ShotContext:
    # ---- inputs (compute_shot's arguments) ----
    range_hexes: int
    gun: Optional[str] = None
    mm: Optional[int] = None
    barrel: str = ""
    nationality: str = ""
    year: Optional[int] = None
    th_color: Optional[str] = None
    ammo: str = "AP"
    target_type: str = "vehicle"
    th_drm: Sequence[Dict[str, Any]] = ()
    tem: int = 0
    target: Optional[Dict[str, Any]] = None
    basic_tk: Optional[int] = None
    b_number: int = 12
    rof: int = 0
    fp: Optional[float] = None
    effects_drm: int = 0
    double_lower_dr: bool = False

    # ---- reporting ----
    notes: List[str] = field(default_factory=list)

    # ---- target ----
    kind: str = ""                       # armored | unarmored | personnel
    hull_down: bool = False

    # ---- To Hit ----
    color: str = ""
    mth: Dict[str, Any] = field(default_factory=dict)     # modified_to_hit()
    drm_lines: List[Dict[str, Any]] = field(default_factory=list)
    total_drm: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)   # the 36-roll hit tree
    p_hit: Fraction = Fraction(0)

    # ---- effects ----
    total: Dict[str, Fraction] = field(default_factory=lambda: defaultdict(Fraction))
    result: Dict[str, Any] = field(default_factory=dict)
    done: bool = False      # an effects step has set the final outcomes

    def hits(self, **match) -> Fraction:
        """Probability of the hit events matching every given field."""
        return sum((e["p"] for e in self.events if e["hit"]
                    and all(e[k] == v for k, v in match.items())), Fraction(0))


@dataclass
class AttackPrep:
    """compute_attack's request while the front steps fill it in."""
    kw: Dict[str, Any]
    notes: List[str] = field(default_factory=list)
    used: Dict[str, Any] = field(default_factory=dict)     # listing rows used
    warnings: List[str] = field(default_factory=list)
