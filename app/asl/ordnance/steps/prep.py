"""compute_attack's front steps: turn a described situation into shot inputs."""
from app.asl import thtk
from app.asl.ordnance.context import AttackPrep


def listings(prep: AttackPrep) -> None:
    """Vehicles / Guns named in the request -> gun, AF, Target Size, ROF, B#
    from the Chapter H listings (explicit values win)."""
    looked_up = thtk._apply_vehicles(prep.kw)
    prep.notes.extend(looked_up["notes"])
    prep.used.update(looked_up["used"])


def th_cases(prep: AttackPrep) -> None:
    """The C5/C6 To Hit case flags -> an itemized TH DRM ledger."""
    kw = prep.kw
    drm_kw = {k: kw.pop(k) for k in list(kw) if k in thtk._DRM_KEYS
              and k not in ("target_type", "range_hexes")}
    ledger = thtk.build_th_drm(target_type=kw.get("target_type", "vehicle"),
                               range_hexes=kw.get("range_hexes", 0), **drm_kw)
    kw["th_drm"] = ledger["lines"]
    kw["double_lower_dr"] = ledger["double_lower_dr"]
    prep.warnings.extend(ledger["warnings"])
