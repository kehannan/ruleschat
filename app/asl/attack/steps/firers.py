"""Who fires, with what FP: side, eligibility, PBF, SW manning (A7, A9)."""
from typing import Dict, List, Set

from app.asl.attack.context import AttackContext, NoAttack
from app.asl.attack_resolver import (
    _all_hex_markers, _is_hs_or_crew, _is_squad, classify_unit, ski_state,
)


def firing_side(ctx: AttackContext) -> None:
    """The side firing: most Good Order personnel in the firing hex."""
    ctx.units = [u for u in ctx.fentry.get("units", [])]
    side_votes: Dict[str, int] = {}
    for u in ctx.units:
        cls = classify_unit(u)
        if cls["kind"] in ("personnel", "leader", "hero") and u.get("side") \
                and not u.get("broken"):
            side_votes[u["side"]] = side_votes.get(u["side"], 0) + 1
    if not side_votes:
        for u in ctx.units:
            if u.get("side") and not u.get("broken"):
                side_votes[u["side"]] = side_votes.get(u["side"], 0) + 1
    if not side_votes:
        raise NoAttack(f"No usable units in firing hex {ctx.fkey}.")
    ctx.firing_side = max(side_votes, key=side_votes.get)
    if len(side_votes) > 1:
        ctx.warnings.append(
            f"Firing hex {ctx.fkey} contains units of multiple sides "
            f"({', '.join(sorted(side_votes))}); firing side inferred as "
            f"{ctx.firing_side} (most Good Order personnel). Note the Melee rules "
            "if these units are locked in CC."  # VERIFY (V10)
        )
    if "Melee" in _all_hex_markers(ctx.fentry):
        ctx.warnings.append(
            "Firing hex is marked Melee: IFT attacks are not allowed by units "
            "in Melee (A7.21) — this resolution is hypothetical."
        )
    if "Melee" in _all_hex_markers(ctx.tentry):
        ctx.warnings.append(
            "Target hex is marked Melee: firing into a Melee Location is "
            "restricted and risks hitting friendly units; not modeled."  # VERIFY (V10)
        )


def eligible_firers(ctx: AttackContext) -> None:
    """Sort the firing hex into personnel, SW and leaders; exclude the rest."""
    filt = (ctx.firing_unit_filter or "").strip().lower()
    selected_names = [name.strip().lower() for name in (ctx.selected_firers or [])
                      if isinstance(name, str) and name.strip()]
    for u in ctx.units:
        cls = classify_unit(u)
        markers = u.get("markers") or []
        st = ski_state(u)
        if st == "worn":
            ctx.skis_worn = True
        elif st == "carried":
            ctx.skis_carried = True
        unit_name = (u.get("name") or "").lower()
        # A clicked VASL stack is authoritative: do not let neighboring
        # counters in the source hex join the attack. Match both directions
        # because VASSAL display names can include an extra counter suffix.
        if selected_names and cls["kind"] != "leader" and not any(
                chosen in unit_name or unit_name in chosen
                for chosen in selected_names):
            ctx.exclude(u, "not in the selected firing stack")
            continue
        if filt and filt not in unit_name and cls["kind"] != "leader":
            ctx.exclude(u, f"excluded by firing_unit_filter {ctx.firing_unit_filter!r}")
            continue
        if u.get("side") and u["side"] != ctx.firing_side:
            if cls["kind"] in ("sw", "ordnance"):
                ctx.exclude(u, f"enemy ({u['side']}) weapon — abandoned/captured; "
                               "not fired (captured use would be penalized, "
                               "A21.1x)")  # VERIFY (V9)
            else:
                ctx.exclude(u, f"enemy unit ({u['side']}) — not part of the "
                               f"{ctx.firing_side} attack")
            continue
        if u.get("broken"):
            ctx.exclude(u, "BROKEN — broken units may not attack (A10.5x)")
            continue
        if cls["kind"] == "ordnance":
            ctx.exclude(u, f"ordnance ({cls['detail']}) — resolves on the To-Hit "
                           "process (C3), NOT the IFT; excluded from this attack")
            continue
        if ctx.phase == "prep" and "Prep Fire" in markers:
            ctx.exclude(u, "already marked Prep Fire — has fired this PFPh")
            continue
        if ctx.phase in ("defensive_first", "defensive_final") and \
                ("First Fire" in markers or "Final Fire" in markers):
            ctx.warnings.append(
                f"{u.get('name')} is already marked "
                f"{'Final' if 'Final Fire' in markers else 'First'} Fire — "
                "Subsequent First Fire / FPF restrictions (A8.3, A8.31) apply "
                "and are not modeled."
            )
        if cls["kind"] == "leader":
            ctx.leaders.append({"unit": u, "cls": cls})
            continue
        if cls["kind"] == "hero":
            ctx.exclude(u, "hero/SMC inherent FP not modeled")  # VERIFY (V11)
            continue
        if cls["kind"] == "unknown":
            ctx.exclude(u, "unrecognized counter name — could not derive FP")
            continue
        if cls["kind"] == "personnel":
            ctx.personnel.append((u, cls))
        elif cls["kind"] == "sw":
            ctx.sws.append((u, cls))

    if not ctx.personnel and not ctx.sws:
        raise NoAttack(
            f"No eligible {ctx.firing_side} firers in {ctx.fkey} "
            f"(see exclusions: {[e['reason'] for e in ctx.excluded]})."
        )
    if ctx.sws and not ctx.personnel:
        raise NoAttack(
            f"SW in {ctx.fkey} have no Good Order {ctx.firing_side} manning unit — "
            "no valid attack."
        )
    n_squads = sum(1 for _, c in ctx.personnel if _is_squad(c))
    if len(ctx.sws) > max(n_squads, 1):
        ctx.warnings.append(
            "More MG/SW than squads in the firing stack: MG usage limits "
            "(firing extra MG forfeits inherent FP, A9.1x) are only partly enforced — "
            "totals may be optimistic."  # VERIFY (V8)
        )


def point_blank(ctx: AttackContext) -> None:
    """PBF / TPBF (A7.21, A7.211); denied when the firer is >= 2 levels
    below an adjacent target."""
    felev = (ctx.fentry.get("terrain") or {}).get("elevation")
    telev = (ctx.tentry.get("terrain") or {}).get("elevation")
    facts = ctx.facts
    if isinstance(facts, dict) and facts.get("source_height") is not None \
            and facts.get("target_height") is not None:
        # VASL's absolute Location heights (hill + building level)
        felev, telev = facts["source_height"], facts["target_height"]
    if ctx.rng == 0:
        ctx.pbf_mode = "tpbf"
    elif ctx.rng == 1:
        ctx.pbf_mode = "pbf"
        if felev is not None and telev is not None and telev - felev >= 2:
            ctx.pbf_mode = "none"  # VERIFY (V14)
            ctx.warnings.append(
                "PBF denied: firer appears ≥2 levels below the target "
                "(A7.21 requires within one level of or higher than the "
                "target)."
            )


def fire_strength(ctx: AttackContext) -> None:
    """Per-unit FP entries for the IFT: SW manning, Long Range, pinned,
    Assault Fire, Spraying Fire note (A7.22-.36, A9)."""
    rng, phase = ctx.rng, ctx.phase
    all_pinned = ctx.personnel and all("Pin" in (u.get("markers") or [])
                                       for u, _ in ctx.personnel)
    ctx.cx_present = any("CX" in (u.get("markers") or [])
                         for u, _ in ctx.personnel + ctx.sws)
    ctx.encircled_firer = any("Encircled" in (u.get("markers") or [])
                              for u, _ in ctx.personnel + ctx.sws)

    consumed_personnel_ids: Set[int] = set()
    sw_manning_notes: Dict[int, str] = {}
    if ctx.sws and not any(_is_squad(c) for _, c in ctx.personnel):
        manning = [(u, c) for u, c in ctx.personnel if _is_hs_or_crew(c)]
        for (sw_unit, _sw_cls), (man_unit, man_cls) in zip(ctx.sws, manning):
            consumed_personnel_ids.add(id(man_unit))
            sw_manning_notes[id(sw_unit)] = (
                f"manned by {man_unit.get('name')} ({man_cls.get('detail')}); "
                "that unit's inherent FP is not also used while firing the SW "
                "(A9.1x)"
            )
        if len(ctx.sws) > len(manning):
            ctx.warnings.append(
                "More SW than available HS/crew manning units in the firing "
                "stack; extra SW manning is not fully modeled."
            )

    def _add_firer(u, cls, kind_label):
        markers = u.get("markers") or []
        notes: List[str] = []
        if cls.get("detail") and cls["kind"] == "sw":
            notes.append(cls["detail"])  # generic-values note, VERIFY (V1)
        if id(u) in sw_manning_notes:
            notes.append(sw_manning_notes[id(u)])
        pinned = "Pin" in markers if cls["kind"] == "personnel" else all_pinned
        if pinned and cls["kind"] == "sw":
            notes.append("manning unit pinned — MG fires as Area Fire "
                         "(A7.81); applied as a halving")
        long_range = False
        nr = cls["normal_range"]
        if nr is not None and rng > nr:
            if cls.get("no_long_range") or rng > 2 * nr:
                ctx.exclude(u, f"target at range {rng} exceeds "
                               f"{'Normal Range (no Long Range use)' if cls.get('no_long_range') else 'double Normal Range'}"
                               f" of {nr} (A7.22)")
                return
            long_range = True
            notes.append(f"Long Range (range {rng} > Normal Range {nr}): "
                         "FP halved (A7.22)")
        if u.get("concealed_by"):
            notes.append("firer is concealed — firing forfeits concealment "
                         "(A12.41); no FP effect")
        # A7.36 Assault Fire (verified verbatim, eASLRB v3.14): "Assault Fire
        # capability allows any squad using its inherent FP during the AFPh
        # to add one FP to its Small Arms Fire attack after all modification
        # to the squad's inherent FP; any fraction in its FP is then rounded
        # up. The Assault Fire bonus is not applicable to Opportunity Fire or
        # Long Range Fire, but is still applicable to pinned-firers/
        # Spraying-Fire in the AFPh." Capability = the underscored FP factor,
        # read from the counter art (unit_capabilities). The ift engine
        # encodes the effect (incl. the Long Range NA); squads only — SW and
        # HS/crew counters carry no underscore.
        assault_fire = False
        if cls["kind"] == "personnel":
            af = cls.get("assault_fire")
            if phase == "advancing":
                if af:
                    assault_fire = True
                    notes.append(
                        "Assault Fire: +1 FP after all other modification, "
                        "fractions rounded up (A7.36) — underscored FP, "
                        f"{cls.get('cap_source')}")
                elif af is None:
                    ctx.warnings.append(
                        f"{u.get('name')}: Assault Fire capability UNKNOWN "
                        "(counter not in the capability table) — the A7.36 "
                        "+1 is NOT applied. If its FP factor is underscored, "
                        "re-run via ift_attack with assault_fire set."
                    )
                if cls.get("cap_note"):
                    notes.append(cls["cap_note"])
            if cls.get("spraying_fire") and rng <= 3:
                notes.append(
                    "capable of Spraying Fire (underscored Range, A7.34): "
                    "could instead attack two adjacent Locations within 3 "
                    "hexes as Area Fire — NOT applied here (requires a "
                    "two-Location target choice)")
        ctx.ift_units.append({"fp": cls["fp"], "pbf": ctx.pbf_mode,
                              "long_range": long_range, "pinned": bool(pinned),
                              "assault_fire": assault_fire})
        ctx.firer_rows.append({
            "name": u.get("name"), "kind": kind_label, "fp": cls["fp"],
            "normal_range": nr, "morale": cls["morale"],
            "pinned": bool(pinned),
            "assault_fire": cls.get("assault_fire"),
            "spraying_fire": cls.get("spraying_fire"),
            "markers": markers, "notes": notes,
        })

    for u, cls in ctx.personnel:
        if id(u) in consumed_personnel_ids:
            ctx.exclude(u, "manning a SW — HS/crew cannot also use inherent FP (A9.1x)")
            continue
        _add_firer(u, cls, "personnel")
    for u, cls in ctx.sws:
        _add_firer(u, cls, "sw")
    if not ctx.ift_units:
        raise NoAttack(
            f"All candidate firers in {ctx.fkey} were excluded "
            f"({[e['reason'] for e in ctx.excluded]})."
        )


def leadership(ctx: AttackContext) -> None:
    """Best (most negative) directing leader; a +DRM leader doesn't direct
    (A7.53, A7.531); wounded leaders are one worse (A17.3)."""
    for l in ctx.leaders:
        u, cls = l["unit"], l["cls"]
        if "Pin" in (u.get("markers") or []):
            ctx.warnings.append(
                f"Leader {u.get('name')} is pinned — direction not applied "
                "(a pinned leader's direction is degraded; not modeled)."
            )
            continue
        if cls["leadership"] is None:
            ctx.warnings.append(
                f"Leader {u.get('name')} is a generic counter — leadership "
                "DRM not recoverable from the save; omitted. If he directs "
                "the attack, apply his printed DRM and note that ANY "
                "direction also prevents cowering (A7.9, A7.531)."
            )
            continue
        ld = cls["leadership"]
        if u.get("wounded"):
            ld += 1
            ctx.caveats.append(
                f"Leader {u.get('name')} is WOUNDED: his leadership DRM is "
                f"worsened by one, {cls['leadership']:+d} -> {ld:+d} "
                "(A17.3); applied if he directs."
            )
        if not ctx.leader_directs or ld < ctx.leadership:
            ctx.leadership = ld
            ctx.leader_directs = True
    if ctx.leader_directs and ctx.leadership > 0:
        # A positive-DRM leader would not normally be chosen to direct.
        ctx.warnings.append(
            f"Only leader available has a +{ctx.leadership} modifier — direction "
            "is optional, so he is assumed NOT to direct; cowering applies "
            "normally (A7.531)."
        )
        ctx.leadership = 0
        ctx.leader_directs = False
