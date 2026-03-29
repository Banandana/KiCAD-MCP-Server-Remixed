"""
Schematic group analysis, layout computation, and orthogonal rewiring.

Three tools:
1. analyze_schematic_group — classify component roles in a circuit group
2. compute_group_layout — position passives around IC following conventions
3. rewire_group_orthogonal — delete diagonal wires, redraw as L-shaped routes
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


# ── Tool 2: compute_group_layout ────────────────────────────────────


def _get_ic_pin_sides(all_pins, ic_ref):
    """Classify IC pins by side (left/right/top/bottom) based on position relative to IC center.

    Returns dict: pin_num -> {"side": "left"|"right"|"top"|"bottom", "x": float, "y": float, "name": str}
    """
    ic_pins = [(pn, name, px, py) for ref, pn, name, px, py, _v, _l in all_pins if ref == ic_ref]
    if not ic_pins:
        return {}

    # Compute IC center from pin positions
    cx = sum(p[2] for p in ic_pins) / len(ic_pins)
    cy = sum(p[3] for p in ic_pins) / len(ic_pins)

    result = {}
    for pn, name, px, py in ic_pins:
        dx = px - cx
        dy = py - cy
        # Classify by which offset is dominant
        if abs(dx) > abs(dy):
            side = "right" if dx > 0 else "left"
        else:
            side = "bottom" if dy > 0 else "top"
        result[pn] = {"side": side, "x": px, "y": py, "name": name}

    return result


def _get_component_current_pos(all_pins, ref):
    """Get a component's approximate center from its pin positions."""
    pins = [(px, py) for r, _pn, _name, px, py, _v, _l in all_pins if r == ref]
    if not pins:
        return None
    return (sum(p[0] for p in pins) / len(pins), sum(p[1] for p in pins) / len(pins))


def compute_group_layout(schematic_path, components, pin_locator, anchor=None, constraints=None):
    """Compute positions for group components following schematic conventions.

    Places the primary IC at anchor, then positions passives around it:
    - Decoupling caps in a row below the IC
    - Series elements on the side of their connected IC pins
    - Feedback dividers vertically stacked
    - Test points at group edges
    - Power symbols and GND placed after component positioning

    Returns computed positions without applying them. Use apply_group_layout to apply.
    """
    content = _read_schematic(Path(schematic_path))
    component_set = set(components)

    # Parse and analyze
    all_pins = _compute_pin_endpoints_from_content(
        content, component_set, pin_locator, schematic_path
    )
    if not all_pins:
        return {"success": False, "message": "No pins found for listed components"}

    # Run analysis to get roles
    analysis = analyze_schematic_group(schematic_path, components, pin_locator)
    if not analysis.get("success"):
        return analysis

    roles = analysis.get("roles", {})
    primary_ic = analysis.get("primaryIC")
    if not primary_ic:
        return {"success": False, "message": "No primary IC identified in group"}

    # Default constraints
    c = {
        "spacing": 6.35,          # min component spacing (mm)
        "capRowGap": 3.81,        # gap between IC body and cap row
        "capSpacing": 5.08,       # spacing between caps in row
        "seriesOffset": 10.16,    # distance from IC body for series elements
        "dividerSpacing": 5.08,   # vertical spacing for feedback divider
        "testPointOffset": 15.24, # distance for test points from IC
    }
    if constraints:
        c.update(constraints)

    # Get IC geometry
    ic_pin_sides = _get_ic_pin_sides(all_pins, primary_ic)
    ic_pins_list = [(px, py) for ref, _pn, _name, px, py, _v, _l in all_pins if ref == primary_ic]
    if not ic_pins_list:
        return {"success": False, "message": f"No pins found for primary IC {primary_ic}"}

    ic_cx = sum(p[0] for p in ic_pins_list) / len(ic_pins_list)
    ic_cy = sum(p[1] for p in ic_pins_list) / len(ic_pins_list)
    ic_x1 = min(p[0] for p in ic_pins_list) - 2.54
    ic_y1 = min(p[1] for p in ic_pins_list) - 2.54
    ic_x2 = max(p[0] for p in ic_pins_list) + 2.54
    ic_y2 = max(p[1] for p in ic_pins_list) + 2.54

    # If anchor provided, compute delta to move IC there; otherwise IC stays
    if anchor:
        ic_dx = anchor.get("x", ic_cx) - ic_cx
        ic_dy = anchor.get("y", ic_cy) - ic_cy
    else:
        ic_dx = 0
        ic_dy = 0

    # Get current positions for all components
    current_positions = {}
    symbols = parse_placed_symbols_from_content(content)
    for sym in symbols:
        ref = sym.get("reference", "")
        if ref in component_set:
            current_positions[ref] = {"x": sym.get("x", 0), "y": sym.get("y", 0), "rotation": sym.get("rotation", 0)}

    # Compute new positions
    new_positions = {}

    # IC at anchor (or unchanged)
    if primary_ic in current_positions:
        ic_pos = current_positions[primary_ic]
        new_positions[primary_ic] = {
            "x": round(ic_pos["x"] + ic_dx, 4),
            "y": round(ic_pos["y"] + ic_dy, 4),
        }

    # Classify passives by placement zone
    decoupling_caps = []
    left_side = []
    right_side = []
    top_side = []
    bottom_side = []
    feedback_top = None
    feedback_bottom = None
    test_points = []
    unplaced = []

    for ref in components:
        if ref == primary_ic:
            continue
        role_info = roles.get(ref, {})
        role = role_info.get("role", "unknown")

        if role in ("decoupling_cap", "bypass_cap", "bypass_element"):
            decoupling_caps.append(ref)
        elif role == "feedback_top":
            feedback_top = ref
        elif role == "feedback_bottom":
            feedback_bottom = ref
        elif role in ("series_element", "bootstrap_cap"):
            # Place on the side of the connected IC pin
            connected = role_info.get("connectedBetween", [])
            connected_pin = role_info.get("connectedPin", "")
            # Find which side the connected pin is on
            placed = False
            for pin_id in ([connected_pin] if connected_pin else connected):
                # pin_id might be "U10/VIN" or just a net name
                if "/" in str(pin_id):
                    pin_name = str(pin_id).split("/", 1)[1]
                    for pn, pdata in ic_pin_sides.items():
                        if pdata["name"] == pin_name:
                            side = pdata["side"]
                            if side == "left":
                                left_side.append(ref)
                            elif side == "right":
                                right_side.append(ref)
                            elif side == "top":
                                top_side.append(ref)
                            else:
                                bottom_side.append(ref)
                            placed = True
                            break
                if placed:
                    break
            if not placed:
                right_side.append(ref)  # default to right
        elif role in ("pullup", "pulldown"):
            # Place on the side of the connected IC pin
            connected_pin = role_info.get("connectedPin", "")
            placed = False
            if "/" in str(connected_pin):
                pin_name = str(connected_pin).split("/", 1)[1]
                for pn, pdata in ic_pin_sides.items():
                    if pdata["name"] == pin_name:
                        side = pdata["side"]
                        if side == "left":
                            left_side.append(ref)
                        elif side == "right":
                            right_side.append(ref)
                        elif side == "top":
                            top_side.append(ref)
                        else:
                            bottom_side.append(ref)
                        placed = True
                        break
            if not placed:
                right_side.append(ref)
        elif role == "test_point":
            test_points.append(ref)
        elif role in ("power_symbol",):
            # Power symbols will be placed after components
            pass
        else:
            unplaced.append(ref)

    # Apply IC offset to all IC-relative coordinates
    new_ic_x1 = ic_x1 + ic_dx
    new_ic_y1 = ic_y1 + ic_dy
    new_ic_x2 = ic_x2 + ic_dx
    new_ic_y2 = ic_y2 + ic_dy
    new_ic_cx = ic_cx + ic_dx
    new_ic_cy = ic_cy + ic_dy

    # Place decoupling caps in a horizontal row below IC
    cap_y = new_ic_y2 + c["capRowGap"]
    cap_start_x = new_ic_cx - (len(decoupling_caps) - 1) * c["capSpacing"] / 2
    for i, ref in enumerate(decoupling_caps):
        new_positions[ref] = {
            "x": round(cap_start_x + i * c["capSpacing"], 4),
            "y": round(cap_y, 4),
        }

    # Place left-side components
    left_x = new_ic_x1 - c["seriesOffset"]
    for i, ref in enumerate(left_side):
        # Align Y with the connected IC pin if possible
        target_y = new_ic_cy + (i - len(left_side) / 2) * c["spacing"]
        new_positions[ref] = {
            "x": round(left_x, 4),
            "y": round(target_y, 4),
        }

    # Place right-side components
    right_x = new_ic_x2 + c["seriesOffset"]
    for i, ref in enumerate(right_side):
        target_y = new_ic_cy + (i - len(right_side) / 2) * c["spacing"]
        new_positions[ref] = {
            "x": round(right_x, 4),
            "y": round(target_y, 4),
        }

    # Place feedback divider on right side, vertically stacked
    if feedback_top or feedback_bottom:
        fb_x = new_ic_x2 + c["seriesOffset"]
        fb_y = new_ic_cy
        if feedback_top:
            new_positions[feedback_top] = {"x": round(fb_x, 4), "y": round(fb_y - c["dividerSpacing"] / 2, 4)}
        if feedback_bottom:
            new_positions[feedback_bottom] = {"x": round(fb_x, 4), "y": round(fb_y + c["dividerSpacing"] / 2, 4)}

    # Place top-side components
    top_y = new_ic_y1 - c["seriesOffset"]
    for i, ref in enumerate(top_side):
        target_x = new_ic_cx + (i - len(top_side) / 2) * c["spacing"]
        new_positions[ref] = {"x": round(target_x, 4), "y": round(top_y, 4)}

    # Place bottom-side (non-cap) components
    bot_y = new_ic_y2 + c["seriesOffset"]
    for i, ref in enumerate(bottom_side):
        target_x = new_ic_cx + (i - len(bottom_side) / 2) * c["spacing"]
        new_positions[ref] = {"x": round(target_x, 4), "y": round(bot_y, 4)}

    # Place test points at group edges
    tp_x = new_ic_x2 + c["testPointOffset"]
    for i, ref in enumerate(test_points):
        new_positions[ref] = {
            "x": round(tp_x, 4),
            "y": round(new_ic_cy + (i - len(test_points) / 2) * c["spacing"], 4),
        }

    # Unplaced components go to the right in a column
    for i, ref in enumerate(unplaced):
        new_positions[ref] = {
            "x": round(new_ic_x2 + c["seriesOffset"] + c["spacing"], 4),
            "y": round(new_ic_y1 + i * c["spacing"], 4),
        }

    # Compute bounding box of the layout
    all_x = [p["x"] for p in new_positions.values()]
    all_y = [p["y"] for p in new_positions.values()]
    bbox = {
        "x1": round(min(all_x) - 5, 2),
        "y1": round(min(all_y) - 5, 2),
        "x2": round(max(all_x) + 5, 2),
        "y2": round(max(all_y) + 5, 2),
    }

    return {
        "success": True,
        "positions": new_positions,
        "roles": roles,
        "primaryIC": primary_ic,
        "boundingBox": bbox,
        "placementSummary": {
            "decouplingCaps": decoupling_caps,
            "leftSide": left_side,
            "rightSide": right_side,
            "topSide": top_side,
            "bottomSide": bottom_side,
            "feedbackTop": feedback_top,
            "feedbackBottom": feedback_bottom,
            "testPoints": test_points,
            "unplaced": unplaced,
        },
        "message": f"Computed positions for {len(new_positions)}/{len(components)} components",
    }


def _find_block_end_str_aware(s, start):
    """Find end of balanced paren block starting at s[start]='('.
    String-aware: skips parens inside quoted strings."""
    depth = 0
    i = start
    in_str = False
    while i < len(s):
        ch = s[i]
        if in_str:
            if ch == '\\':
                i += 2
                continue
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return len(s)


def apply_group_layout(schematic_path, positions, pin_locator, kicad_interface=None, rewire=True, routing_style="auto"):
    """Apply layout positions atomically: move all components + their attached elements in one pass.

    For each component, computes its (dx, dy) offset. Then in a single content pass:
    1. Moves all component (symbol) blocks to new positions
    2. For each wire, checks which group pins its endpoints touch:
       - Both endpoints at group pins → delete (rewire will recreate)
       - One endpoint at a group pin → shift that endpoint by the pin's component delta
       - Neither endpoint at group pins → leave untouched
    3. Shifts labels, power symbols, no-connects at old pin positions by their component's delta
    4. Writes once, then optionally rewires
    """
    import os
    sch_path = Path(schematic_path)
    content = _read_schematic(sch_path)

    # Get current symbol positions
    symbols = parse_placed_symbols_from_content(content)
    current_pos = {}
    for sym in symbols:
        ref = sym.get("reference", "")
        if ref in positions:
            current_pos[ref] = {"x": sym.get("x", 0), "y": sym.get("y", 0)}

    # Compute per-component deltas
    deltas = {}  # ref -> (dx, dy)
    for ref, new_pos in positions.items():
        cur = current_pos.get(ref)
        if not cur:
            continue
        dx = new_pos["x"] - cur["x"]
        dy = new_pos["y"] - cur["y"]
        if abs(dx) > 0.01 or abs(dy) > 0.01:
            deltas[ref] = (dx, dy)

    if not deltas:
        return {"success": True, "movedComponents": [], "movedCount": 0,
                "message": "All components already in position"}

    # Get OLD pin positions for all group components (before move)
    component_set = set(positions.keys())
    all_pins = _compute_pin_endpoints_from_content(
        content, component_set, pin_locator, schematic_path
    )

    # Build: pin_position -> (ref, pin_num, delta)
    # So we can match wire endpoints to components and know which delta to apply
    pin_to_comp = {}  # (round_x, round_y) -> (ref, dx, dy)
    eps = 0.5
    for ref, pn, _name, px, py, _v, _l in all_pins:
        dx, dy = deltas.get(ref, (0, 0))
        key = (round(px, 2), round(py, 2))
        pin_to_comp[key] = (ref, dx, dy)

    def _match_pin(x, y):
        """Find which component's pin is at (x, y), return (ref, dx, dy) or None."""
        for ddx in (-eps, 0, eps):
            for ddy in (-eps, 0, eps):
                key = (round(x + ddx, 2), round(y + ddy, 2))
                if key in pin_to_comp:
                    return pin_to_comp[key]
        return None

    # Skip lib_symbols section
    lib_sym_start = content.find("(lib_symbols")
    lib_sym_end = _find_block_end_str_aware(content, lib_sym_start) if lib_sym_start >= 0 else -1

    replacements = []
    moved_components = []

    # 1. Move component (symbol) blocks
    sym_pat = re.compile(r'\(symbol\s+\(lib_id\s+"([^"]*)"')
    for sm in sym_pat.finditer(content):
        pos = sm.start()
        if lib_sym_start >= 0 and lib_sym_start <= pos < lib_sym_end:
            continue
        end = _find_block_end_str_aware(content, pos)
        block = content[pos:end]

        ref_m = re.search(r'\(property\s+"Reference"\s+"([^"]*)"', block)
        if not ref_m:
            continue
        ref = ref_m.group(1)

        lib_id = sm.group(1)

        # Move group components
        if ref in deltas:
            dx, dy = deltas[ref]
            def _shift_at(match, _dx=dx, _dy=dy):
                ax = float(match.group(1)) + _dx
                ay = float(match.group(2)) + _dy
                rest = match.group(3)
                return f"(at {_fmt(ax)} {_fmt(ay)}{rest}"
            new_block = re.sub(
                r'\(at\s+([\d.e+-]+)\s+([\d.e+-]+)([\s\d.e+-]*\))',
                _shift_at, block
            )
            replacements.append((pos, end, new_block))
            moved_components.append(ref)
        # Move power symbols at group pin positions
        elif lib_id.startswith("power:"):
            at_m = re.search(r'\(at\s+([\d.e+-]+)\s+([\d.e+-]+)', block)
            if at_m:
                sx, sy = float(at_m.group(1)), float(at_m.group(2))
                match = _match_pin(sx, sy)
                if match:
                    _, dx, dy = match
                    if abs(dx) > 0.01 or abs(dy) > 0.01:
                        def _shift_pwr(m, _dx=dx, _dy=dy):
                            ax = float(m.group(1)) + _dx
                            ay = float(m.group(2)) + _dy
                            rest = m.group(3)
                            return f"(at {_fmt(ax)} {_fmt(ay)}{rest}"
                        new_block = re.sub(
                            r'\(at\s+([\d.e+-]+)\s+([\d.e+-]+)([\s\d.e+-]*\))',
                            _shift_pwr, block
                        )
                        replacements.append((pos, end, new_block))

    # 2. Handle wires: shift any endpoint that's at a group pin position.
    # Don't delete wires — just stretch them. rewire_group_orthogonal
    # will clean up the internal (diagonal/stretched) wires afterward.
    # This preserves external connections (to non-group components).
    wire_pat = re.compile(r'\(wire\b')
    xy_pat = re.compile(r'\(xy\s+([\d.e+-]+)\s+([\d.e+-]+)\)')
    for wm in wire_pat.finditer(content):
        wpos = wm.start()
        wend = _find_block_end_str_aware(content, wpos)
        block = content[wpos:wend]
        xys = xy_pat.findall(block)
        if len(xys) < 2:
            continue

        x1, y1 = float(xys[0][0]), float(xys[0][1])
        x2, y2 = float(xys[-1][0]), float(xys[-1][1])
        match_a = _match_pin(x1, y1)
        match_b = _match_pin(x2, y2)

        if not match_a and not match_b:
            continue  # Neither endpoint at a group pin — untouched

        new_block = block
        if match_a:
            _, dx_a, dy_a = match_a
            if abs(dx_a) > 0.01 or abs(dy_a) > 0.01:
                new_block = new_block.replace(
                    f"(xy {xys[0][0]} {xys[0][1]})",
                    f"(xy {_fmt(x1 + dx_a)} {_fmt(y1 + dy_a)})",
                    1
                )
        if match_b:
            _, dx_b, dy_b = match_b
            if abs(dx_b) > 0.01 or abs(dy_b) > 0.01:
                # Replace last occurrence (reverse trick)
                rev = new_block[::-1]
                old_xy = f"(xy {xys[-1][0]} {xys[-1][1]})"
                new_xy = f"(xy {_fmt(x2 + dx_b)} {_fmt(y2 + dy_b)})"
                rev = rev.replace(old_xy[::-1], new_xy[::-1], 1)
                new_block = rev[::-1]

        if new_block != block:
            replacements.append((wpos, wend, new_block))

    # 3. Move labels at old pin positions
    for lt in ["label", "global_label", "hierarchical_label"]:
        lp = re.compile(
            rf'\({lt}\s+"([^"]*)"(?:\s+\(shape\s+[^)]*\))?\s+\(at\s+([\d.e+-]+)\s+([\d.e+-]+)'
        )
        for m in lp.finditer(content):
            lx, ly = float(m.group(2)), float(m.group(3))
            match = _match_pin(lx, ly)
            if match:
                _, dx, dy = match
                if abs(dx) > 0.01 or abs(dy) > 0.01:
                    lpos = m.start()
                    lend = _find_block_end_str_aware(content, lpos)
                    block = content[lpos:lend]
                    def _shift_label(m2, _dx=dx, _dy=dy):
                        ax = float(m2.group(1)) + _dx
                        ay = float(m2.group(2)) + _dy
                        rest = m2.group(3)
                        return f"(at {_fmt(ax)} {_fmt(ay)}{rest}"
                    new_block = re.sub(
                        r'\(at\s+([\d.e+-]+)\s+([\d.e+-]+)([\s\d.e+-]*\))',
                        _shift_label, block
                    )
                    replacements.append((lpos, lend, new_block))

    # 4. Move junctions and no-connects at old pin positions
    for elem_type in ["junction", "no_connect"]:
        ep = re.compile(rf'\({elem_type}\s+\(at\s+([\d.e+-]+)\s+([\d.e+-]+)\)')
        for m in ep.finditer(content):
            ex, ey = float(m.group(1)), float(m.group(2))
            match = _match_pin(ex, ey)
            if match:
                _, dx, dy = match
                if abs(dx) > 0.01 or abs(dy) > 0.01:
                    epos = m.start()
                    eend = _find_block_end_str_aware(content, epos)
                    block = content[epos:eend]
                    new_block = re.sub(
                        r'\(at\s+([\d.e+-]+)\s+([\d.e+-]+)\)',
                        lambda m2: f"(at {_fmt(float(m2.group(1)) + dx)} {_fmt(float(m2.group(2)) + dy)})",
                        block
                    )
                    replacements.append((epos, eend, new_block))

    # Apply all replacements in reverse position order (single pass)
    replacements.sort(key=lambda r: r[0], reverse=True)
    for start, end, new_text in replacements:
        content = content[:start] + new_text + content[end:]

    _write_schematic(sch_path, content)

    result = {
        "success": True,
        "movedComponents": moved_components,
        "movedCount": len(moved_components),
        "message": f"Moved {len(moved_components)} components atomically",
    }

    # Optionally rewire
    if rewire and moved_components:
        all_refs = list(positions.keys())
        rewire_result = rewire_group_orthogonal(
            schematic_path, all_refs, pin_locator, routing_style
        )
        result["rewireResult"] = rewire_result

    return result


# ── Tool 3: rewire_group_orthogonal ─────────────────────────────────


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


def _route_crosses_any_bbox(segments, bboxes, margin=0.5):
    """Check if any segment in a route crosses any component bbox."""
    for sx1, sy1, sx2, sy2 in segments:
        for ref, bb in bboxes.items():
            if _segment_crosses_bbox(sx1, sy1, sx2, sy2, *bb, margin=margin):
                return True
    return False


def _compute_avoiding_route(x1, y1, x2, y2, comp_bboxes, routing_style="auto"):
    """Compute an orthogonal route that avoids component bodies.

    Tries in order:
    1. L-shape horizontal-first: (x1,y1)→(x2,y1)→(x2,y2)
    2. L-shape vertical-first: (x1,y1)→(x1,y2)→(x2,y2)
    3. U-detour: route around the blocking component(s)

    Returns list of (x1, y1, x2, y2) wire segments.
    """
    # Build the two L-route options
    h_first = [(x1, y1, x2, y1), (x2, y1, x2, y2)]  # horizontal then vertical
    v_first = [(x1, y1, x1, y2), (x1, y2, x2, y2)]  # vertical then horizontal

    h_crosses = _route_crosses_any_bbox(h_first, comp_bboxes)
    v_crosses = _route_crosses_any_bbox(v_first, comp_bboxes)

    if routing_style == "horizontal_first":
        if not h_crosses:
            return h_first
        if not v_crosses:
            return v_first
    elif routing_style == "vertical_first":
        if not v_crosses:
            return v_first
        if not h_crosses:
            return h_first
    else:  # auto
        if not h_crosses:
            return h_first
        if not v_crosses:
            return v_first

    # Both L-routes cross component bodies — compute a U-shaped detour.
    # Find the combined bounding box of all blocking components, then route
    # around the outside.
    blocking_bbs = []
    for ref, bb in comp_bboxes.items():
        # Check if this bbox blocks either L-route
        for seg in h_first + v_first:
            if _segment_crosses_bbox(seg[0], seg[1], seg[2], seg[3], *bb):
                blocking_bbs.append(bb)
                break

    if not blocking_bbs:
        # Shouldn't happen, but fall back to h_first
        return h_first

    # Combined blocking region
    block_x1 = min(bb[0] for bb in blocking_bbs)
    block_y1 = min(bb[1] for bb in blocking_bbs)
    block_x2 = max(bb[2] for bb in blocking_bbs)
    block_y2 = max(bb[3] for bb in blocking_bbs)

    # Clearance for the detour
    clearance = 2.54

    # Try 4 detour options: go around top, bottom, left, or right
    detour_options = []

    # Detour above: go up past block_y1, across, then down
    wy = block_y1 - clearance
    above = [(x1, y1, x1, wy), (x1, wy, x2, wy), (x2, wy, x2, y2)]
    if not _route_crosses_any_bbox(above, comp_bboxes, margin=0.3):
        detour_options.append(above)

    # Detour below: go down past block_y2, across, then up
    wy = block_y2 + clearance
    below = [(x1, y1, x1, wy), (x1, wy, x2, wy), (x2, wy, x2, y2)]
    if not _route_crosses_any_bbox(below, comp_bboxes, margin=0.3):
        detour_options.append(below)

    # Detour left: go left past block_x1, down, then right
    wx = block_x1 - clearance
    left = [(x1, y1, wx, y1), (wx, y1, wx, y2), (wx, y2, x2, y2)]
    if not _route_crosses_any_bbox(left, comp_bboxes, margin=0.3):
        detour_options.append(left)

    # Detour right: go right past block_x2, down, then left
    wx = block_x2 + clearance
    right = [(x1, y1, wx, y1), (wx, y1, wx, y2), (wx, y2, x2, y2)]
    if not _route_crosses_any_bbox(right, comp_bboxes, margin=0.3):
        detour_options.append(right)

    if detour_options:
        # Pick the shortest detour (by total wire length)
        def _route_length(segs):
            return sum(abs(s[2] - s[0]) + abs(s[3] - s[1]) for s in segs)
        detour_options.sort(key=_route_length)
        return detour_options[0]

    # All detours also cross — give up and use horizontal-first L-route.
    # The validation step will flag the crossings.
    logger.warning(f"Could not find crossing-free route from ({x1},{y1}) to ({x2},{y2})")
    return h_first


def rewire_group_orthogonal(schematic_path, components, pin_locator, routing_style="auto", include_labeled=False):
    """Delete wires between group components and redraw as orthogonal routes.

    Finds all wires that form chains between group pins (including through
    intermediate junctions). By default preserves label-connected wires;
    set include_labeled=True to also rewire intra-group label-mediated connections.
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

    # Step 2: Find wires that connect group pins (directly or through junctions).
    # Use union-find to chain wire segments, then check if both terminal
    # endpoints of each chain are at group pins.
    wire_blocks = _parse_wire_blocks(content)

    # Also parse labels — wire endpoints at label positions should NOT be
    # considered as internal chain points (labels carry connectivity externally)
    all_labels = _parse_labels(content)
    label_positions = set()
    for lx, ly, _name, _lt in all_labels:
        label_positions.add((round(lx, 2), round(ly, 2)))

    # Build union-find over wire endpoint connectivity
    wire_uf = _UnionFind()
    endpoint_to_wires = {}  # snapped endpoint -> list of wire indices

    for i, (x1, y1, x2, y2, _bs, _be) in enumerate(wire_blocks):
        ep1 = (round(x1, 2), round(y1, 2))
        ep2 = (round(x2, 2), round(y2, 2))
        wire_uf.union(ep1, ep2)
        endpoint_to_wires.setdefault(ep1, []).append(i)
        endpoint_to_wires.setdefault(ep2, []).append(i)

    # Group wires into chains by their union-find root
    root_to_wires = {}  # uf root -> set of wire indices
    root_to_endpoints = {}  # uf root -> set of all endpoints in the chain
    for i, (x1, y1, x2, y2, _bs, _be) in enumerate(wire_blocks):
        ep1 = (round(x1, 2), round(y1, 2))
        root = wire_uf.find(ep1)
        root_to_wires.setdefault(root, set()).add(i)
        root_to_endpoints.setdefault(root, set()).add(ep1)
        root_to_endpoints.setdefault(root, set()).add((round(x2, 2), round(y2, 2)))

    # For each chain, find terminal endpoints (endpoints that appear in only one wire)
    # A chain connecting two pins: pin_A → wire → junction → wire → pin_B
    # Terminal endpoints are pin_A and pin_B
    wires_to_delete = []
    connected_pairs = set()

    for root, wire_indices in root_to_wires.items():
        all_eps = root_to_endpoints[root]

        # Count how many wires touch each endpoint
        ep_wire_count = {}
        for wi in wire_indices:
            x1, y1, x2, y2, _bs, _be = wire_blocks[wi]
            ep1 = (round(x1, 2), round(y1, 2))
            ep2 = (round(x2, 2), round(y2, 2))
            ep_wire_count[ep1] = ep_wire_count.get(ep1, 0) + 1
            ep_wire_count[ep2] = ep_wire_count.get(ep2, 0) + 1

        # Terminal endpoints: appear in exactly 1 wire in this chain
        # OR are at a pin position (pins are always terminals)
        terminals = set()
        for ep, count in ep_wire_count.items():
            if count == 1:
                terminals.add(ep)
            if _point_in_index(ep[0], ep[1], pin_index):
                terminals.add(ep)

        # Check if any endpoint touches a label
        touches_label = any(
            (round(ep[0], 2), round(ep[1], 2)) in label_positions
            or any(abs(ep[0] - lp[0]) < 0.1 and abs(ep[1] - lp[1]) < 0.1 for lp in label_positions)
            for ep in all_eps
        )
        if touches_label and not include_labeled:
            # Skip label-connected wires unless explicitly included
            continue

        # Find which terminals are at group pins
        pin_terminals = []
        for ep in terminals:
            match = _point_in_index(ep[0], ep[1], pin_index)
            if match:
                pin_terminals.append((ep, match))

        # If 2+ terminals are at group pins, this chain connects group components
        if len(pin_terminals) >= 2:
            # Delete all wires in this chain
            for wi in wire_indices:
                _, _, _, _, bstart, bend = wire_blocks[wi]
                wires_to_delete.append((bstart, bend))

            # Record connected pin pairs (all combinations for multi-terminal chains)
            for i_pt in range(len(pin_terminals)):
                for j_pt in range(i_pt + 1, len(pin_terminals)):
                    pair = frozenset([pin_terminals[i_pt][1], pin_terminals[j_pt][1]])
                    connected_pairs.add(pair)

    if not wires_to_delete:
        return {
            "success": True,
            "message": "No internal wires found between group components (wires may connect through labels instead)",
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

        # Use exact pin coordinates — do NOT snap to grid.
        # Components may be at off-grid positions from prior moves.
        x1, y1 = round(pos_a[0], 4), round(pos_a[1], 4)
        x2, y2 = round(pos_b[0], 4), round(pos_b[1], 4)

        if abs(x1 - x2) < 0.01 or abs(y1 - y2) < 0.01:
            # Colinear — single straight wire
            content = add_wire_to_content(content, [x1, y1], [x2, y2])
            new_endpoints.extend([(x1, y1), (x2, y2)])
            added_count += 1
        else:
            # Compute route with avoidance: try L-shapes first, fall back to U-detour
            route_segments = _compute_avoiding_route(
                x1, y1, x2, y2, comp_bboxes, routing_style
            )
            for seg in route_segments:
                content = add_wire_to_content(content, [seg[0], seg[1]], [seg[2], seg[3]])
                new_endpoints.extend([(seg[0], seg[1]), (seg[2], seg[3])])
            added_count += len(route_segments)

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
