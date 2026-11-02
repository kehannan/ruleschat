#!/usr/bin/env python
"""
Tests for the TH/TK ordnance engine (`app.asl.thtk`). Golden cases are the
rulebook's own examples (C3.6, C3.7, C3.32, C4.5, C7.24) plus table spot checks
against the Chapter C divider (C3, C4, C7.31-.34, C7.7, C8.31).

Pure engine — no network, no app server. Runnable directly
(`python tests/test_thtk.py`) or under pytest.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asl import thtk
from app.asl.thtk import compute_shot


def _armor(af=8, facing="front", **kw):
    return {"kind": "armored", "facing": facing, "hull_af": af, "turret_af": af, **kw}


def _outcome(r, name):
    return next((o["probability"] for o in r["outcomes"] if o["result"] == name), 0.0)


def _total(r):
    return sum(o["probability"] for o in r["outcomes"])


# --- To Hit ------------------------------------------------------------------

def test_c45_example_modified_th():
    # C4.5 EX: German 40LL firing APCR at a tank 25 hexes away: Basic 6, Modified 3.
    r = compute_shot(gun="40LL", nationality="German", ammo="APCR", range_hexes=25,
                     target=_armor(3))
    assert r["to_hit"]["basic"] == 6
    assert r["to_hit"]["modified_th"] == 3


def test_red_vs_black_basic_th():
    black = compute_shot(gun="75", nationality="German", range_hexes=20, target=_armor())
    red = compute_shot(gun="75", nationality="Russian", range_hexes=20, target=_armor())
    assert (black["to_hit"]["basic"], red["to_hit"]["basic"]) == (7, 6)


def test_american_th_color_by_year():
    early = compute_shot(gun="75", nationality="American", year=1943, range_hexes=20,
                         target=_armor())
    late = compute_shot(gun="75", nationality="American", year=1944, range_hexes=20,
                        target=_armor())
    assert early["to_hit"]["color"] == "red" and late["to_hit"]["color"] == "black"


def test_small_caliber_mods_extend_past_printed_table():
    # C4.2: -1 per 12 hexes (or fraction) beyond 12 -> -5 at 61 hexes, plus -1 for <=40mm.
    r = compute_shot(gun="37", nationality="German", ammo="HE", target_type="infantry",
                     range_hexes=61)
    mods = {m["label"]: m["mod"] for m in r["to_hit"]["modifications"]}
    assert mods["≤57mm gun"] == -5 and mods["≤40mm gun"] == -1


def test_smoke_needs_area_and_gets_plus_two():
    with pytest.raises(ValueError):
        compute_shot(gun="75", ammo="SMOKE", target_type="vehicle", range_hexes=5)
    r = compute_shot(gun="75", nationality="German", ammo="SMOKE", target_type="area",
                     range_hexes=5)
    assert r["to_hit"]["modified_th"] == 9  # Area 7 + SMOKE 2 (C4.4)


def test_c36_improbable_hit():
    # C3.6 EX: German L Gun, Infantry Target Type at 37 hexes -> Modified TH# 3,
    # +2 DRM: only an Original 2 then a dr of 1-3 hits.
    r = compute_shot(gun="75L", nationality="German", ammo="HE", target_type="infantry",
                     range_hexes=37, th_drm=[{"label": "DRM", "drm": 2}])
    assert r["to_hit"]["modified_th"] == 3
    assert r["to_hit"]["improbable"]
    assert r["to_hit"]["p_hit"] == pytest.approx(3 / 216, abs=1e-4)


@pytest.mark.parametrize("drm,ch_rolls", [(1, 1), (0, 3), (-1, 6)])
def test_c37_infantry_target_type_critical_hits(drm, ch_rolls):
    # C3.7 EX: Modified TH# 8 at 2 hexes; CH on Original 2 (DRM +1), <=3 (DRM 0),
    # <=4 (DRM -1) -- i.e. Final DR <= 3 (< half of 8).
    r = compute_shot(gun="75", nationality="German", ammo="HE", target_type="infantry",
                     range_hexes=2, th_drm=[{"label": "DRM", "drm": drm}])
    assert r["to_hit"]["modified_th"] == 8
    assert r["to_hit"]["p_critical"] == pytest.approx(ch_rolls / 36, abs=1e-4)


def test_vehicle_target_ch_on_original_2_and_hit_location():
    r = compute_shot(gun="75L", nationality="German", range_hexes=8, target=_armor())
    th = r["to_hit"]
    assert th["p_critical"] == pytest.approx(1 / 36, abs=1e-4)
    # Modified TH# 9: 30 hitting combos; 15 have colored < white (turret).
    assert th["p_hit"] == pytest.approx(30 / 36, abs=1e-4)
    assert th["p_turret"] == pytest.approx(13 / 36, abs=1e-4)


def test_twelve_is_not_an_automatic_miss():
    # No rule makes an Original 12 miss; with B11 it malfunctions but still resolves (A9.7).
    r = compute_shot(gun="75L", nationality="German", range_hexes=0,
                     th_drm=[{"label": "DRM", "drm": -3}], b_number=11, target=_armor())
    assert r["to_hit"]["p_hit"] == pytest.approx(1.0)
    assert r["to_hit"]["p_malfunction"] == pytest.approx(3 / 36, abs=1e-4)


def test_hull_down_hull_hits_miss():
    open_ = compute_shot(gun="75L", nationality="German", range_hexes=8, target=_armor())
    hd = compute_shot(gun="75L", nationality="German", range_hexes=8,
                      target=_armor(hull_down=True))
    assert hd["to_hit"]["p_hull"] == 0.0
    # Only turret hits plus the CH (which strikes the turret vs HD) remain.
    assert hd["to_hit"]["p_hit"] == pytest.approx(open_["to_hit"]["p_turret"] + 1 / 36, abs=1e-4)


def test_multiple_hits_only_15_to_40mm():
    small = compute_shot(gun="37L", nationality="German", range_hexes=3, target=_armor(2))
    big = compute_shot(gun="75L", nationality="German", range_hexes=3, target=_armor(2))
    assert small["to_hit"]["p_multiple"] > 0
    assert big["to_hit"]["p_multiple"] == 0


def test_rof_retention():
    r = compute_shot(gun="37L", nationality="German", range_hexes=3, rof=3, target=_armor(2))
    assert r["to_hit"]["p_rof_retained"] == pytest.approx(0.5)


# --- To Kill -----------------------------------------------------------------

def test_c724_example_final_tk():
    # C7.24 EX: 50L AP vs rear of a KV-1E at 25 hexes, rear hull AF 8:
    # Basic 13 +1 (rear) -2 (Case D) = 12, Final TK# 4.
    r = compute_shot(gun="50L", nationality="German", range_hexes=25,
                     target=_armor(8, facing="rear"))
    normal_hull = next(b for b in r["to_kill"]["branches"]
                       if b["location"] == "hull" and not b["critical"])
    assert normal_hull["final_tk"] == 4
    ch = next(b for b in r["to_kill"]["branches"] if b["critical"])
    assert ch["final_tk"] == 13 * 2 + 1 - 2 - 8  # CH doubles before modifiers (C7.23)


@pytest.mark.parametrize("gun,nat,ammo,expected", [
    ("76L", "Russian", "AP", 13),      # brown 76L (C7.31 text)
    ("76L", "German", "AP", 17),
    ("76LL", "Russian", "AP", 16),
    ("90L", "Italian", "AP", 20),
    ("90L", "American", "AP", 21),
    ("75*", "Japanese", "AP", 10),     # Year-38 Type (12) needs an override
    ("88", "British", "AP", 13),
    ("76L", "American", "APCR", 22),   # olive 76L
    ("76L", "Russian", "APCR", 14),
    ("57L", "British", "APDS", 18),    # D57L
    ("75L", "German", "HEAT", 13),
    ("76L", "German", "HE", 7),        # C7.34 EX: 76L HE -> 70+ column
])
def test_basic_tk_lookup(gun, nat, ammo, expected):
    mm, barrel = thtk.parse_gun(gun)
    assert thtk.basic_to_kill(mm, barrel, ammo, nat)["basic"] == expected


def test_gun_missing_from_table_asks_for_override():
    with pytest.raises(ValueError, match="basic_tk"):
        compute_shot(gun="88", nationality="German", range_hexes=5, target=_armor())
    r = compute_shot(gun="88", nationality="German", range_hexes=5, target=_armor(),
                     basic_tk=13)
    assert r["to_kill"]["basic_tk"]["rule"] == "override"


def test_case_d_na_means_no_kill():
    # 37-57mm Case D is -4 at 49-54 hexes but NA from 61 (C7.31).
    r = compute_shot(gun="37L", nationality="German", range_hexes=62, target=_armor(1),
                     th_drm=[{"label": "DRM", "drm": -10}])
    assert _outcome(r, "Burn") == 0 and _outcome(r, "Elim") == 0


def test_tk_result_table_rows():
    # Final TK# 9, hull: <=4 Burn, 5-8 Elim, 9 Immob, 10 Possible Shock (AP), 12 Dud.
    res = lambda dr, he=False, loc="hull": thtk._tk_result(dr, dr, 9, loc, he, True)
    assert [res(4), res(5), res(8), res(9), res(10), res(11)] == \
        ["Burn", "Elim", "Elim", "Immobilized", "Possible Shock", "No effect"]
    assert res(9, loc="turret") == "Shock"
    assert res(10, he=True, loc="turret") == "Shock"   # HE: one > Final TK#
    assert thtk._tk_result(12, 12, 30, "hull", False, True) == "No effect"  # Dud


def test_unarmored_vehicle():
    r = compute_shot(gun="75", nationality="German", ammo="AP", range_hexes=5,
                     target={"kind": "unarmored"})
    assert r["to_kill"]["unarmored_final_tk"] == 9          # 65-84mm
    ch = next(b for b in r["to_kill"]["branches"] if b["critical"])
    assert ch["final_tk"] == 18
    assert "Possible Shock" not in {o["result"] for o in r["outcomes"]}


def test_outcomes_sum_to_one():
    for kw in [
        dict(gun="75L", nationality="German", range_hexes=8, target=_armor()),
        dict(gun="37L", nationality="German", range_hexes=3, target=_armor(2)),
        dict(gun="37L", nationality="German", range_hexes=3, target=_armor(2, hull_down=True)),
        dict(gun="20L", nationality="German", ammo="HE", target_type="infantry", range_hexes=3),
        dict(gun="75", nationality="Russian", ammo="HE", target_type="area", range_hexes=10, tem=2),
        dict(gun="57", nationality="British", ammo="AP", range_hexes=40,
             th_drm=[{"label": "DRM", "drm": 3}], target=_armor(4)),
    ]:
        assert _total(compute_shot(**kw)) == pytest.approx(1.0, abs=2e-3), kw


# --- vs personnel (IFT) ------------------------------------------------------

def test_c332_example_he_fp_and_th():
    # C3.32 EX: Russian 57LL HE on the Infantry Target Type at 6 hexes: TH# 8, IFT 6 column.
    r = compute_shot(gun="57LL", nationality="Russian", ammo="HE", target_type="infantry",
                     range_hexes=6)
    assert r["to_hit"]["modified_th"] == 8
    normal = next(b for b in r["to_kill"]["branches"] if not b["critical"])
    assert normal["ift_column"] == 6


def test_area_target_halves_fp_and_ch_doubles_and_reverses_tem():
    r = compute_shot(gun="75", nationality="German", ammo="HE", target_type="area",
                     range_hexes=10, tem=2)
    by_ch = {b["critical"]: b for b in r["to_kill"]["branches"]}
    assert by_ch[False]["ift_column"] == 6 and by_ch[False]["effects_drm"] == 2
    assert by_ch[True]["ift_column"] == 24 and by_ch[True]["effects_drm"] == -2
    # TEM is not a TH DRM on the Area Target Type (C3.331).
    assert r["to_hit"]["total_drm"] == 0


def test_infantry_target_type_tem_is_th_drm():
    r = compute_shot(gun="75", nationality="German", ammo="HE", target_type="infantry",
                     range_hexes=4, tem=1)
    assert r["to_hit"]["total_drm"] == 1


def test_heat_he_equivalency_vs_personnel():
    r = compute_shot(gun="75", nationality="German", ammo="HEAT", target_type="infantry",
                     range_hexes=4)
    assert r["to_kill"]["full_fp"] == 8   # column left of 75mm's 12


def test_target_type_legality():
    with pytest.raises(ValueError):
        compute_shot(gun="75", ammo="HE", target_type="infantry", range_hexes=4,
                     target=_armor())
    with pytest.raises(ValueError):
        compute_shot(gun="75", ammo="AP", target_type="area", range_hexes=4)
    with pytest.raises(ValueError):
        compute_shot(gun="75", ammo="HE", target_type="area", range_hexes=0)


# --- C5/C6 case ledger + agentic tools ---------------------------------------

def test_build_th_drm_cases():
    led = thtk.build_th_drm(target_type="vehicle", range_hexes=2, gun_type="NT",
                            ca_hexspines=2, firer_in_woods_building_rubble=True,
                            acquired=-1, target_size=1, buttoned_up=True)
    by_rule = {l["rule"]: l["drm"] for l in led["lines"]}
    assert by_rule["C5.1"] == (3 + 1) * 2      # NT first hexspine +3, next +1, x2 woods
    assert by_rule["C6.3"] == -1               # Point Blank at 2 hexes
    assert by_rule["C6.5"] == -1 and by_rule["C6.7"] == 1 and by_rule["C5.9"] == 1


def test_c53_bounding_fire_examples():
    # C5.3 EX: AFPh after moving: Stabilized +3, T/ST +4, NT +5.
    for gun_type, drm in (("stabilized", 3), ("T", 4), ("ST", 4), ("NT", 5)):
        led = thtk.build_th_drm(range_hexes=6, gun_type=gun_type, bounding_fire=True, afph=True)
        assert sum(l["drm"] for l in led["lines"]) == drm, gun_type


def test_point_blank_na_vs_moving_target_and_area():
    assert not any(l["rule"] == "C6.3" for l in
                   thtk.build_th_drm(range_hexes=1, target_moving="moving")["lines"])
    assert not any(l["rule"] == "C6.3" for l in
                   thtk.build_th_drm(range_hexes=1, target_type="area")["lines"])


def test_motion_firer_doubles_lower_dr():
    base = compute_shot(gun="75L", nationality="German", range_hexes=6, target=_armor())
    moving = thtk.compute_attack(gun="75L", nationality="German", range_hexes=6,
                                 target=_armor(), bounding_fire=True, motion_firer=True)
    assert moving["to_hit"]["p_hit"] < base["to_hit"]["p_hit"]
    assert any("C5.35" in n for n in moving["notes"])


def test_tem_not_th_drm_when_it_makes_target_hull_down():
    r = compute_shot(gun="75L", nationality="German", range_hexes=6, tem=1,
                     target=_armor(hull_down=True))
    assert r["to_hit"]["total_drm"] == 0


def test_quick_odds_matches_full_engine():
    full = compute_shot(gun="75L", nationality="German", range_hexes=8, target=_armor())
    quick = thtk.quick_odds(modified_th=9, drm=0, final_tk=9, ch_final_tk=26)
    assert quick["p_hit"] == full["to_hit"]["p_hit"]
    assert {o["result"]: o["probability"] for o in quick["outcomes"]} == pytest.approx(
        {o["result"]: o["probability"] for o in full["outcomes"]}, abs=1e-4)


def test_tools_registered_and_callable():
    from app.asl.tools import CALC_TOOL_NAMES, TOOL_SCHEMAS, execute_tool
    names = {s["name"] for s in TOOL_SCHEMAS}
    assert {"ordnance_attack", "thtk_odds"} <= names & CALC_TOOL_NAMES
    r = execute_tool("ordnance_attack", {"gun": "75L", "nationality": "German",
                                         "range_hexes": 8, "acquired": -1,
                                         "target": {"kind": "armored", "hull_af": 8,
                                                    "turret_af": 8}})
    assert r["to_hit"]["total_drm"] == -1
    assert "error" in execute_tool("ordnance_attack", {"gun": "88", "nationality": "German",
                                                       "range_hexes": 8,
                                                       "target": _armor()})
    assert execute_tool("thtk_odds", {"modified_th": 9, "final_tk": 6})["p_hit"] > 0


def test_tool_schema_properties_match_engine():
    from app.asl.tools import TOOL_SCHEMAS
    import inspect
    props = set(next(s for s in TOOL_SCHEMAS if s["name"] == "ordnance_attack")
                ["parameters"]["properties"])
    accepted = (set(inspect.signature(thtk.compute_shot).parameters) | thtk._DRM_KEYS
                | thtk.VEHICLE_KEYS) \
        - {"th_drm", "double_lower_dr", "mm", "barrel"}
    assert props <= accepted, props - accepted


# --- Chapter H vehicle listings ----------------------------------------------

@pytest.fixture
def listing():
    from app.asl import vehicles
    vehicles.load(str(Path(__file__).resolve().parent / "fixtures" / "vehicles_fixture.json"))
    yield vehicles
    vehicles.reset()


def test_vehicle_find_alias_prefix_and_ambiguity(listing):
    hit = listing.find("Panther")
    assert hit["vehicle"]["hull_front"] == 18 and hit["vehicle"]["turret_front"] == 14
    assert listing.find("pzkpfw ivh")["vehicle"]["name"] == "PzKpfw IVH"
    assert listing.find("KV-1E")["vehicle"]["hull_side"] == 8      # C7.24 EX: rear hull AF 8
    assert "candidates" in listing.find("Sherman")
    assert "error" in listing.find("Maus")


def test_turret_armor_from_ta(listing):
    ivh = listing.find("PzKpfw IVH")["vehicle"]          # AF 8/3, TA -F/+SR (D1.63-.64)
    assert (ivh["turret_front"], ivh["turret_side"]) == (6, 4)
    assert listing.armor_for(ivh, "rear") == {"hull_af": 3, "turret_af": 4}


def test_compute_attack_reads_target_vehicle(listing):
    r = thtk.compute_attack(gun="76LL", nationality="British", range_hexes=6,
                            target_vehicle="Panther")
    assert r["target"]["hull_af"] == 18 and r["target"]["turret_af"] == 14
    assert {"label": "Case P: target size", "drm": -1, "rule": "C6.7"} in r["to_hit"]["drm"]
    turret = next(b for b in r["to_kill"]["branches"]
                  if b["location"] == "turret" and not b["critical"])
    assert turret["final_tk"] == 23 - 14
    assert r["vehicles"]["target"]["page"]


def test_explicit_values_override_listing(listing):
    r = thtk.compute_attack(gun="76LL", nationality="British", range_hexes=6,
                            target_vehicle="Panther", target_size=0,
                            target={"turret_af": 11})
    assert r["target"]["turret_af"] == 11 and r["to_hit"]["total_drm"] == 0


def test_firer_vehicle_supplies_gun(listing):
    r = thtk.compute_attack(firer_vehicle="PzKpfw IVH", range_hexes=8,
                            target_vehicle="T-34 M43", target={"facing": "side"})
    assert (r["gun"], r["nationality"]) == ("75L", "German")
    assert r["target"]["hull_af"] == 6


def test_unarmored_target_vehicle(listing):
    truck = next(v for v in listing.load() if v["armor"] == "unarmored")
    r = thtk.compute_attack(gun="75", nationality="Russian", ammo="HE", range_hexes=5,
                            target_vehicle=truck["name"])
    assert r["target"]["kind"] == "unarmored"


def test_ambiguous_vehicle_is_a_tool_error(listing):
    from app.asl.tools import execute_tool
    r = execute_tool("ordnance_attack", {"gun": "75L", "range_hexes": 5,
                                         "target_vehicle": "Sherman"})
    assert "ambiguous" in r["error"]


def test_missing_listing_store_falls_back(monkeypatch):
    from app.asl import vehicles
    monkeypatch.setattr(vehicles, "_PATH", Path("/nonexistent/vehicles.json"))
    vehicles.reset()
    try:
        assert "error" in vehicles.find("Panther")
        r = thtk.compute_attack(gun="76LL", nationality="British", range_hexes=6,
                                target={"kind": "armored", "hull_af": 18, "turret_af": 14})
        assert r["to_hit"]["p_hit"] > 0
    finally:
        vehicles.reset()



def test_us_listing_from_decoded_fonts(listing):
    # The U.S. listing is decoded from fonts without a character map.
    m4a1 = listing.find("M4A1", "U.S.")["vehicle"]       # AF 11/4, TA -F/+SR
    assert (m4a1["hull_front"], m4a1["hull_side"]) == (11, 4)
    assert (m4a1["turret_front"], m4a1["turret_side"]) == (8, 6)
    assert listing.find("Hellcat")["vehicle"]["name"] == "M18 GMC"
    assert "candidates" in listing.find("Sherman", "U.S.")


def test_us_glyph_tables_are_in_cp1252_order():
    import json
    tables = json.loads((Path(__file__).resolve().parent.parent / "scripts" / "data"
                         / "us_listing_glyphs.json").read_text())
    for tag, chars in tables.items():
        if tag.startswith("_"):
            continue
        body = chars.lstrip("\x00")
        keys = [c.encode("cp1252", "ignore") or b"\xff" for c in body]
        assert keys == sorted(keys), tag
        assert len(set(body)) == len(body), tag



def test_gun_lookup_and_firer_gun(listing):
    g = listing.find_gun("17pdr")["gun"]
    assert (g["name"], g["gun"]) == ("OQF 17-Pounder", "76LL")
    r = thtk.compute_attack(firer_gun="17pdr", range_hexes=6, target_vehicle="Panther")
    assert (r["gun"], r["nationality"]) == ("76LL", "British")
    assert {"label": "Case P: target size", "drm": -1, "rule": "C6.7"} in r["to_hit"]["drm"]


def test_firer_name_prefers_gun_over_partial_vehicle_match(listing):
    # "PaK 40" is a German Gun even though "M3A3(a) PaK 40" contains it.
    r = thtk.compute_attack(firer_vehicle="PaK 40", range_hexes=8, target_vehicle="T-34 M43")
    assert r["vehicles"]["firer"]["name"] == "7.5cm PaK 40" and r["gun"] == "75L"


def test_target_gun_needs_infantry_target_type(listing):
    with pytest.raises(ValueError, match="Infantry"):
        thtk.compute_attack(firer_vehicle="PzKpfw IVH", range_hexes=5, target_gun="PaK 40")
    r = thtk.compute_attack(firer_vehicle="PzKpfw IVH", ammo="HE", target_type="infantry",
                            range_hexes=5, target_gun="PaK 40")
    assert {"label": "Case P: target size", "drm": 1, "rule": "C6.7"} in r["to_hit"]["drm"]



def test_listing_overrides_set_and_add(tmp_path, monkeypatch):
    import json
    from app.asl import vehicles
    fixes = tmp_path / "overrides.json"
    fixes.write_text(json.dumps({
        "vehicles": [{"name": "PzKpfw IVH", "set": {"turret_front": 8}, "reason": "test fix"}],
        "add_vehicles": [{"name": "Test Tank", "nationality": "German", "type": "MT",
                          "hull_front": 4, "hull_side": 2, "turret_front": 4,
                          "turret_side": 2, "armor": "full", "gun": "75", "size": 0,
                          "page": 0}],
    }))
    monkeypatch.setattr(vehicles, "OVERRIDES_PATH", fixes)
    vehicles.load(str(Path(__file__).resolve().parent / "fixtures" / "vehicles_fixture.json"))
    try:
        ivh = vehicles.find("PzKpfw IVH")["vehicle"]
        assert ivh["turret_front"] == 8 and ivh["override"] == "test fix"
        assert vehicles.find("Test Tank")["vehicle"]["hull_front"] == 4
    finally:
        vehicles.reset()


def test_committed_overrides_and_aliases_are_valid():
    import json
    from app.asl import vehicles
    fixes = json.loads(vehicles.OVERRIDES_PATH.read_text())
    assert set(fixes) >= {"vehicles", "guns", "add_vehicles", "add_guns"}
    for kind in ("vehicles", "guns"):
        for fix in fixes[kind]:
            assert fix.get("name") and fix.get("set") and fix.get("reason"), fix
    aliases = json.loads(vehicles.ALIASES_PATH.read_text())
    for kind in ("vehicles", "guns"):
        keys = [vehicles._norm(k) for k in aliases[kind]]
        assert len(keys) == len(set(keys)), f"duplicate {kind} alias after normalizing"
        assert all(isinstance(v, list) and v for v in aliases[kind].values())


def test_th_case_values_come_from_tables(monkeypatch):
    t = thtk._load()
    monkeypatch.setitem(t["th_cases"]["I"], "drm", 5)
    lines = thtk.build_th_drm(range_hexes=6, buttoned_up=True)["lines"]
    assert {"label": "Case I: Buttoned Up", "drm": 5, "rule": "C5.9"} in lines



def test_ordnance_pipeline_contract():
    # compute_shot / compute_attack are thin wrappers over the step pipeline.
    from app.asl.ordnance import pipeline
    names = [f.__name__ for f in pipeline.STEPS]
    assert names[:3] == ["gun_size", "target_kind", "legality"]
    assert names.index("hit_tree") < names.index("to_hit_summary") < names.index("armored")
    assert names[-1] == "outcomes"
    assert all(f.__doc__ for f in pipeline.STEPS + pipeline.PREP_STEPS)
    shot = dict(gun="75L", nationality="German", range_hexes=8,
                target={"kind": "armored", "hull_af": 8, "turret_af": 8})
    assert pipeline.run_shot(**shot) == compute_shot(**shot)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_ordnance_attack_takes_acquisition_from_vasl_counter():
    from app.asl.tools import execute_tool
    los = {"range": 4, "facts": {"hexes": [
        {"hex": "K1", "role": "firer", "counters": []},
        {"hex": "Y3", "role": "target", "counters": [{"name": "-2 GE Acq"}]}]}}
    args = {"gun": "75L", "nationality": "German", "ammo": "HE", "range_hexes": 4,
            "target_type": "infantry"}
    r = execute_tool("ordnance_attack", dict(args), context={"native_los": los})
    labels = [d["label"] for d in r["to_hit"]["drm"]]
    assert any("Acquired" in l for l in labels), labels
    assert any("-2 GE Acq" in n for n in r["notes"])
    # The model's own value wins, and a different shot (range) is left alone.
    r = execute_tool("ordnance_attack", {**args, "acquired": 0}, context={"native_los": los})
    assert not any("Acquired" in d["label"] for d in r["to_hit"]["drm"])
    r = execute_tool("ordnance_attack", {**args, "range_hexes": 7}, context={"native_los": los})
    assert not any("Acquired" in d["label"] for d in r["to_hit"]["drm"])


def test_vasl_pzkw_spelling_matches_listing():
    from app.asl import vehicles
    assert vehicles._norm("PzKw IVJ") == vehicles._norm("PzKpfw IVJ")
