"""
S-Expression Writer for KiCad Schematics

Provides text-based insertion into .kicad_sch files, preserving KiCad's native formatting.
Replaces sexpdata round-trip (loads → modify → dumps) which collapses all formatting
into a single line.

All functions insert properly-indented S-expression text at the correct location
in the file, without parsing/re-serializing the entire document.
"""

import os
import uuid
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("kicad_interface")


def _fmt(v: float) -> str:
    """Format a coordinate value consistently: strip trailing zeros but keep
    at least one decimal place. Matches KiCad's native output (e.g. 82 not 82.0,
    148.604 not 148.60400)."""
    if isinstance(v, int):
        return str(v)
    # Format with enough precision, strip trailing zeros
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s


def _find_insert_position(content: str) -> int:
    """Find the insertion point before (sheet_instances in the schematic file.

    Returns the character index where new elements should be inserted.
    """
    marker = "(sheet_instances"
    pos = content.rfind(marker)
    if pos == -1:
        raise ValueError("Could not find (sheet_instances in schematic file")
    return pos


def _read_schematic(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_schematic(path: Path, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def add_wire_to_content(
    content: str,
    start_point: List[float],
    end_point: List[float],
    stroke_width: float = 0,
    stroke_type: str = "default",
) -> str:
    """Add a wire to schematic content string. Returns modified content."""
    wire_uuid = str(uuid.uuid4())
    wire_text = (
        f"  (wire (pts (xy {_fmt(start_point[0])} {_fmt(start_point[1])}) "
        f"(xy {_fmt(end_point[0])} {_fmt(end_point[1])}))\n"
        f"    (stroke (width {stroke_width}) (type {stroke_type}))\n"
        f"    (uuid {wire_uuid})\n"
        f"  )\n\n"
    )
    insert_at = _find_insert_position(content)
    return content[:insert_at] + wire_text + content[insert_at:]


def add_wire(
    schematic_path: Path,
    start_point: List[float],
    end_point: List[float],
    stroke_width: float = 0,
    stroke_type: str = "default",
) -> bool:
    """Add a wire to the schematic using text insertion."""
    try:
        content = _read_schematic(schematic_path)
        content = add_wire_to_content(content, start_point, end_point, stroke_width, stroke_type)
        _write_schematic(schematic_path, content)
        logger.info(f"Added wire from {start_point} to {end_point}")
        return True
    except Exception as e:
        logger.error(f"Error adding wire: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def add_polyline_wire(
    schematic_path: Path,
    points: List[List[float]],
    stroke_width: float = 0,
    stroke_type: str = "default",
) -> bool:
    """Add a multi-segment wire (polyline) to the schematic."""
    try:
        if len(points) < 2:
            logger.error("Polyline requires at least 2 points")
            return False

        content = _read_schematic(schematic_path)
        wire_uuid = str(uuid.uuid4())

        pts_parts = " ".join(f"(xy {_fmt(p[0])} {_fmt(p[1])})" for p in points)
        wire_text = (
            f"  (wire (pts {pts_parts})\n"
            f"    (stroke (width {stroke_width}) (type {stroke_type}))\n"
            f"    (uuid {wire_uuid})\n"
            f"  )\n\n"
        )

        insert_at = _find_insert_position(content)
        content = content[:insert_at] + wire_text + content[insert_at:]
        _write_schematic(schematic_path, content)

        logger.info(f"Added polyline wire with {len(points)} points")
        return True
    except Exception as e:
        logger.error(f"Error adding polyline wire: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def add_label(
    schematic_path: Path,
    text: str,
    position: List[float],
    label_type: str = "label",
    orientation: int = 0,
    shape: Optional[str] = None,
) -> bool:
    """Add a net label to the schematic.

    Args:
        label_type: 'label', 'global_label', or 'hierarchical_label'
        shape: For global_label: 'input', 'output', 'bidirectional', 'passive', 'tri_state'
    """
    try:
        content = _read_schematic(schematic_path)
        label_uuid = str(uuid.uuid4())

        # Build the label S-expression
        shape_attr = ""
        if label_type == "global_label" and shape:
            shape_attr = f" (shape {shape})"

        # Justify depends on angle: 0°/90° → left, 180°/270° → right.
        # Local labels additionally use "bottom".
        norm_angle = int(orientation) % 360
        justify_dir = "right" if norm_angle in (180, 270) else "left"
        if label_type == "label":
            justify = f"(justify {justify_dir} bottom)"
        else:
            justify = f"(justify {justify_dir})"

        # Global/hierarchical labels need an Intersheetrefs property
        isr_block = ""
        if label_type in ("global_label", "hierarchical_label"):
            isr_uuid = str(uuid.uuid4())
            # Intersheetrefs position: for justify left it's at the label position,
            # for justify right it's offset by the flag width
            char_w = 0.75
            text_len = len(text) * char_w
            body = 3.0
            total_w = body + text_len
            isr_x, isr_y = position[0], position[1]
            if norm_angle == 180:
                isr_x = round(position[0] - total_w, 4)
            elif norm_angle == 270:
                isr_y = round(position[1] - total_w, 4)
            isr_block = (
                f'    (property "Intersheetrefs" "${{INTERSHEET_REFS}}"\n'
                f"      (at {_fmt(isr_x)} {_fmt(isr_y)} {orientation})\n"
                f"      (effects (font (size 1.27 1.27)) (justify {justify_dir}) (hide yes))\n"
                f"      (uuid {isr_uuid})\n"
                f"    )\n"
            )

        label_text = (
            f'  ({label_type} "{text}"{shape_attr} (at {_fmt(position[0])} {_fmt(position[1])} {orientation})\n'
            f"    (fields_autoplaced yes)\n"
            f"    (effects (font (size 1.27 1.27)) {justify})\n"
            f"    (uuid {label_uuid})\n"
            f"{isr_block}"
            f"  )\n\n"
        )

        insert_at = _find_insert_position(content)
        content = content[:insert_at] + label_text + content[insert_at:]
        _write_schematic(schematic_path, content)

        logger.info(f"Added {label_type} '{text}' at {position}")
        return True
    except Exception as e:
        logger.error(f"Error adding label: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def add_junction_to_content(
    content: str,
    position: List[float],
    diameter: float = 0,
) -> str:
    """Add a junction to schematic content string. Returns modified content."""
    junction_uuid = str(uuid.uuid4())
    junction_text = (
        f"  (junction (at {_fmt(position[0])} {_fmt(position[1])}) (diameter {diameter})\n"
        f"    (color 0 0 0 0)\n"
        f"    (uuid {junction_uuid})\n"
        f"  )\n\n"
    )
    insert_at = _find_insert_position(content)
    return content[:insert_at] + junction_text + content[insert_at:]


def add_junction(
    schematic_path: Path,
    position: List[float],
    diameter: float = 0,
) -> bool:
    """Add a junction (connection dot) to the schematic."""
    try:
        content = _read_schematic(schematic_path)
        content = add_junction_to_content(content, position, diameter)
        _write_schematic(schematic_path, content)
        logger.info(f"Added junction at {position}")
        return True
    except Exception as e:
        logger.error(f"Error adding junction: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def add_no_connect_to_content(content: str, position: List[float]) -> str:
    """Add a no-connect flag to schematic content string. Returns modified content."""
    nc_uuid = str(uuid.uuid4())
    nc_text = (
        f"  (no_connect (at {_fmt(position[0])} {_fmt(position[1])})\n"
        f"    (uuid {nc_uuid})\n"
        f"  )\n\n"
    )
    insert_at = _find_insert_position(content)
    return content[:insert_at] + nc_text + content[insert_at:]


def add_no_connect(schematic_path: Path, position: List[float]) -> bool:
    """Add a no-connect flag to the schematic."""
    try:
        content = _read_schematic(schematic_path)
        content = add_no_connect_to_content(content, position)
        _write_schematic(schematic_path, content)
        logger.info(f"Added no-connect at {position}")
        return True
    except Exception as e:
        logger.error(f"Error adding no-connect: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def delete_wire_from_content(
    content: str,
    start_point: List[float],
    end_point: List[float],
    tolerance: float = 0.5,
) -> Optional[str]:
    """Delete a wire from schematic content string. Returns modified content, or None if not found."""
    import re
    wire_pattern = re.compile(r'\(wire\b')

    for m in wire_pattern.finditer(content):
        block_start = m.start()
        depth = 0
        i = block_start
        while i < len(content):
            if content[i] == '(':
                depth += 1
            elif content[i] == ')':
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break
            i += 1
        else:
            continue

        block = content[block_start:block_end]
        xy_matches = re.findall(r'\(xy\s+([\d.e+-]+)\s+([\d.e+-]+)\)', block)
        if len(xy_matches) < 2:
            continue

        x1, y1 = float(xy_matches[0][0]), float(xy_matches[0][1])
        x2, y2 = float(xy_matches[-1][0]), float(xy_matches[-1][1])
        sx, sy = start_point
        ex, ey = end_point

        match_fwd = (
            abs(x1 - sx) < tolerance and abs(y1 - sy) < tolerance
            and abs(x2 - ex) < tolerance and abs(y2 - ey) < tolerance
        )
        match_rev = (
            abs(x1 - ex) < tolerance and abs(y1 - ey) < tolerance
            and abs(x2 - sx) < tolerance and abs(y2 - sy) < tolerance
        )

        if match_fwd or match_rev:
            end_with_nl = block_end
            while end_with_nl < len(content) and content[end_with_nl] in '\n':
                end_with_nl += 1
            return content[:block_start] + content[end_with_nl:]

    return None


def delete_wire(
    schematic_path: Path,
    start_point: List[float],
    end_point: List[float],
    tolerance: float = 0.5,
) -> bool:
    """Delete a wire matching given start/end coordinates using text parsing."""
    try:
        content = _read_schematic(schematic_path)
        result = delete_wire_from_content(content, start_point, end_point, tolerance)
        if result is not None:
            _write_schematic(schematic_path, result)
            logger.info(f"Deleted wire from {start_point} to {end_point}")
            return True
        logger.warning(f"No matching wire found for {start_point} to {end_point}")
        return False
    except Exception as e:
        logger.error(f"Error deleting wire: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def delete_label_from_content(
    content: str,
    net_name: str,
    position: Optional[List[float]] = None,
    tolerance: float = 0.5,
) -> Optional[str]:
    """Delete a label from schematic content string. Returns modified content, or None if not found."""
    import re
    escaped_name = re.escape(net_name)
    label_pattern = re.compile(
        rf'\((?:label|global_label|hierarchical_label)\s+"{escaped_name}"\s'
    )

    for m in label_pattern.finditer(content):
        block_start = m.start()
        depth = 0
        i = block_start
        while i < len(content):
            if content[i] == '(':
                depth += 1
            elif content[i] == ')':
                depth -= 1
                if depth == 0:
                    block_end = i + 1
                    break
            i += 1
        else:
            continue

        block = content[block_start:block_end]

        if position is not None:
            at_match = re.search(r'\(at\s+([\d.e+-]+)\s+([\d.e+-]+)', block)
            if at_match:
                lx, ly = float(at_match.group(1)), float(at_match.group(2))
                if abs(lx - position[0]) >= tolerance or abs(ly - position[1]) >= tolerance:
                    continue

        end_with_nl = block_end
        while end_with_nl < len(content) and content[end_with_nl] in '\n':
            end_with_nl += 1
        return content[:block_start] + content[end_with_nl:]

    return None


def delete_label(
    schematic_path: Path,
    net_name: str,
    position: Optional[List[float]] = None,
    tolerance: float = 0.5,
) -> bool:
    """Delete a net label by name (and optionally position) using text parsing."""
    try:
        content = _read_schematic(schematic_path)
        result = delete_label_from_content(content, net_name, position, tolerance)
        if result is not None:
            _write_schematic(schematic_path, result)
            logger.info(f"Deleted label '{net_name}'")
            return True
        logger.warning(f"No matching label found for '{net_name}'")
        return False
    except Exception as e:
        logger.error(f"Error deleting label: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def create_orthogonal_path(
    start: List[float], end: List[float], prefer_horizontal_first: bool = True
) -> List[List[float]]:
    """Create an orthogonal (right-angle) path between two points."""
    x1, y1 = start
    x2, y2 = end

    if x1 == x2 or y1 == y2:
        return [start, end]

    if prefer_horizontal_first:
        return [start, [x2, y1], end]
    else:
        return [start, [x1, y2], end]


def add_instances_block(
    schematic_path: Path,
    symbol_uuid: str,
    reference: str,
    unit: int = 1,
) -> bool:
    """Add an (instances) block to an existing symbol in the schematic.

    KiCad 9 requires (instances (project "name" (path "/root-uuid" (reference "R1") (unit 1))))
    for annotation to work.
    """
    try:
        content = _read_schematic(schematic_path)

        # Get project name from .kicad_pro file
        project_name = _get_project_name(schematic_path)

        # Get root sheet UUID
        root_uuid = _get_root_sheet_uuid(content)

        # Find the symbol block by UUID and inject instances before closing paren
        import re
        uuid_pattern = re.compile(
            rf'\(uuid\s+"{re.escape(symbol_uuid)}"\s*\)'
        )
        m = uuid_pattern.search(content)
        if not m:
            # Try without quotes (some UUIDs are unquoted)
            uuid_pattern = re.compile(
                rf'\(uuid\s+{re.escape(symbol_uuid)}\s*\)'
            )
            m = uuid_pattern.search(content)
        if not m:
            logger.error(f"Could not find symbol with UUID {symbol_uuid}")
            return False

        # From the UUID position, find the closing paren of the symbol block
        # Walk backwards to find the opening (symbol, then find matching close
        # Actually, easier: from UUID pos, scan forward to next unmatched )
        uuid_end = m.end()

        # Find the closing ) of the symbol block
        # Count remaining depth from the uuid position
        depth = 0
        i = uuid_end
        while i < len(content):
            if content[i] == '(':
                depth += 1
            elif content[i] == ')':
                if depth == 0:
                    # This is the closing paren of the symbol
                    break
                depth -= 1
            i += 1

        if i >= len(content):
            logger.error(f"Could not find end of symbol block for UUID {symbol_uuid}")
            return False

        # Insert instances block before the closing paren
        instances_text = (
            f'\n    (instances (project "{project_name}"\n'
            f'      (path "/{root_uuid}" (reference "{reference}") (unit {unit}))\n'
            f"    ))"
        )

        content = content[:i] + instances_text + "\n" + content[i:]
        _write_schematic(schematic_path, content)

        logger.info(f"Added instances block for {reference} (UUID: {symbol_uuid})")
        return True
    except Exception as e:
        logger.error(f"Error adding instances block: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def _get_project_name(schematic_path: Path) -> str:
    """Get project name from the .kicad_pro file in the same directory."""
    import glob
    parent = schematic_path.parent
    pro_files = list(parent.glob("*.kicad_pro"))
    if pro_files:
        return pro_files[0].stem
    # Fallback: use schematic filename without extension
    return schematic_path.stem


def _get_root_sheet_uuid(content: str) -> str:
    """Extract the root sheet UUID from the schematic content."""
    import re
    # The first (uuid ...) in the file is the schematic's root UUID
    m = re.search(r'\(uuid\s+"?([0-9a-fA-F-]+)"?\s*\)', content)
    if m:
        return m.group(1)
    return "00000000-0000-0000-0000-000000000000"
