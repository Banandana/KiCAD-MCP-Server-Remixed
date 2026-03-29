"""
Schematic group analysis and orthogonal rewiring.

Two tools:
1. analyze_schematic_group — classify component roles in a circuit group
2. rewire_group_orthogonal — delete diagonal wires, redraw as L-shaped routes
"""

import re
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("kicad_interface")

# Re-use existing infrastructure
from commands.net_analysis import (
    _parse_wires,
    _parse_labels,
    _parse_power_symbols,
    _UnionFind,
    _build_wire_spatial_index,
    _find_t_junctions,
    _snap_pt,
)
from commands.pin_locator import PinLocator, parse_placed_symbols_from_content
from commands.sexp_writer import (
    add_wire_to_content,
    auto_add_t_junctions,
    _fmt,
    _read_schematic,
    _write_schematic,
    delete_wire_from_content,
)


# ── Shared helpers ──────────────────────────────────────────────────


def _snap_grid(v, grid=1.27):
    """Snap a coordinate to the schematic grid."""
    return round(round(v / grid) * grid, 4)


def _compute_pin_endpoints_from_content(content, components, pin_locator, schematic_path):
    """Content-based pin endpoint computation (no kicad-skip).

    Returns list of (ref, pin_num, pin_name, pin_x, pin_y, value, lib_id)
    for the listed components only.
    """
    sch_file = Path(schematic_path)
    symbols = parse_placed_symbols_from_content(content)
    results = []

    for sym in symbols:
        ref = sym.get("reference", "")
        if ref not in components:
            continue
        if ref.startswith("_TEMPLATE"):
            continue

        lib_id = sym.get("lib_id", "")
        value = sym.get("value", "")
        sx = sym.get("x", 0.0)
        sy = sym.get("y", 0.0)
        sym_rot = sym.get("rotation", 0.0)
        mirror_x = sym.get("mirror_x", False)
        mirror_y = sym.get("mirror_y", False)

        pins_def = pin_locator.get_symbol_pins(sch_file, lib_id)
        if not pins_def:
            continue

        for pin_num, pd in pins_def.items():
            prx = pd["x"]
            pry = -pd["y"]  # Y-up to Y-down
            if mirror_x:
                pry = -pry
            if mirror_y:
                prx = -prx
            prx, pry = PinLocator.rotate_point(prx, pry, sym_rot)
            pin_x = sx + prx
            pin_y = sy + pry
            results.append(
                (ref, pin_num, pd.get("name", pin_num), pin_x, pin_y, value, lib_id)
            )

    return results


def _build_group_net_graph(content, components, pin_locator, schematic_path, tolerance=0.5):
    """Build net graph for a set of components using content-based parsing.

    Returns:
        pin_nets: dict (ref, pin_num) -> net_name | None
        components_data: dict ref -> {lib_id, value, pins: {pin_num: {net, name, x, y}}}
        net_pins: dict net_name -> [(ref, pin_num, pin_name)]
    """
    all_wires = _parse_wires(content)
    all_labels = _parse_labels(content)
    power_syms = _parse_power_symbols(content)

    # Union-find over wire connectivity
    uf = _UnionFind()
    h_index, v_index = _build_wire_spatial_index(all_wires)

    for x1, y1, x2, y2 in all_wires:
        ep1 = _snap_pt(x1, y1)
        ep2 = _snap_pt(x2, y2)
        uf.union(ep1, ep2)

    all_endpoints = set()
    for x1, y1, x2, y2 in all_wires:
        all_endpoints.add(_snap_pt(x1, y1))
        all_endpoints.add(_snap_pt(x2, y2))

    for pt in all_endpoints:
        _find_t_junctions(pt, h_index, v_index, all_wires, uf, tolerance)

    # Register labels and power symbols
    for lx, ly, _name, _lt in all_labels:
        sp = _snap_pt(lx, ly)
        uf.find(sp)
        if sp not in all_endpoints:
            _find_t_junctions(sp, h_index, v_index, all_wires, uf, tolerance)

    for px, py, _name in power_syms:
        sp = _snap_pt(px, py)
        uf.find(sp)
        if sp not in all_endpoints:
            _find_t_junctions(sp, h_index, v_index, all_wires, uf, tolerance)

    # Assign net names to roots
    root_to_net = {}
    for lx, ly, name, _lt in all_labels:
        sp = _snap_pt(lx, ly)
        root = uf.find(sp)
        if root not in root_to_net:
            root_to_net[root] = name

    for px, py, name in power_syms:
        sp = _snap_pt(px, py)
        root = uf.find(sp)
        if root not in root_to_net:
            root_to_net[root] = name

    # Compute pin endpoints for group components
    component_set = set(components)
    all_pins = _compute_pin_endpoints_from_content(
        content, component_set, pin_locator, schematic_path
    )

    pin_nets = {}
    net_pins = {}
    components_data = {}

    for ref, pin_num, pin_name, px, py, value, lib_id in all_pins:
        if ref not in components_data:
            components_data[ref] = {"lib_id": lib_id, "value": value, "pins": {}}

        sp = _snap_pt(px, py)
        matched_net = None

        if sp in uf.parent:
            root = uf.find(sp)
            matched_net = root_to_net.get(root)
        else:
            _find_t_junctions(sp, h_index, v_index, all_wires, uf, tolerance)
            if sp in uf.parent:
                root = uf.find(sp)
                matched_net = root_to_net.get(root)

        if matched_net is None:
            for lx, ly, name, _lt in all_labels:
                if abs(px - lx) < tolerance and abs(py - ly) < tolerance:
                    matched_net = name
                    break
        if matched_net is None:
            for ppx, ppy, pname in power_syms:
                if abs(px - ppx) < tolerance and abs(py - ppy) < tolerance:
                    matched_net = pname
                    break

        pin_nets[(ref, pin_num)] = matched_net
        components_data[ref]["pins"][pin_num] = {
            "name": pin_name, "net": matched_net,
            "x": round(px, 2), "y": round(py, 2),
        }
        if matched_net is not None:
            net_pins.setdefault(matched_net, []).append((ref, pin_num, pin_name))

    return pin_nets, net_pins, components_data


# ── Tool 1: analyze_schematic_group ─────────────────────────────────


_GROUND_NETS = {"GND", "GNDREF", "GNDD", "GNDA", "AGND", "DGND", "PGND", "VSS", "VSSA", "VSSD", "0V"}
_POWER_PREFIXES = ("+", "V", "VDD", "VCC", "VBUS")


def _is_ground_net(name):
    if not name:
        return False
    return name.upper() in _GROUND_NETS or name.upper().startswith("GND")


def _is_power_net(name):
    if not name:
        return False
    upper = name.upper()
    if upper in _GROUND_NETS:
        return False
    if any(upper.startswith(p) for p in _POWER_PREFIXES):
        return True
    if upper.endswith("V") and any(c.isdigit() for c in upper):
        return True
    return False


def _is_passive_2pin(comp_data):
    """Check if component is a 2-pin passive (R, C, L, D, etc.)."""
    ref_prefix = ""
    lib_id = comp_data.get("lib_id", "")
    pins = comp_data.get("pins", {})
    return len(pins) == 2


def _get_pin_nets(comp_data):
    """Get list of (pin_num, net_name) for a component."""
    pins = comp_data.get("pins", {})
    return [(pn, p.get("net")) for pn, p in pins.items()]


def analyze_schematic_group(schematic_path, components, pin_locator):
    """Analyze a group of components and classify their roles.

    Returns dict with primaryIC, roles, rails, interSectionLabels.
    """
    content = _read_schematic(Path(schematic_path))
    component_set = set(components)

    pin_nets, net_pins, comp_data = _build_group_net_graph(
        content, component_set, pin_locator, schematic_path
    )

    # Find primary IC (most pins, excluding power symbols)
    primary_ic = None
    max_pins = 0
    for ref in components:
        cd = comp_data.get(ref)
        if not cd:
            continue
        if cd.get("lib_id", "").startswith("power:"):
            continue
        pin_count = len(cd.get("pins", {}))
        if pin_count > max_pins:
            max_pins = pin_count
            primary_ic = ref

    # Get all nets used by the primary IC
    ic_nets = set()
    ic_pin_by_net = {}  # net -> pin_name
    if primary_ic and primary_ic in comp_data:
        for pn, pinfo in comp_data[primary_ic]["pins"].items():
            net = pinfo.get("net")
            if net:
                ic_nets.add(net)
                ic_pin_by_net[net] = f"{primary_ic}/{pinfo.get('name', pn)}"

    # Classify each component
    roles = {}
    for ref in components:
        cd = comp_data.get(ref)
        if not cd:
            roles[ref] = {"role": "unknown", "reason": "not found in schematic"}
            continue

        if ref == primary_ic:
            roles[ref] = {"role": "primary_ic", "pinCount": len(cd["pins"])}
            continue

        pin_list = _get_pin_nets(cd)
        lib_id = cd.get("lib_id", "")

        # Power symbol
        if lib_id.startswith("power:"):
            roles[ref] = {"role": "power_symbol", "net": pin_list[0][1] if pin_list else None}
            continue

        # Test point (reference starts with TP or single pin)
        if ref.startswith("TP") or len(pin_list) == 1:
            net = pin_list[0][1] if pin_list else None
            roles[ref] = {"role": "test_point", "connectedNet": net}
            continue

        if len(pin_list) != 2:
            # Multi-pin non-IC component
            connected_nets = [n for _, n in pin_list if n]
            roles[ref] = {"role": "auxiliary", "nets": connected_nets}
            continue

        # 2-pin passive classification
        net_a = pin_list[0][1]
        net_b = pin_list[1][1]

        a_is_gnd = _is_ground_net(net_a)
        b_is_gnd = _is_ground_net(net_b)
        a_is_power = _is_power_net(net_a)
        b_is_power = _is_power_net(net_b)
        a_is_ic = net_a in ic_nets
        b_is_ic = net_b in ic_nets

        # Decoupling/bypass cap: one pin on IC net or power, other on GND
        if ref.startswith("C"):
            if (a_is_ic or a_is_power) and b_is_gnd:
                ic_pin = ic_pin_by_net.get(net_a, net_a)
                roles[ref] = {"role": "decoupling_cap", "connectedPin": ic_pin, "otherNet": net_b}
                continue
            if (b_is_ic or b_is_power) and a_is_gnd:
                ic_pin = ic_pin_by_net.get(net_b, net_b)
                roles[ref] = {"role": "decoupling_cap", "connectedPin": ic_pin, "otherNet": net_a}
                continue
            if a_is_ic and b_is_ic:
                pin_a = ic_pin_by_net.get(net_a, net_a)
                pin_b = ic_pin_by_net.get(net_b, net_b)
                roles[ref] = {"role": "bootstrap_cap", "connectedBetween": [pin_a, pin_b]}
                continue

        # Resistor classification
        if ref.startswith("R"):
            # Pullup: IC signal pin to power rail
            if a_is_ic and b_is_power:
                roles[ref] = {"role": "pullup", "connectedPin": ic_pin_by_net.get(net_a, net_a), "otherNet": net_b}
                continue
            if b_is_ic and a_is_power:
                roles[ref] = {"role": "pullup", "connectedPin": ic_pin_by_net.get(net_b, net_b), "otherNet": net_a}
                continue
            # Pulldown: IC signal pin to GND
            if a_is_ic and b_is_gnd:
                roles[ref] = {"role": "pulldown", "connectedPin": ic_pin_by_net.get(net_a, net_a), "otherNet": net_b}
                continue
            if b_is_ic and a_is_gnd:
                roles[ref] = {"role": "pulldown", "connectedPin": ic_pin_by_net.get(net_b, net_b), "otherNet": net_a}
                continue

        # Series element: connects two non-GND, non-power nets (inductor, series resistor)
        if not a_is_gnd and not b_is_gnd:
            if a_is_ic and b_is_ic:
                pin_a = ic_pin_by_net.get(net_a, net_a)
                pin_b = ic_pin_by_net.get(net_b, net_b)
                roles[ref] = {"role": "series_element", "connectedBetween": [pin_a, pin_b]}
                continue
            if a_is_ic or b_is_ic:
                ic_net = net_a if a_is_ic else net_b
                other_net = net_b if a_is_ic else net_a
                roles[ref] = {"role": "series_element", "connectedBetween": [ic_pin_by_net.get(ic_net, ic_net), other_net]}
                continue

        # Fallback for caps/passives connected between two non-IC nets
        if a_is_gnd or b_is_gnd:
            non_gnd = net_a if b_is_gnd else net_b
            gnd = net_b if b_is_gnd else net_a
            roles[ref] = {"role": "bypass_element", "connectedNet": non_gnd, "otherNet": gnd}
            continue

        roles[ref] = {"role": "passive", "nets": [net_a, net_b]}

    # Detect feedback dividers: two resistors where one connects output→mid, other mid→GND
    resistors = [r for r in components if r.startswith("R") and r in roles and roles[r].get("role") in ("pulldown", "series_element", "passive")]
    for i, r1 in enumerate(resistors):
        for r2 in resistors[i + 1:]:
            r1_nets = set(n for _, n in _get_pin_nets(comp_data.get(r1, {})) if n)
            r2_nets = set(n for _, n in _get_pin_nets(comp_data.get(r2, {})) if n)
            shared = r1_nets & r2_nets
            if len(shared) == 1:
                mid_net = shared.pop()
                # Check if mid_net connects to an IC FB/VSEN/ADJ pin
                if mid_net in ic_nets:
                    ic_pin = ic_pin_by_net.get(mid_net, mid_net)
                    r1_other = (r1_nets - {mid_net}).pop() if len(r1_nets) > 1 else None
                    r2_other = (r2_nets - {mid_net}).pop() if len(r2_nets) > 1 else None
                    # Top is the one NOT connected to GND
                    if _is_ground_net(r2_other):
                        roles[r1] = {"role": "feedback_top", "dividerPartner": r2, "fromNet": r1_other, "toNet": mid_net}
                        roles[r2] = {"role": "feedback_bottom", "dividerPartner": r1, "fromNet": mid_net, "toNet": r2_other}
                    elif _is_ground_net(r1_other):
                        roles[r2] = {"role": "feedback_top", "dividerPartner": r1, "fromNet": r2_other, "toNet": mid_net}
                        roles[r1] = {"role": "feedback_bottom", "dividerPartner": r2, "fromNet": mid_net, "toNet": r1_other}

    # Identify rails
    all_group_nets = set()
    for ref in components:
        cd = comp_data.get(ref, {})
        for pn, pinfo in cd.get("pins", {}).items():
            if pinfo.get("net"):
                all_group_nets.add(pinfo["net"])

    rails = {"input": [], "output": [], "ground": []}
    for net in all_group_nets:
        if _is_ground_net(net):
            rails["ground"].append(net)
        elif _is_power_net(net):
            # Heuristic: if net connects to IC input-side pin, it's input; otherwise output
            rails.setdefault("power", []).append(net)

    # Inter-section labels: global/hierarchical labels on group nets
    all_labels_parsed = _parse_labels(content)
    inter_section = set()
    for lx, ly, name, lt in all_labels_parsed:
        if name in all_group_nets and lt in ("global_label", "hierarchical_label"):
            inter_section.add(name)

    return {
        "success": True,
        "primaryIC": primary_ic,
        "roles": roles,
        "rails": rails,
        "interSectionLabels": sorted(inter_section),
        "componentCount": len(components),
        "analyzedCount": len([r for r in components if r in comp_data]),
    }


# ── Tool 2: rewire_group_orthogonal ─────────────────────────────────


def _parse_wire_blocks(content):
    """Parse wire blocks with positions for deletion.

    Returns list of (x1, y1, x2, y2, block_start, block_end).
    """
    wires = []
    wire_pat = re.compile(r"\(wire\b")
    xy_pat = re.compile(r"\(xy\s+([\d.e+-]+)\s+([\d.e+-]+)\)")

    for wm in wire_pat.finditer(content):
        depth = 0
        i = wm.start()
        block_end = i
        while i < len(content):
            if content[i] == "(":
                depth += 1
            elif content[i] == ")":
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break
            i += 1
        # Include trailing whitespace/newline for clean deletion
        while block_end < len(content) and content[block_end] in (" ", "\t", "\n", "\r"):
            block_end += 1

        block = content[wm.start():block_end]
        xys = xy_pat.findall(block)
        if len(xys) >= 2:
            wires.append((
                float(xys[0][0]), float(xys[0][1]),
                float(xys[-1][0]), float(xys[-1][1]),
                wm.start(), block_end,
            ))
    return wires


def _build_pin_index(all_pins, tolerance=0.1):
    """Build spatial index: snapped (x,y) -> (ref, pin_num).

    Uses grid-snapped coordinates for O(1) lookup.
    """
    index = {}
    for ref, pin_num, _name, px, py, _val, _lib in all_pins:
        key = (round(px, 1), round(py, 1))
        index[key] = (ref, pin_num)
    return index


def _point_in_index(x, y, index, tolerance=0.5):
    """Check if a point matches any pin in the index within tolerance."""
    # Check exact and nearby grid points
    for dx in (-tolerance, 0, tolerance):
        for dy in (-tolerance, 0, tolerance):
            key = (round(x + dx, 1), round(y + dy, 1))
            if key in index:
                return index[key]
    return None


def _segment_crosses_bbox(x1, y1, x2, y2, bx1, by1, bx2, by2, margin=0.5):
    """Check if a wire segment crosses a bounding box (with margin shrink)."""
    # Shrink bbox by margin to avoid false positives at pin tips
    bx1 += margin
    by1 += margin
    bx2 -= margin
    by2 -= margin
    if bx1 >= bx2 or by1 >= by2:
        return False

    # Simple AABB line intersection test
    # Check if segment is completely outside
    if max(x1, x2) < bx1 or min(x1, x2) > bx2:
        return False
    if max(y1, y2) < by1 or min(y1, y2) > by2:
        return False

    # For orthogonal segments (which is what we generate), check intersection
    if abs(x1 - x2) < 0.01:  # vertical
        return bx1 <= x1 <= bx2 and not (min(y1, y2) > by2 or max(y1, y2) < by1)
    if abs(y1 - y2) < 0.01:  # horizontal
        return by1 <= y1 <= by2 and not (min(x1, x2) > bx2 or max(x1, x2) < bx1)

    return True  # diagonal — always flagged


def _compute_component_bboxes(all_pins, components):
    """Compute bounding boxes from pin positions for each component."""
    bboxes = {}
    comp_pins = {}
    for ref, _pn, _name, px, py, _val, _lib in all_pins:
        if ref in components:
            comp_pins.setdefault(ref, []).append((px, py))

    for ref, pins in comp_pins.items():
        if not pins:
            continue
        xs = [p[0] for p in pins]
        ys = [p[1] for p in pins]
        # Expand by 1.5mm to approximate component body
        bboxes[ref] = (min(xs) - 1.5, min(ys) - 1.5, max(xs) + 1.5, max(ys) + 1.5)
    return bboxes


def rewire_group_orthogonal(schematic_path, components, pin_locator, routing_style="auto"):
    """Delete direct wires between group components and redraw as orthogonal routes.

    Preserves label-connected wires. Only rewires direct pin-to-pin connections.
    """
    sch_path = Path(schematic_path)
    content = _read_schematic(sch_path)
    component_set = set(components)

    # Step 1: Get pin positions for all group components
    all_pins = _compute_pin_endpoints_from_content(
        content, component_set, pin_locator, schematic_path
    )
    if not all_pins:
        return {"success": False, "message": "No pins found for listed components"}

    pin_index = _build_pin_index(all_pins)
    comp_bboxes = _compute_component_bboxes(all_pins, component_set)

    # Step 2: Find wires where BOTH endpoints are at group pin positions
    wire_blocks = _parse_wire_blocks(content)
    wires_to_delete = []  # (block_start, block_end)
    connected_pairs = set()  # frozenset of ((ref,pin), (ref,pin))

    for x1, y1, x2, y2, bstart, bend in wire_blocks:
        match_a = _point_in_index(x1, y1, pin_index)
        match_b = _point_in_index(x2, y2, pin_index)
        if match_a and match_b:
            # Both endpoints at group pins — this is a direct internal wire
            wires_to_delete.append((bstart, bend))
            pair = frozenset([match_a, match_b])
            connected_pairs.add(pair)

    if not wires_to_delete:
        return {
            "success": True,
            "message": "No direct pin-to-pin wires found between group components",
            "deletedWires": 0,
            "addedWires": 0,
            "pinPairsRewired": 0,
        }

    # Step 3: Delete wires (reverse order to preserve positions)
    wires_to_delete.sort(key=lambda w: w[0], reverse=True)
    for bstart, bend in wires_to_delete:
        content = content[:bstart] + content[bend:]

    deleted_count = len(wires_to_delete)

    # Step 4: Build pin position lookup for route computation
    pin_positions = {}  # (ref, pin_num) -> (x, y)
    for ref, pin_num, _name, px, py, _val, _lib in all_pins:
        pin_positions[(ref, pin_num)] = (px, py)

    # Step 5: Compute and add orthogonal routes
    added_count = 0
    new_endpoints = []

    for pair in connected_pairs:
        pair_list = list(pair)
        if len(pair_list) != 2:
            continue
        pos_a = pin_positions.get(pair_list[0])
        pos_b = pin_positions.get(pair_list[1])
        if not pos_a or not pos_b:
            continue

        x1, y1 = _snap_grid(pos_a[0]), _snap_grid(pos_a[1])
        x2, y2 = _snap_grid(pos_b[0]), _snap_grid(pos_b[1])

        if abs(x1 - x2) < 0.01 or abs(y1 - y2) < 0.01:
            # Colinear — single straight wire
            content = add_wire_to_content(content, [x1, y1], [x2, y2])
            new_endpoints.extend([(x1, y1), (x2, y2)])
            added_count += 1
        else:
            # L-shaped route: choose style
            style = routing_style
            if style == "auto":
                h_first_crosses = any(
                    _segment_crosses_bbox(x2, y1, x2, y2, *bb)
                    for ref, bb in comp_bboxes.items()
                )
                v_first_crosses = any(
                    _segment_crosses_bbox(x1, y2, x2, y2, *bb)
                    for ref, bb in comp_bboxes.items()
                )
                if h_first_crosses and not v_first_crosses:
                    style = "vertical_first"
                else:
                    style = "horizontal_first"

            if style == "horizontal_first":
                content = add_wire_to_content(content, [x1, y1], [x2, y1])
                content = add_wire_to_content(content, [x2, y1], [x2, y2])
                new_endpoints.extend([(x1, y1), (x2, y1), (x2, y2)])
            else:
                content = add_wire_to_content(content, [x1, y1], [x1, y2])
                content = add_wire_to_content(content, [x1, y2], [x2, y2])
                new_endpoints.extend([(x1, y1), (x1, y2), (x2, y2)])
            added_count += 2

    # Step 6: Auto-add T-junction dots at new wire intersections
    content, _junc_count = auto_add_t_junctions(content, new_endpoints)

    # Step 7: Write
    _write_schematic(sch_path, content)

    # Step 8: Validate
    validation = {"crossingSymbols": 0}
    try:
        from commands.schematic_analysis import find_wires_crossing_symbols
        crossings = find_wires_crossing_symbols(sch_path)
        validation["crossingSymbols"] = len(crossings)
    except Exception as e:
        logger.warning(f"Validation skipped: {e}")

    return {
        "success": True,
        "deletedWires": deleted_count,
        "addedWires": added_count,
        "pinPairsRewired": len(connected_pairs),
        "validation": validation,
        "message": f"Rewired {len(connected_pairs)} pin pairs: deleted {deleted_count} wires, added {added_count} wire segments",
    }
