#!/usr/bin/env python
"""
Tests for the VASL .vsav save parser (app/services/vsav_service.py).

Uses a real save (tests/fixtures/Hazmo-52-After-Finn-4.vsav) whose 71 moved
pieces carry OldLocationName breadcrumbs — VASL's own ground truth for the
pixel->hex math. Runnable directly (`python tests/test_vsav_service.py`) or
under pytest. No network, no DB.
"""
import base64
import io
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import vsav_service
from app.services.vsav_service import (
    VsavError,
    VsavValidationError,
    parse_vsav,
    render_board_state,
    save_vsav_data_url,
    validate_vsav_bytes,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "Hazmo-52-After-Finn-4.vsav"

_STATE = None


def _state():
    """Parse the fixture once and share across tests."""
    global _STATE
    if _STATE is None:
        _STATE = parse_vsav(FIXTURE)
    return _STATE


# --------------------------------------------------------------------------- #
# Parsing + built-in breadcrumb self-validation
# --------------------------------------------------------------------------- #

def test_parse_succeeds_and_breadcrumbs_validate_71_of_71():
    s = _state()
    val = s["validation"]
    assert val["n_breadcrumbs_checked"] == 71, val
    assert val["n_matched"] == 71, f"breadcrumb mismatches: {val['mismatches']}"
    assert val["mismatches"] == []


def test_boards_and_ssr():
    s = _state()
    names = sorted(b["name"] for b in s["boards"])
    assert names == ["r57", "r69"], names
    assert all(b["reversed"] for b in s["boards"])
    assert "Winter" in s["ssr_transforms"]
    assert "NoGrain" in s["ssr_transforms"]


def test_known_hex_contents():
    s = _state()
    # 57-K3: Russian 37L AT gun + its 2-2-8 crew
    k3 = [u["name"] for u in s["hexes"]["57-K3"]["units"]]
    assert any("37L AT" in n for n in k3), k3
    assert any("2-2-8" in n for n in k3), k3
    assert all(u["side"] == "Russian" for u in s["hexes"]["57-K3"]["units"])
    # 57-G9: a Melee involving both sides. The Melee counter is the TOP of
    # the stack, so it applies (stack-order semantics) to every unit below
    # it — it is attributed per-unit, not left on the hex-level list.
    g9 = s["hexes"]["57-G9"]
    assert all("Melee" in (u.get("markers") or []) for u in g9["units"]), g9
    assert "Melee" not in g9["markers"], g9["markers"]
    sides = {u["side"] for u in g9["units"]}
    assert sides == {"Russian", "Finnish"}, sides


def test_unit_counter_art_exposed():
    """Each unit carries its counter-art path: squads from the active level
    of their unit-identity Layer, SW/single-art pieces from the basic piece
    trait. The art identifies the exact counter type (capability lookups)."""
    s = _state()
    h9 = {u["name"]: u for u in s["hexes"]["57-H9"]["units"]}
    assert h9["6-4-8 1sq"].get("art") == "fi/fi648S.svg", h9["6-4-8 1sq"]
    assert h9["LMG (r)"].get("art") == "fi/firLMG.svg", h9["LMG (r)"]
    h8 = {u["name"]: u for u in s["hexes"]["57-H8"]["units"]}
    assert h8["4-4-7 1sq"].get("art") == "ru/ru447S.svg", h8["4-4-7 1sq"]


def test_active_identity_layer_overrides_printed_counter():
    """Unit identity follows the ACTIVE unit-identity Layer, not the printed
    counter. Ground truth (verified on the VASL screen): in 57-G9 the piece
    whose basic name is '6-4-8 1sq' has its SQ\\/HS Layer active at level 1 —
    on board it is a Finnish 2-4-8 half-squad. Name, counter, and art (which
    keys the capability lookup) must all follow the active layer. The
    SQ\\/HS level names ('2-4-8 1hs,2-3-8 Ghs') never contain the base
    squad name, which is what the old name-in-level-names test missed.
    """
    s = _state()
    g9 = {u.get("counter") or u["name"]: u for u in s["hexes"]["57-G9"]["units"]}
    fin = g9["6-4-8 1sq"]
    assert fin["name"] == "2-4-8 1hs", fin
    assert fin["counter"] == "6-4-8 1sq", fin
    assert fin["art"] == "fi/fi248H.svg", fin
    assert fin["side"] == "Finnish", fin
    # The Russian piece in the same Melee is ELR-flipped AND HS-reduced:
    # printed 4-5-8 Esq, SQ/HS layer active at level 1 = 2-3-7 1hs (art
    # ru/ru237H.svg, which the save composites on top of ru447S).
    rus = g9["4-5-8 Esq"]
    assert rus["name"] == "2-3-7 1hs", rus
    assert rus["art"] == "ru/ru237H.svg", rus

    # The ELR-flip mechanism must keep working where SQ/HS is NOT active:
    # the 57-G3 / 57-H8 Russians stay 4-4-7 1sq over counter 4-5-8 Esq.
    g3 = {u["name"]: u for u in s["hexes"]["57-G3"]["units"]}
    assert g3["4-4-7 1sq"]["counter"] == "4-5-8 Esq", g3["4-4-7 1sq"]
    assert g3["4-4-7 1sq"]["art"] == "ru/ru447S.svg", g3["4-4-7 1sq"]


def test_trait_state_pairing_is_structural():
    """Trait states with literal TABs (multi-line HTML labels) and traits
    embedding whole piece definitions (placeDM) must not shift the pairing.
    Before the structural decode, one User-Labeled marker read an HTML
    fragment as its hide state and was reported HIP by '</tbody>'."""
    s = _state()
    labels = [p for p in s["render_pieces"] if p["name"] == "User-Labeled"]
    assert labels, "fixture lost its User-Labeled markers"
    for p in labels:
        flags = p.get("flags") or {}
        assert "hip_by" not in flags, p
        assert not (p.get("label") or "").startswith("</"), p


def test_stack_order_is_bottom_to_top():
    """Direction proof for the +/id/stack member list: it runs BOTTOM -> TOP.

    Ground truths in the fixture: DM counters sit ON TOP of the broken units
    they affect, and concealment "?" counters sit ON TOP of the units they
    conceal. In the serialized stacks (57-E8: 6-2-8, LMG, Skis, DM and
    69-J1: 6-4-8, LMG, Skis, ?) the DM / "?" are LAST — so last = top.
    With the rule "a marker applies to the units BELOW it", both markers
    must therefore attribute to all units in their stacks; if the list ran
    top -> bottom they would attribute to nothing.
    """
    s = _state()
    e8 = s["hexes"]["57-E8"]
    # the 6-2-8 Esq counter is HS-reduced in the save (active SQ/HS layer ->
    # 3-2-8 Ehs); whatever its identity, it must stay BROKEN under DM
    broken = next(u for u in e8["units"]
                  if u.get("counter", u["name"]).startswith("6-2-8"))
    assert broken["name"] == "3-2-8 Ehs", broken
    assert broken.get("broken") is True, broken
    assert "DM" in broken["markers"], broken
    lmg = next(u for u in e8["units"] if u["name"] == "LMG")
    assert "DM" in lmg["markers"], lmg
    assert "DM" not in e8["markers"], e8["markers"]  # attributed, not hex-level

    j1 = s["hexes"]["69-J1"]
    sq = next(u for u in j1["units"] if u["name"].startswith("6-4-8"))
    assert "concealed_by" in sq, sq           # the save's own conceal trait
    assert "?" in sq["markers"], sq           # and the "?" counter is above it
    assert "?" not in j1["markers"], j1["markers"]


def test_per_unit_marker_attribution_by_stack_position():
    """A marker applies only to units BELOW it. 69-K7's stack is
    (bottom -> top): FI Sniper, 2-2-8 Icr, Skis, ?, fiLDR, DC, Skis —
    the fiLDR and DC sit ABOVE the "?" and must not pick it up. 57-H10's
    8-3-8 Esq is the TOP counter, above both Skis markers."""
    s = _state()
    k7 = {u["name"]: u for u in s["hexes"]["69-K7"]["units"]}
    assert "?" in k7["FI Sniper"]["markers"], k7["FI Sniper"]
    assert "?" in k7["2-2-8 Icr"]["markers"], k7["2-2-8 Icr"]
    assert "?" not in k7["fiLDR"].get("markers", []), k7["fiLDR"]
    assert "?" not in k7["DC"].get("markers", []), k7["DC"]

    h10 = {u["name"]: u for u in s["hexes"]["57-H10"]["units"]}
    assert "Skis" in h10["6-4-8 1sq"]["markers"], h10["6-4-8 1sq"]
    assert h10["8-3-8 Esq"].get("markers") is None, h10["8-3-8 Esq"]


def test_ski_worn_vs_carried_decoded_from_marker_face():
    """The VASL Skis counter's flip Layer mirrors E4.2/E4.21: base art
    sh/skis.png is the "Skis" face (worn = ski mode); level 2 overlays
    "sh/skis off.png" (the "OFF Skis" face = carried at 1 PP). Each unit
    below a Skis marker gets `skis: "worn"|"carried"` from that face.

    Ground truth (Kevin, verified on the VASL screen): the 57-G9 Melee
    units' skis are OFF — carried, not worn. Spot checks across the
    fixture: 57-H9 / 57-H10 / 69-H5 carried; 69-J6 / 69-E1 / 57-E8 /
    69-I7 / 69-H7 worn."""
    s = _state()
    def ski(hx, name):
        u = next(u for u in s["hexes"][hx]["units"] if u["name"] == name)
        assert "Skis" in u["markers"], u
        return u["skis"]
    # The headline ground truth: G9 = carried (both sides).
    assert ski("57-G9", "2-4-8 1hs") == "carried"   # Finnish HS
    assert ski("57-G9", "2-3-7 1hs") == "carried"   # Russian HS
    assert ski("57-G9", "ruCOM") == "carried"
    # More carried units.
    assert ski("57-H9", "6-4-8 1sq") == "carried"
    assert ski("57-H10", "6-4-8 1sq") == "carried"
    assert ski("69-H5", "2-4-8 1hs") == "carried"
    # Worn (ski mode) units — the marker's face stayed on its base side.
    assert ski("69-J6", "6-4-8 1sq") == "worn"
    assert ski("69-E1", "6-2-8 Esq") == "worn"
    assert ski("57-E8", "3-2-8 Ehs") == "worn"
    assert ski("69-I7", "6-4-8 1sq") == "worn"
    assert ski("69-H7", "6-4-8 1sq") == "worn"
    # 69-K7 interleaves both: sniper/crew sit under the WORN marker, the
    # fiLDR/DC under the CARRIED one higher up (nearest-above wins).
    assert ski("69-K7", "2-2-8 Icr") == "worn"
    assert ski("69-K7", "fiLDR") == "carried"


def test_ski_state_from_units_own_activate_skis_layer():
    """A unit's OWN 'Activate Skis' Layer (skison.svg = worn / skisoff.svg
    = carried) also decodes — no fixture unit has it active (every squad's
    layer state is -1 = off), so drive piece_dynamic_state directly."""
    layer_t = ("emb2;Activate Skis;128;A;Toggle ON\\/1pp;128;;;128;;;;1;"
               "false;1;4;dyo\\/MS\\/skison.svg,dyo\\/MS\\/skisoff.svg;,;"
               "true;Skis;;;false;;1;1;false;83,195;90,650;;;1.0;;true")
    def flags_for(state):
        p = {"name": "6-4-8 1sq",
             "pairs": [(layer_t, state), ("piece;;;;6-4-8 1sq", "Main Map;0;0")]}
        flags, _, _, _ = vsav_service.piece_dynamic_state(p)
        return flags
    assert flags_for("1").get("skis") == "worn"      # level 1 = skison.svg
    assert flags_for("2").get("skis") == "carried"   # level 2 = skisoff.svg
    assert "skis" not in flags_for("-1")             # layer off: no skis
    assert "skis" not in flags_for("0")


def test_entrenched_by_from_stack_order():
    """Foxholes become per-unit `entrenched_by`. In both 57-H8 and 57-H9
    the Foxhole counter is ABOVE every unit (top / next-to-top of the
    stack), so every unit is IN it — this is the data behind the H9 -> H8
    +2 DRM regression case. A Foxhole alone in its stack (57-I2) applies
    to nothing and stays a hex-level marker."""
    s = _state()
    for hx in ("57-H8", "57-H9"):
        units = s["hexes"][hx]["units"]
        assert units, hx
        for u in units:
            assert u.get("entrenched_by") == "Foxhole", (hx, u)
            assert "Foxhole" not in (u.get("markers") or []), (hx, u)
        assert "Foxhole" not in s["hexes"][hx]["markers"], (hx, s["hexes"][hx])
    assert "Foxhole" in s["hexes"]["57-I2"]["markers"], s["hexes"]["57-I2"]


def test_render_foxhole_in_notation():
    """Rendering: a unit beneath a Foxhole shows '{Foxhole: in}'; a unit in
    the same hex above/outside it carries no such annotation."""
    text = render_board_state(_state())
    h8 = _hex_line(text, "57-H8")
    assert "4-4-7 1sq (counter: 4-5-8 Esq) {Foxhole: in}" in h8, h8
    # synthetic mixed hex: one unit in, one out
    state = {"hexes": {"57-B3": {"units": [
        {"name": "4-4-7 1sq", "side": "Russian", "entrenched_by": "Foxhole"},
        {"name": "2-2-8 Icr", "side": "Russian"},
    ], "markers": []}}, "boards": []}
    line = _hex_line(render_board_state(state), "57-B3")
    assert "4-4-7 1sq {Foxhole: in}" in line, line
    assert "2-2-8 Icr {" not in line, line
    # legend explains the convention
    assert "ABOVE" in text and "Foxhole: in" in text


def test_render_ski_face_in_braces():
    """Rendering: the Skis marker shows its decoded face — '{Skis: worn}'
    (ski mode, E4.2) vs '{Skis: carried}' (1 PP, E4.21) — and the legend
    explains both. G9 (the CC regression hex) must read 'carried'."""
    text = render_board_state(_state())
    g9 = _hex_line(text, "57-G9")
    assert "Skis: carried" in g9, g9
    assert "Skis: worn" not in g9, g9
    j6 = _hex_line(text, "69-J6")
    assert "Skis: worn" in j6, j6
    assert "'Skis: worn'" in text and "'Skis: carried'" in text, \
        "legend must explain the ski faces"
    assert "E4.21" in text, "legend cites the carried-skis rule"


def test_player_sides_mapping():
    s = _state()
    # Fixture is sanitized: real player names/ids were pseudonymized in-place.
    assert s["player_sides"]["finn_player"] == "Finnish", s["player_sides"]
    assert s["player_sides"]["Russian Player"] == "Russian", s["player_sides"]


# --------------------------------------------------------------------------- #
# render_board_state + perspective filtering
# --------------------------------------------------------------------------- #

def test_render_full_view_contains_key_facts():
    text = render_board_state(_state())
    assert "BOARD STATE" in text
    assert "57-K3" in text and "37L AT" in text
    assert "71/71 position breadcrumbs matched" in text
    assert "Winter" in text


def _hex_line(text, hx):
    """Find the rendered line for a hex; tolerates an optional [terrain]
    annotation between the hex ID and the colon."""
    return next(l for l in text.splitlines()
                if l.strip().startswith(hx + ":") or l.strip().startswith(hx + " ["))


def test_perspective_masks_enemy_concealed_units():
    s = _state()
    # 57-G2 holds Russian squads concealed under "?" counters.
    finn_view = render_board_state(s, perspective_side="Finnish")
    g2_line = _hex_line(finn_view, "57-G2")
    assert "? (concealed" in g2_line, g2_line
    assert "4-4-7" not in g2_line, f"enemy concealed identity leaked: {g2_line}"
    # The Russian player sees their own concealed units in full.
    rus_view = render_board_state(s, perspective_side="Russian")
    g2_line_r = _hex_line(rus_view, "57-G2")
    assert "4-4-7" in g2_line_r, g2_line_r
    # And the Finnish player's concealed squad (69-J1) is masked from the Russian.
    j1_line_r = _hex_line(rus_view, "69-J1")
    assert "? (concealed" in j1_line_r, j1_line_r
    assert "6-4-8" not in j1_line_r, f"enemy concealed identity leaked: {j1_line_r}"
    # ... but fully visible to the Finn.
    j1_line_f = _hex_line(finn_view, "69-J1")
    assert "6-4-8" in j1_line_f, j1_line_f


def test_no_perspective_shows_everything():
    text = render_board_state(_state(), perspective_side=None)
    assert "? (concealed — identity unknown)" not in text
    assert "[concealed]" in text  # flags still annotated


# --------------------------------------------------------------------------- #
# Validation + graceful failure
# --------------------------------------------------------------------------- #

def test_corrupt_file_fails_gracefully():
    with tempfile.NamedTemporaryFile(suffix=".vsav") as f:
        f.write(b"this is definitely not a zip archive")
        f.flush()
        try:
            parse_vsav(f.name)
        except VsavError as e:
            assert "zip" in str(e).lower(), e
            return
    raise AssertionError("corrupt input should raise VsavError")


def test_zip_without_savedgame_fails_gracefully():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("notASave.txt", "hello")
    try:
        validate_vsav_bytes(buf.getvalue())
    except VsavValidationError as e:
        assert "savedGame" in str(e), e
        return
    raise AssertionError("zip without savedGame should raise VsavValidationError")


def test_missing_file_fails_gracefully():
    try:
        parse_vsav("does/not/exist.vsav")
    except VsavError:
        return
    raise AssertionError("missing file should raise VsavError")


def test_oversized_upload_rejected():
    big = b"\x00" * (vsav_service.MAX_VSAV_BYTES + 1)
    try:
        validate_vsav_bytes(big)
    except VsavValidationError as e:
        assert "limit" in str(e), e
        return
    raise AssertionError("oversized input should raise VsavValidationError")


def test_save_vsav_data_url_roundtrip():
    """Upload path: data URL -> stored file -> parse_vsav works on it."""
    raw = FIXTURE.read_bytes()
    data_url = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()
    orig_dir = vsav_service.UPLOADS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        vsav_service.UPLOADS_DIR = Path(tmp)
        try:
            rel = save_vsav_data_url(data_url, "demo")
            assert rel.startswith("demo/") and rel.endswith(".vsav"), rel
            s = parse_vsav(rel)
            assert s["validation"]["n_matched"] == 71
        finally:
            vsav_service.UPLOADS_DIR = orig_dir


def test_save_vsav_data_url_rejects_garbage():
    bad = "data:application/octet-stream;base64," + base64.b64encode(b"nope").decode()
    orig_dir = vsav_service.UPLOADS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        vsav_service.UPLOADS_DIR = Path(tmp)
        try:
            try:
                save_vsav_data_url(bad, "demo")
            except VsavValidationError:
                return
            raise AssertionError("garbage upload should raise VsavValidationError")
        finally:
            vsav_service.UPLOADS_DIR = orig_dir


# --------------------------------------------------------------------------- #
# Per-hex terrain (app/services/board_terrain.py)
#
# Needs real VASL board archives (bd57/bd69), which are copyrighted and NOT
# in the repo — these tests no-op (pass with a notice) when no archive is
# found in board_terrain.BOARD_SEARCH_DIRS (e.g. in CI).
# --------------------------------------------------------------------------- #

from app.services import board_terrain


def _boards_available():
    return (board_terrain.find_board_archive("57") is not None
            and board_terrain.find_board_archive("69") is not None)


def test_terrain_known_hexes():
    """Raw terrain (no SSR) for hexes verified against BoardMetadata.xml and
    the board images: 57-Q2/57-X3 stone buildings, 57-Y5 wooden building,
    57-H8 orchard+road, 69-G1 multi-hex wooden building (a BoardMetadata
    buildingType override), 69-I5 grain, 69-X4 depression (elev -1)."""
    if not _boards_available():
        print("    (skipped: no local board archives)")
        return
    def t(board, hx):
        info = board_terrain.get_hex_terrain(board, hx)
        assert info is not None, (board, hx)
        return info
    assert "Stone Building" in t("57", "Q2")["terrain"]
    assert "Stone Building" in t("57", "X3")["terrain"]
    assert "Wooden Building" in t("57", "Y5")["terrain"]
    h8 = t("57", "H8")
    assert "Orchard" in h8["terrain"] and h8["road"], h8
    g1 = t("69", "G1")
    assert "Wooden Building (multi-hex)" in g1["terrain"], g1
    assert "Grain" in t("69", "I5")["parts"]
    x4 = t("69", "X4")
    assert x4["elevation"] == -1 and "depression" in x4["terrain"], x4


def test_terrain_ssr_transforms():
    if not _boards_available():
        print("    (skipped: no local board archives)")
        return
    # GrainToBrush: a grain hex must report Brush.
    i5 = board_terrain.get_hex_terrain("69", "I5", ["GrainToBrush"])
    assert "Brush" in i5["parts"] and "Grain" not in i5["parts"], i5
    assert i5["ssr_changed"].get("Grain") == "Brush", i5
    # Sequential semantics: NoGrain first consumes the grain, so a later
    # GrainToBrush is a no-op (mirrors VASL's color substitution order).
    i5b = board_terrain.get_hex_terrain("69", "I5", ["NoGrain", "GrainToBrush"])
    assert "Open Ground" in i5b["parts"] and "Brush" not in i5b["parts"], i5b
    # OrchardOutOfSeason on 57-H8.
    h8 = board_terrain.get_hex_terrain("57", "H8", ["OrchardOutOfSeason"])
    assert "Orchard, Out of Season" in h8["parts"], h8


def test_terrain_in_parsed_state_and_render():
    """End-to-end: parse_vsav annotates occupied hexes; render shows them.
    The fixture's boards list NoGrain before GrainToBrush, so grain hexes
    must come out as Open Ground (not Brush)."""
    if not _boards_available():
        print("    (skipped: no local board archives)")
        return
    s = _state()
    k3 = s["hexes"]["57-K3"].get("terrain")
    assert k3 is not None, "57-K3 not annotated"
    assert "Grain" not in k3["parts"], k3            # NoGrain applied
    assert "Open Ground" in k3["parts"], k3
    h8 = s["hexes"]["57-H8"]["terrain"]
    assert "Orchard, Out of Season" in h8["parts"], h8
    assert h8["road"], h8
    text = render_board_state(s)
    assert "57-H8 [" in text and "Orchard" in _hex_line(text, "57-H8")
    assert "read from VASL board data" in text


def test_terrain_missing_board_degrades_gracefully():
    """With no archives findable, parsing still works, hexes carry no
    terrain, and the render notes terrain is unavailable."""
    orig = board_terrain.BOARD_SEARCH_DIRS
    board_terrain.BOARD_SEARCH_DIRS = [Path("/nonexistent-boards-dir")]
    board_terrain._load_board.cache_clear()
    try:
        s = parse_vsav(FIXTURE)
        assert s["validation"]["n_matched"] == 71
        assert all("terrain" not in v for v in s["hexes"].values())
        info = s.get("terrain_info") or {}
        assert sorted(info.get("missing_boards", [])) == ["57", "69"], info
        text = render_board_state(s)
        assert "Terrain unavailable for board(s)" in text, text
        assert board_terrain.get_hex_terrain("57", "Q2") is None
    finally:
        board_terrain.BOARD_SEARCH_DIRS = orig
        board_terrain._load_board.cache_clear()


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except Exception as e:
            failures += 1
            print(f"  ❌ {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
