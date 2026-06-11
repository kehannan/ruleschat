"""Per-hex ASL terrain read from VASL board archives (the ``LOSData`` entry).

A VASL board archive (``bdNN``, a zip) contains ``LOSData``: VASL's own
precomputed per-pixel terrain + elevation grid, written by
``VASL.LOS.Map.Map`` via a Java ``ObjectOutputStream`` and gzipped.
Binary format (mirrors VASL's BoardArchive read/write code):

  GZIP -> Java serialization stream:
    magic 0xACED, version 0x0005, then block-data segments —
    TC_BLOCKDATA (0x77, 1-byte length) / TC_BLOCKDATALONG (0x7A, 4-byte
    length). The concatenated payload is plain DataOutput primitives:
      writeInt(width_hexes) writeInt(height_hexes)
      writeInt(gridWidth_px) writeInt(gridHeight_px)
      for x in range(gridWidth):
        for y in range(gridHeight):
          writeByte(elevation)        # signed; -1 = depression
          writeByte(terrainTypeCode)  # unsigned; see TERRAIN_NAMES
      ... trailing per-hex stairway booleans (ignored here).

Terrain codes -> names come from the VASL module's
``boardData/SharedBoardMetadata.xml`` (``terrainType`` elements); the table
is embedded below (extracted from VASL 6.7.3). Per-hex building-type
overrides come from each board's ``BoardMetadata.xml``.

A hex's terrain is summarized by sampling the grid over a disc around the
hex center (the center pixel alone misreports e.g. a road crossing a woods
hex) and reporting the dominant terrain(s) + road/building/elevation.

Board archives are MMP/VASL copyrighted content and are NEVER shipped in
this repo: ``find_board_archive`` searches ``data/boards/`` (gitignored
local cache) then ``~/vasl/boards/``. Everything degrades gracefully when
an archive is missing.
"""
import gzip
import logging
import re
import struct
import zipfile
from collections import Counter
from functools import lru_cache
from pathlib import Path

# Hex geometry shared with the .vsav parser (same VASL geo-board constants).
from app.services.vsav_service import DX, DY, LETTERS

# Search order for board archives (file named e.g. "bd57").
BOARD_SEARCH_DIRS = [
    Path("data/boards"),              # repo-local cache (gitignored)
    Path.home() / "vasl" / "boards",  # local VASL install
]

# typeCode -> terrain name, from SharedBoardMetadata.xml (VASL 6.7.3).
TERRAIN_NAMES = {
    0: "Open Ground", 1: "Plowed Field", 2: "Rooftop", 3: "Snow",
    4: "Deep Snow", 5: "Ice", 6: "Mud", 7: "Mudflats", 8: "Water",
    9: "Shallow Water", 10: "River", 11: "Shallow River", 12: "Ford",
    13: "Canal", 14: "Marsh", 15: "Shellholes", 20: "Foxholes",
    21: "Trench", 22: "Antitank Trench", 23: "Pillbox", 24: "Grain",
    25: "Brush", 26: "Crags", 27: "Debris", 28: "Graveyard",
    29: "Lumberyard", 30: "Gully", 31: "Dry Stream", 32: "Shallow Stream",
    33: "Deep Stream", 34: "Flooded Stream", 35: "Irrigation Ditch",
    40: "Stone Building", 41: "Stone Building, 1 Level",
    42: "Stone Building, 2 Level", 43: "Stone Building, 3 Level",
    44: "Stone Building, 4 Level", 45: "Stone Factory Wall, 1.5 Level",
    46: "Stone Factory Wall, 2.5 Level", 47: "Stone Factory, 1.5 Level",
    48: "Stone Factory, 2.5 Level", 49: "Stone Market Place",
    50: "Wooden Building", 51: "Wooden Building, 1 Level",
    52: "Wooden Building, 2 Level", 53: "Wooden Building, 3 Level",
    54: "Wooden Building, 4 Level", 55: "Wooden Factory Wall, 1.5 Level",
    56: "Wooden Factory Wall, 2.5 Level", 57: "Wooden Factory, 1.5 Level",
    58: "Wooden Factory, 2.5 Level", 59: "Wooden Market Place",
    60: "Woods", 61: "Forest", 62: "Pine Woods", 63: "Orchard",
    64: "Orchard, Out of Season", 65: "Dirt Road", 66: "Paved Road",
    67: "Elevated Road", 68: "Sunken Road", 69: "Runway", 70: "Path",
    72: "Wall", 73: "Hedge", 74: "Bocage", 75: "Cliff",
    76: "Rowhouse Wall", 77: "Rowhouse Wall, 1 Level",
    78: "Rowhouse Wall, 2 Level", 79: "Rowhouse Wall, 3 Level",
    80: "Rowhouse Wall, 4 Level", 81: "Road Block",
    82: "Single Hex Stone Bridge", 83: "Single Hex Wooden Bridge",
    84: "Stone Bridge", 85: "Wooden Bridge", 86: "Pontoon Bridge",
    87: "Foot Bridge", 90: "Stone Rubble", 91: "Wooden Rubble",
    95: "Sewer", 96: "Tunnel", 97: "Cave", 100: "Scrub", 101: "Hammada",
    102: "Deir", 103: "Deir Lip", 104: "Wadi", 105: "Hillock",
    106: "Hillock Summit", 107: "Sand", 108: "Sand Dune, Low",
    109: "Dune, Crest Low", 110: "Dune, Crest High", 111: "Sangar",
    112: "Track", 113: "Mausoleum", 114: "Bedouin Camp",
    115: "Building Cluster", 116: "Cactus Hedge", 117: "Cactus Patch",
    118: "Olive Grove", 119: "Vineyard", 120: "Broken Ground",
    121: "Scrub", 125: "Temple", 126: "Light Jungle", 127: "Dense Jungle",
    128: "Bamboo", 129: "Palm Trees", 130: "Huts", 131: "Collapsed Huts",
    132: "Kunai", 133: "Swamp", 135: "Rice Paddy, Drained",
    136: "Rice Paddy, Irrigated", 137: "Rice Paddy, In Season",
    138: "Rice Paddy Bank", 139: "Panji", 140: "Ocean",
    141: "Shallow Ocean", 142: "Effluent", 143: "Soft Sand",
    144: "Beach, Slight Slope", 145: "Beach, Moderate Slope",
    146: "Beach, Steep Slope", 147: "Exposed Reef", 148: "Submerged Reef",
    152: "Railway Embankment", 153: "Railroad, Ground Level",
    154: "Railroad, Embankment", 155: "Railroad, Elevated",
    156: "Railroad, Sunken", 157: "Rrembankment", 160: "Tower Hindrance",
    161: "Tower, 2 Level Hindrance", 162: "Tower, 3 Level Hindrance",
    163: "Tower Obstacle", 164: "Tower, 2 Level Obstacle",
    165: "Tower, 3 Level Obstacle", 166: "Storage Tank",
    167: "Storage Tank, 2 Level", 168: "BFP Tower, 1 Level",
    169: "BFP Tower, 2 Level", 170: "Roofless Stone Factory, 1.5 Level",
    171: "Roofless Stone Factory, 2.5 Level",
    172: "Interior Factory Wall, 1 Level",
    173: "Interior Factory Wall, 2 Level", 174: "Cellar",
    175: "Light Woods", 180: "Gutted Stone Factory, 1.5 Level",
    181: "Gutted Stone Factory, 2.5 Level", 182: "Gutted Stone Building",
    183: "Gutted Stone Building, 1 Level",
    184: "Gutted Stone Building, 2 Level",
    185: "Gutted Stone Building, 3 Level",
    186: "Gutted Stone Building, 4 Level",
    190: "Stone Rubble Ground Level", 191: "Stone Rubble Level 1",
    192: "Stone Rubble Level 2", 193: "Stone Rubble Level 3",
    194: "Wooden Rubble Ground Level", 195: "Wooden Rubble Level 1",
    196: "Wooden Rubble Level 2", 197: "Wooden Rubble Level 3",
    198: "Wooden Factory, 1 Level", 199: "Wooden Factory Wall, 1 Level",
    200: "PartialOrchard", 201: "OffBObserver", 202: "Crest",
    203: "MultipleWooden", 204: "Monument", 205: "BTCrags",
    206: "MultipleStone", 207: "Breach", 208: "Light Grain",
    209: "Volga Pier", 210: "Gutted Building Wall, 1 Level",
    211: "Gutted Building Wall, 2 Level",
}

# VASL-internal names -> player-facing names.
_FRIENDLY = {
    "MultipleWooden": "Wooden Building (multi-hex)",
    "MultipleStone": "Stone Building (multi-hex)",
    "PartialOrchard": "Orchard (partial)",
}

# SSR terrain transforms applied SEQUENTIALLY in the order the save lists
# them per board (mirrors VASL's sequential color substitution: once
# NoGrain has turned Grain into Open Ground, a later GrainToBrush finds no
# grain left to change). Transforms with no terrain-name effect (e.g.
# Winter) are intentionally absent; unrecognized ones are simply skipped —
# the full SSR list is already shown at the top of the BOARD STATE block.
SSR_NAME_TRANSFORMS = {
    "GrainToBrush": {"Grain": "Brush", "Light Grain": "Brush"},
    "NoGrain": {"Grain": "Open Ground", "Light Grain": "Open Ground"},
    "MarshToOpenGround": {"Marsh": "Open Ground"},
    "SwampToMarsh": {"Swamp": "Marsh"},
    "OrchardOutOfSeason": {"Orchard": "Orchard, Out of Season"},
    "OrchardsToCrags": {"Orchard": "Crags"},
    "OrchardsToShellholes": {"Orchard": "Shellholes"},
    "PalmTrees": {"Orchard": "Palm Trees"},
    "HedgesToBocage": {"Hedge": "Bocage"},
    "WallsToBocage": {"Wall": "Bocage"},
    "WallToCactus": {"Wall": "Cactus Hedge"},
    "HedgeToCactus": {"Hedge": "Cactus Hedge"},
    "LightWoods": {"Woods": "Light Woods"},
    "DenseJungle": {"Woods": "Dense Jungle"},
    "Bamboo": {"Brush": "Bamboo"},
    "NoWoodsRoads": {"Dirt Road": "Open Ground", "Paved Road": "Open Ground"},
    "NoRoads": {"Dirt Road": "Open Ground", "Paved Road": "Open Ground"},
}

_ROAD_NAMES = {"Dirt Road", "Paved Road", "Elevated Road", "Sunken Road",
               "Path", "Track", "Runway"}

# Disc sampled around each hex center. Radius must stay below the minimum
# half-distance between adjacent hex centers (~32.25 px) so every sampled
# pixel is guaranteed to lie inside the hex.
_SAMPLE_RADIUS = 30
_SAMPLE_STEP = 3

_BUILDING_OVERRIDE_RE = re.compile(
    r'<buildingType\s+hexName="([^"]+)"\s+buildingTypeName="([^"]+)"')


def _is_building(name: str) -> bool:
    return ("Building" in name or "Factory" in name or "Rowhouse" in name
            or name in ("MultipleWooden", "MultipleStone", "Huts", "Temple",
                        "Building Cluster"))


def find_board_archive(board_base: str) -> Path:
    """Locate the bdNN archive for a board base name ('57'); None if absent."""
    fname = f"bd{board_base}"
    for d in BOARD_SEARCH_DIRS:
        p = Path(d) / fname
        if p.is_file():
            return p
    return None


def _deblock_java_stream(raw: bytes) -> bytes:
    """Concatenate the block-data payload of a Java ObjectOutputStream."""
    if raw[:4] != b"\xac\xed\x00\x05":
        raise ValueError("LOSData: not a Java serialization stream")
    out = bytearray()
    i = 4
    n = len(raw)
    while i < n:
        tag = raw[i]
        if tag == 0x77:                                # TC_BLOCKDATA
            ln = raw[i + 1]
            out += raw[i + 2:i + 2 + ln]
            i += 2 + ln
        elif tag == 0x7A:                              # TC_BLOCKDATALONG
            ln = struct.unpack(">I", raw[i + 1:i + 5])[0]
            out += raw[i + 5:i + 5 + ln]
            i += 5 + ln
        else:
            break  # objects after the primitive payload — not needed
    return bytes(out)


@lru_cache(maxsize=8)
def _load_board(board_base: str):
    """Load + cache one board's LOSData grid and metadata.

    Returns dict(grid_w, grid_h, grid(bytes: elev,terrain per px,
    column-major), building_overrides {HEX: name}) or None if the archive
    is missing/unreadable.
    """
    archive = find_board_archive(board_base)
    if archive is None:
        return None
    try:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            if "LOSData" not in names:
                logging.warning("board_terrain: %s has no LOSData", archive)
                return None
            payload = _deblock_java_stream(
                gzip.decompress(z.read("LOSData")))
            overrides = {}
            for n in names:
                if n.lower() == "boardmetadata.xml":
                    xml = z.read(n).decode("utf-8", "replace")
                    overrides = {h.upper(): t for h, t in
                                 _BUILDING_OVERRIDE_RE.findall(xml)}
                    break
        w, h, gw, gh = struct.unpack(">iiii", payload[:16])
        grid = payload[16:16 + gw * gh * 2]
        if len(grid) < gw * gh * 2:
            raise ValueError("LOSData grid truncated")
        return dict(grid_w=gw, grid_h=gh, grid=grid,
                    building_overrides=overrides)
    except Exception as e:
        logging.warning("board_terrain: failed to read %s: %s", archive, e)
        return None


def _hex_center(hex_label: str):
    """Hex label ('K3') -> unreversed-board-image pixel center (x, y)."""
    m = re.fullmatch(r"([A-Z]+)(\d+)", hex_label.upper())
    if not m or m.group(1) not in LETTERS:
        raise ValueError(f"Bad hex label: {hex_label!r}")
    i = LETTERS.index(m.group(1))
    r = int(m.group(2))
    x = i * DX
    y = r * DY if i % 2 == 1 else DY / 2 + (r - 1) * DY
    return x, y


def _sample_hex(bd, hex_label):
    """Counter of terrain names + Counter of elevations over the hex disc."""
    cx, cy = _hex_center(hex_label)
    gw, gh, grid = bd["grid_w"], bd["grid_h"], bd["grid"]
    terr, elev = Counter(), Counter()
    rr = _SAMPLE_RADIUS * _SAMPLE_RADIUS
    for ox in range(-_SAMPLE_RADIUS, _SAMPLE_RADIUS + 1, _SAMPLE_STEP):
        for oy in range(-_SAMPLE_RADIUS, _SAMPLE_RADIUS + 1, _SAMPLE_STEP):
            if ox * ox + oy * oy > rr:
                continue
            xi, yi = int(round(cx)) + ox, int(round(cy)) + oy
            if not (0 <= xi < gw and 0 <= yi < gh):
                continue
            idx = (xi * gh + yi) * 2
            e = grid[idx]
            elev[e - 256 if e > 127 else e] += 1
            code = grid[idx + 1]
            terr[TERRAIN_NAMES.get(code, f"terrain code {code}")] += 1
    return terr, elev


def apply_ssr_transforms(name: str, transforms) -> str:
    """Apply per-board SSR transforms sequentially to a terrain name."""
    for t in transforms or ():
        name = SSR_NAME_TRANSFORMS.get(t, {}).get(name, name)
    return name


def get_hex_terrain(board_base: str, hex_label: str, ssr_transforms=()):
    """Terrain summary for one hex, or None if no board data is available.

    Returns dict(terrain=display string, parts=[post-SSR names by share],
    road=bool, elevation=int, ssr_changed={orig: new}).
    """
    bd = _load_board(str(board_base))
    if bd is None:
        return None
    try:
        terr, elev = _sample_hex(bd, hex_label)
    except ValueError:
        return None
    if not terr:
        return None
    total = sum(terr.values())

    road = any(n in _ROAD_NAMES and c >= 0.03 * total
               for n, c in terr.items())

    # building-type override from BoardMetadata.xml (grid mis-types
    # multi-hex / multi-level buildings; the XML is authoritative)
    override = bd["building_overrides"].get(hex_label.upper())

    parts = []
    for n, c in terr.most_common():
        if n in _ROAD_NAMES:
            continue
        if _is_building(n) and c >= 0.05 * total:
            parts.append(override or n)
        elif c >= 0.20 * total:
            parts.append(n)
    if not parts:  # hex is (almost) all road
        parts = [n for n, _ in terr.most_common(1)]
    # buildings first, then by sampled share; cap at 3
    parts = sorted(dict.fromkeys(parts),
                   key=lambda n: (not _is_building(n),
                                  -terr.get(n, total)))[:3]

    ssr_changed = {}
    out_parts = []
    for n in parts:
        nn = apply_ssr_transforms(n, ssr_transforms)
        if nn != n:
            ssr_changed[n] = nn
        if nn not in out_parts:
            out_parts.append(nn)

    elevation = elev.most_common(1)[0][0] if elev else 0

    label = " + ".join(_FRIENDLY.get(p, p) for p in out_parts)
    if road and "Road" not in label:
        rd = apply_ssr_transforms("Dirt Road", ssr_transforms)
        if rd != "Open Ground":   # NoRoads-style SSR removes the road
            label += ", road"
    if elevation > 0:
        label += f", Level {elevation} hill"
    elif elevation < 0:
        label += f", depression (elev {elevation})"
    if ssr_changed:
        label += " (SSR: was " + ", ".join(ssr_changed) + ")"

    return dict(terrain=label, parts=out_parts, road=road,
                elevation=elevation, ssr_changed=ssr_changed)


def annotate_state_with_terrain(state: dict) -> dict:
    """Add per-hex terrain to a parse_vsav() state, in place.

    For every occupied hex, sets state['hexes'][key]['terrain'] (the
    display dict from get_hex_terrain, with that board's SSR transforms
    applied). Adds state['terrain_info'] = dict(source, missing_boards,
    overlay_note). Never raises: boards without archives are listed in
    missing_boards and their hexes are left un-annotated.
    """
    transforms_by_base = {}
    for b in state.get("boards", []):
        base = b.get("base") or b["name"].lstrip("r")
        transforms_by_base[base] = b.get("ssr_transforms", [])

    missing = sorted(base for base in transforms_by_base
                     if _load_board(str(base)) is None)

    # Overlay anchor hexes (spec: "<name> <hex1> <hex2>"): terrain there —
    # and possibly in nearby hexes — is changed by the overlay image,
    # which we do not parse.
    overlay_anchors = set()
    for o in state.get("overlays", []):
        board = o.get("board", "").lstrip("r")
        for tok in o.get("spec", "").split()[1:]:
            if re.fullmatch(r"[A-Za-z]+\d+", tok):
                overlay_anchors.add(f"{board}-{tok.upper()}")

    for key, v in state.get("hexes", {}).items():
        base, _, label = key.partition("-")
        if base in missing or not label:
            continue
        try:
            info = get_hex_terrain(base, label, transforms_by_base.get(base))
        except Exception as e:
            logging.warning("board_terrain: %s failed: %s", key, e)
            continue
        if info is None:
            continue
        if key.upper() in overlay_anchors:
            info["overlay"] = True
            info["terrain"] += " (may be modified by overlay)"
        v["terrain"] = info

    state["terrain_info"] = dict(
        source="vasl-losdata",
        missing_boards=missing,
        has_overlays=bool(state.get("overlays")),
    )
    return state
