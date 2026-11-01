#!/usr/bin/env python3
"""
Extract the Chapter H vehicle listings (AF, TA, Size, MA, ...) from the eASLRB
PDF into data/rulebook/vehicles.json, the vehicle table behind the TH/TK tools.

The listing pages are rotated tables: each column runs along the page's y axis
and each vehicle is a strip at one x. Words are placed by position: rows by x,
columns by the y of the column headers (read off the header strip that holds
"Name"). Quirks handled (verified against eASLRB v3.14, 2026-11-01):
  * Blank cells are simply absent, so plain text extraction can't be used.
  * "Name & Type" listings put the vehicle type (and long names) on extra
    strips a few points away; those fragments are merged into the nearest
    full row (one that has both # and Name).
  * The unarmored star prints as "#" or "★"; it marks unarmored (alone) or
    partially armored ("2/1/★T" = rear turret unarmored) AF.
  * Superscript footnote marks (†, †1) are kept in `notes_raw` but stripped
    from the values used for lookups.

NOT covered: the U.S. listing (H30-H32) is in a font with no character map
and needs OCR; landing craft (H112) use a different layout.

Output is GITIGNORED (copyrighted rulebook data — never commit it).

Usage:
    python scripts/extract_vehicle_listings.py              # build + report
    python scripts/extract_vehicle_listings.py --show Panther
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

REPO = Path(__file__).resolve().parents[1]
PDF = REPO / "data" / "rulebook" / "eASLRB_v3_14_INHERIT_ZOOM.pdf"
OUT = REPO / "data" / "rulebook" / "vehicles.json"

# 0-based PDF page -> nationality of that vehicle listing.
PAGES = {
    336: "German", 338: "German",
    354: "Russian", 355: "Russian",
    401: "British", 402: "British",
    435: "Italian",
    450: "Japanese",
    463: "Chinese",
    477: "French",
    499: "Allied Minor", 500: "Allied Minor",
    515: "Axis Minor", 516: "Axis Minor",
    563: "UN Forces", 564: "UN Forces",
    588: "Communist Forces",
}

HEADER_ALIAS = {"Name &": "Name", "&": "Type", "#*": "#", "sD#": "sD", "S": "SA"}
SKIP_HEADERS = {"®", "H", "Vehicle", "•", "Type", "A"}
# A vehicle type code: MT, LTv, aMT, APCv, AATt, TDht, ht, tr, AG/TD, ...
TYPE_CODE = re.compile(r"^(?:[a-z]{0,2}[A-Z]{1,5}[a-z]{0,3}(?:/[A-Z]{1,3})?|ht|tr|atr|htv|trv|htMC)$")
AF_SCALE = [0, 1, 2, 3, 4, 6, 8, 11, 14, 18, 26]
FOOTNOTE = re.compile(r"†\d*")


def _headers(words, name_x):
    strip = sorted((w for w in words if abs(w[0] - name_x) < 3), key=lambda w: -w[3])
    cols = []
    for w in strip:
        t = HEADER_ALIAS.get(w[4], w[4])
        if t in SKIP_HEADERS and not (t == "Type" and not cols):
            # 'Type' is its own column only in the "# Name WGT Type" layout.
            if not (t == "Type" and cols and cols[-1][0] == "WGT"):
                continue
        cols.append((t, w[3], w[1]))
    return cols  # (name, start_y, end_y), reading order


def _rows(page):
    words = page.get_text("words")
    name_x = min(w[0] for w in words if w[4] == "Name")
    cols = _headers(words, name_x)
    centers = [(t, (s + e) / 2) for t, s, e in cols]
    name_start = next(s for t, s, e in cols if t == "Name")
    later = [s for t, s, e in cols if s < name_start - 2]
    name_end = max(later) if later else 0

    strips = collections.defaultdict(list)
    for w in words:
        if w[0] < name_x + 5:
            continue
        strips[round(w[0] / 2.6)].append(w)
    rows = []
    for k in sorted(strips):
        cells = collections.defaultdict(list)
        for w in sorted(strips[k], key=lambda w: -w[3]):
            yc = (w[1] + w[3]) / 2
            if name_end + 3 < w[3] <= name_start + 4 and yc > name_end:
                col = "Name"   # left-aligned name text runs past its center
            else:
                col = min(centers, key=lambda tc: abs(tc[1] - yc))[0]
            cells[col].append(w[4])
        rows.append((strips[k][0][0], {c: " ".join(v) for c, v in cells.items()}))
    return rows


def _merge(rows):
    """Fold fragment strips into the nearest full row (has # and Name)."""
    full = [(x, dict(r)) for x, r in rows if r.get("#") and r.get("Name")
            and re.search(r"\d", r["#"])]
    if not full:
        return []
    for x, r in rows:
        if r.get("#") and r.get("Name") and re.search(r"\d", r["#"]):
            continue
        nx, target = min(full, key=lambda fr: abs(fr[0] - x))
        if abs(nx - x) > 7:
            continue
        for c, v in r.items():
            if c == "#":
                continue
            target.setdefault("_frag_" + c, []).append(v)
    return [r for _, r in full]


def _clean(v):
    return FOOTNOTE.sub("", v or "").strip()


def _af(raw):
    """'8/3' -> (8, 3, flags). '1' -> (1, 1). '#'/'★' alone -> unarmored."""
    v = _clean(raw).replace("★", "#")
    flags = []
    if v in ("#", ""):
        # Unarmored vehicles print the star as a graphic on most pages, so a
        # blank AF cell means unarmored too (D1.21).
        return None, None, ["unarmored"] if v == "#" else ["unarmored", "af_blank"]
    parts = v.split("/")
    if parts[-1].startswith("#"):
        flags.append("partial_rear_turret_unarmored" if "T" in parts[-1] else "partially_armored")
        parts = parts[:-1]
    nums = []
    for p in parts:
        m = re.match(r"\d+", p)
        nums.append(int(m.group()) if m else None)
    front = nums[0] if nums else None
    side = nums[1] if len(nums) > 1 else front
    return front, side, flags


def _step(af, delta):
    if af is None or af not in AF_SCALE:
        return af
    i = min(max(AF_SCALE.index(af) + delta, 0), len(AF_SCALE) - 1)
    return AF_SCALE[i]


def _turret(front, side, ta):
    """D1.63/.64: TA '+F', '-SR', '-F/+SR', '+FSR' -> one AF step per facing."""
    tf, ts = front, side
    for part in _clean(ta).split("/"):
        m = re.match(r"([+-])(FSR|F|SR)$", part)
        if not m:
            continue
        d = 1 if m.group(1) == "+" else -1
        if m.group(2) in ("F", "FSR"):
            tf = _step(front, d)
        if m.group(2) in ("SR", "FSR"):
            ts = _step(side, d)
    return tf, ts


def _record(r, nat, page):
    def toks(vals):
        return [t for v in vals for t in (_clean(x).rstrip("†") for x in v.split())
                if t and t not in ("•", "®")]

    name = r["Name"]
    main = toks([r.get("Type", "")])
    frag = toks(r.get("_frag_Type", []) + r.get("_frag_Name", []))
    # "Name & Type" layout: the main strip's Type cell continues the name
    # ("Sherman" / "IIC(a)") and the type code sits on a fragment strip
    # ("MT"). In the "# Name WGT Type" layout the Type cell is just the code.
    vtype = ""
    if frag and TYPE_CODE.match(frag[-1]):
        vtype = frag.pop()
    elif main and not frag and TYPE_CODE.match(main[-1]) and r.get("_layout") == "type_col":
        vtype = main.pop()
    extra = main + frag
    if extra:
        name = f"{name} {' '.join(extra)}"
    front, side, flags = _af(r.get("AF", ""))
    tf, ts = _turret(front, side, r.get("TA", ""))
    ma = _clean(r.get("MA", ""))
    m = re.match(r"^([TBRSN]?)(F?)(\d+)(LL|L|\*)?$", ma)
    rec = {
        "name": re.sub(r"\s+", " ", name.replace("®", "")).strip(),
        "nationality": nat,
        "type": vtype,
        "dates": _clean(r.get("Dates", "")),
        "size": _int(r.get("Size")),
        "af_raw": r.get("AF", ""),
        "ta": _clean(r.get("TA", "")),
        "hull_front": front, "hull_side": side,
        "turret_front": tf, "turret_side": ts,
        "armor": "unarmored" if "unarmored" in flags else ("partial" if flags else "full"),
        "armor_flags": flags,
        "open_topped": "•" in (r.get("OT") or ""),
        "ma": ma,
        "ma_mount": m.group(1) if m else None,
        "gun_traverse": _clean(r.get("GT", "")),
        "gun": f"{m.group(3)}{m.group(4) or ''}" if m and not m.group(2) else None,
        "rof": _clean(r.get("ROF", "")),
        "b_number": _clean(r.get("B#", "")),
        "special_ammo": _clean(r.get("Am", "")),
        "notes_raw": r.get("Notes", ""),
        "page": page + 1,
    }
    return rec


def _int(v):
    m = re.match(r"[+-]?\d+", _clean(v or ""))
    return int(m.group()) if m else None


def extract(pdf=PDF):
    doc = fitz.open(str(pdf))
    out = []
    for pno, nat in PAGES.items():
        page = doc[pno]
        words = page.get_text("words")
        name_x = min(w[0] for w in words if w[4] == "Name")
        heads = [h for h, _, _ in _headers(words, name_x)]
        layout = ("type_col" if "WGT" in heads and "Type" in heads
                  and heads.index("Type") > heads.index("WGT") else "name_type")
        for r in _merge(_rows(page)):
            r["_layout"] = layout
            out.append(_record(r, nat, pno))
    # Second pass: some "Name & Type" rows keep the code on the main strip
    # ("M13/40 MT"). Strip a trailing name token only if it is a code already
    # seen as a type elsewhere (roman numerals like "II" also look like codes).
    seen = {v["type"] for v in out if v["type"]}
    for v in out:
        parts = v["name"].split()
        # trailing OT bullets / footnote marks / stray digits after the code
        while len(parts) > 1 and (parts[-1] in ("•", "●") or re.fullmatch(r"[•●]?†\d*", parts[-1])
                                  or (parts[-1].isdigit() and len(parts) > 2
                                      and parts[-2] in seen)):
            parts.pop()
        if parts:
            parts[-1] = parts[-1].strip("“”").rstrip("†")
        v["name"] = " ".join(parts)
        if not v["type"] and len(parts) > 1 and parts[-1] in seen:
            v["type"] = parts.pop()
            v["name"] = " ".join(parts)
    return [v for v in out if v["name"] not in seen]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", help="print matching vehicles instead of writing")
    args = ap.parse_args()
    vehicles = extract()
    if args.show:
        for v in vehicles:
            if args.show.lower() in v["name"].lower():
                print(json.dumps(v, ensure_ascii=False))
        return
    OUT.write_text(json.dumps({"source": PDF.name, "vehicles": vehicles},
                              ensure_ascii=False, indent=1))
    by_nat = collections.Counter(v["nationality"] for v in vehicles)
    blank = [v["name"] for v in vehicles if "af_blank" in v["armor_flags"]]
    bad_af = [v["name"] for v in vehicles if v["armor"] != "unarmored"
              and (v["hull_front"] not in AF_SCALE or v["hull_side"] not in AF_SCALE)]
    print(f"wrote {len(vehicles)} vehicles to {OUT.relative_to(REPO)}")
    for nat, n in by_nat.items():
        print(f"  {nat:18s} {n}")
    print(f"blank AF (treated unarmored): {len(blank)}")
    print(f"AF off the D1.6 scale: {len(bad_af)} {bad_af[:20]}")


if __name__ == "__main__":
    sys.exit(main())
