"""The attack pipeline's structure: step order and keyed caveats."""
from app.asl.attack.context import Caveats, NoAttack
from app.asl.attack.pipeline import STEPS


def _pos(name):
    return [s.__name__ for s in STEPS].index(name)


def test_step_order_respects_dependencies():
    # each pair: (needs, must come after)
    for later, earlier in [
        ("vasl_facts", "vasl_verdict"),   # facts only count when VASL saw LOS
        ("point_blank", "vasl_facts"),    # PBF level test uses VASL heights
        ("fire_strength", "point_blank"), # per-unit FP needs the PBF mode
        ("tem", "walls"),                 # hexside TEM is max'd into TEM
        ("tem", "vasl_facts"),            # ... after VASL may override it
        ("ffmo_ffnam", "tem"),            # FFMO negated by TEM ...
        ("ffmo_ffnam", "hindrance"),      # ... or by a hindrance
        ("odds", "leadership"),           # direction prevents cowering
    ]:
        assert _pos(later) > _pos(earlier), f"{later} must run after {earlier}"
    assert STEPS[0].__name__ == "locate" and STEPS[-1].__name__ == "odds"


def test_caveats_replace_by_key_keep_position():
    c = Caveats()
    c.append("extra")
    c.set("hindrance", "counted by VASL: +1")
    c.set("overlays", None)
    out = c.as_list()
    assert out[1] == "counted by VASL: +1"
    assert not any("overlays are NOT applied" in x for x in out)
    assert out[-1] == "extra"


def test_no_attack_is_a_value_error():
    e = NoAttack("LOS blocked", rule="B9.2")
    assert isinstance(e, ValueError) and e.rule == "B9.2"
