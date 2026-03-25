# Five New MCP Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 5 missing MCP tools that users hit during real PCB design sessions: delete_board_outline, replace_board_outline, swap_schematic_symbol, get_footprint_bounds, auto_assign_footprints.

**Architecture:** Board outline tools add methods to the existing `BoardOutlineCommands` class and delegate via `BoardCommands`. Schematic tools add handlers directly to `KiCADInterface` (following the existing pattern for schematic handlers). `get_footprint_bounds` parses `.kicad_mod` files via text/regex — no pcbnew needed. Each tool follows the 3-layer pattern: TypeScript registration → command_routes entry → Python handler.

**Tech Stack:** TypeScript (Zod schemas, MCP SDK), Python (pcbnew SWIG for board tools, text manipulation for schematic tools, regex for footprint parsing).

---

## File Structure

**Create:**
- (none — all additions go into existing files)

**Modify:**
- `python/commands/board/outline.py` — add `delete_board_outline()`, `replace_board_outline()` methods
- `python/commands/board/__init__.py` — add delegation methods for new outline tools
- `python/commands/library.py` — add `get_footprint_bounds()` method to `LibraryCommands`
- `python/kicad_interface.py` — add `_handle_swap_schematic_symbol()`, `_handle_auto_assign_footprints()` handlers + 5 new command_routes entries
- `src/tools/board.ts` — register `delete_board_outline` and `replace_board_outline` tools
- `src/tools/schematic.ts` — register `swap_schematic_symbol` and `auto_assign_footprints` tools
- `src/tools/component.ts` — register `get_footprint_bounds` tool

---

### Task 1: delete_board_outline (Python handler)

**Files:**
- Modify: `python/commands/board/outline.py` (add method after `add_board_outline`, ~line 202)
- Modify: `python/commands/board/__init__.py` (add delegation method, ~line 66)
- Modify: `python/kicad_interface.py` (add command_routes entry, ~line 374)

- [ ] **Step 1: Add `delete_board_outline` to `BoardOutlineCommands`**

In `python/commands/board/outline.py`, add after the `add_board_outline` method (after line 202):

```python
def delete_board_outline(self, params: Dict[str, Any]) -> Dict[str, Any]:
    """Remove all shapes from the Edge.Cuts layer (board outline)"""
    try:
        if not self.board:
            return {
                "success": False,
                "message": "No board is loaded",
                "errorDetails": "Load or create a board first",
            }

        edge_layer = self.board.GetLayerID("Edge.Cuts")
        shapes_to_remove = []

        for drawing in self.board.GetDrawings():
            if hasattr(drawing, "GetLayer") and drawing.GetLayer() == edge_layer:
                shapes_to_remove.append(drawing)

        if not shapes_to_remove:
            return {
                "success": True,
                "message": "No board outline found to delete",
                "deleted_count": 0,
            }

        for shape in shapes_to_remove:
            self.board.Remove(shape)

        return {
            "success": True,
            "message": f"Deleted board outline ({len(shapes_to_remove)} shapes removed)",
            "deleted_count": len(shapes_to_remove),
        }

    except Exception as e:
        logger.error(f"Error deleting board outline: {str(e)}")
        return {
            "success": False,
            "message": "Failed to delete board outline",
            "errorDetails": str(e),
        }
```

- [ ] **Step 2: Add delegation method to `BoardCommands`**

In `python/commands/board/__init__.py`, add after `add_board_outline` delegation (~line 56):

```python
def delete_board_outline(self, params: Dict[str, Any]) -> Dict[str, Any]:
    """Remove all Edge.Cuts shapes (board outline)"""
    self.outline_commands.board = self.board
    return self.outline_commands.delete_board_outline(params)
```

- [ ] **Step 3: Add command_routes entry**

In `python/kicad_interface.py`, add after the `"add_board_outline"` entry (~line 374):

```python
"delete_board_outline": self.board_commands.delete_board_outline,
```

- [ ] **Step 4: Commit**

```bash
git add python/commands/board/outline.py python/commands/board/__init__.py python/kicad_interface.py
git commit -m "Add delete_board_outline Python handler"
```

---

### Task 2: delete_board_outline (TypeScript registration)

**Files:**
- Modify: `src/tools/board.ts` (add tool registration after add_board_outline, ~line 185)

- [ ] **Step 1: Register the tool**

In `src/tools/board.ts`, add after the `add_board_outline` tool registration (after line 185):

```typescript
// ------------------------------------------------------
// Delete Board Outline Tool
// ------------------------------------------------------
server.tool(
  "delete_board_outline",
  "Remove the existing board outline (all shapes on Edge.Cuts layer). Use before add_board_outline to replace an outline.",
  {},
  async () => {
    logger.debug("Deleting board outline");
    const result = await callKicadScript("delete_board_outline", {});
    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify(result)
      }]
    };
  }
);
```

- [ ] **Step 2: Build TypeScript**

Run: `npm run build`
Expected: Clean compilation, no errors.

- [ ] **Step 3: Commit**

```bash
git add src/tools/board.ts
git commit -m "Register delete_board_outline MCP tool"
```

---

### Task 3: replace_board_outline (Python + TypeScript)

**Files:**
- Modify: `python/commands/board/outline.py` (add method after `delete_board_outline`)
- Modify: `python/commands/board/__init__.py` (add delegation method)
- Modify: `python/kicad_interface.py` (add command_routes entry)
- Modify: `src/tools/board.ts` (add tool registration)

- [ ] **Step 1: Add `replace_board_outline` to `BoardOutlineCommands`**

In `python/commands/board/outline.py`, add after `delete_board_outline`:

```python
def replace_board_outline(self, params: Dict[str, Any]) -> Dict[str, Any]:
    """Delete existing board outline and create a new one atomically"""
    try:
        if not self.board:
            return {
                "success": False,
                "message": "No board is loaded",
                "errorDetails": "Load or create a board first",
            }

        # Step 1: Delete existing outline
        delete_result = self.delete_board_outline({})
        deleted_count = delete_result.get("deleted_count", 0)

        # Step 2: Create new outline (reuse add_board_outline)
        add_result = self.add_board_outline(params)

        if not add_result.get("success"):
            return add_result

        add_result["message"] = (
            f"Replaced board outline (removed {deleted_count} old shapes, "
            f"created new {params.get('shape', 'rectangle')})"
        )
        add_result["deleted_count"] = deleted_count
        return add_result

    except Exception as e:
        logger.error(f"Error replacing board outline: {str(e)}")
        return {
            "success": False,
            "message": "Failed to replace board outline",
            "errorDetails": str(e),
        }
```

- [ ] **Step 2: Add delegation method to `BoardCommands`**

In `python/commands/board/__init__.py`, add after `delete_board_outline` delegation:

```python
def replace_board_outline(self, params: Dict[str, Any]) -> Dict[str, Any]:
    """Delete existing outline and create a new one"""
    self.outline_commands.board = self.board
    return self.outline_commands.replace_board_outline(params)
```

- [ ] **Step 3: Add command_routes entry**

In `python/kicad_interface.py`, add after `"delete_board_outline"`:

```python
"replace_board_outline": self.board_commands.replace_board_outline,
```

- [ ] **Step 4: Register TypeScript tool**

In `src/tools/board.ts`, add after `delete_board_outline` registration:

```typescript
// ------------------------------------------------------
// Replace Board Outline Tool
// ------------------------------------------------------
server.tool(
  "replace_board_outline",
  "Atomically replace the board outline: deletes all existing Edge.Cuts shapes then creates a new outline. Same parameters as add_board_outline.",
  {
    shape: z.enum(["rectangle", "circle", "polygon", "rounded_rectangle"]).describe("Shape of the new outline"),
    params: z.object({
      width: z.number().optional().describe("Width of rectangle (mm)"),
      height: z.number().optional().describe("Height of rectangle (mm)"),
      cornerRadius: z.number().optional().describe("Corner radius for rounded_rectangle (mm)"),
      radius: z.number().optional().describe("Radius of circle (mm)"),
      points: z.array(
        z.object({
          x: z.number().describe("X coordinate"),
          y: z.number().describe("Y coordinate")
        })
      ).optional().describe("Points of polygon"),
      x: z.number().optional().describe("X coordinate of top-left corner (default: 0)"),
      y: z.number().optional().describe("Y coordinate of top-left corner (default: 0)"),
      unit: z.enum(["mm", "inch"]).default("mm").describe("Unit of measurement")
    }).describe("Parameters for the new outline shape")
  },
  async ({ shape, params }) => {
    logger.debug(`Replacing board outline with ${shape}`);
    const result = await callKicadScript("replace_board_outline", {
      shape,
      ...params
    });
    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify(result)
      }]
    };
  }
);
```

- [ ] **Step 5: Build TypeScript**

Run: `npm run build`
Expected: Clean compilation.

- [ ] **Step 6: Commit**

```bash
git add python/commands/board/outline.py python/commands/board/__init__.py python/kicad_interface.py src/tools/board.ts
git commit -m "Add replace_board_outline: atomic delete + create outline"
```

---

### Task 4: get_footprint_bounds (Python handler)

**Files:**
- Modify: `python/commands/library.py` (add method to `LibraryCommands` after `get_footprint_info`, ~line 531)
- Modify: `python/kicad_interface.py` (add command_routes entry)

- [ ] **Step 1: Add `get_footprint_bounds` to `LibraryCommands`**

In `python/commands/library.py`, add after `get_footprint_info` method:

```python
def get_footprint_bounds(self, params: Dict) -> Dict:
    """Get bounding box dimensions of a footprint from its .kicad_mod file.

    Returns courtyard, fab layer, and pad extents without needing pcbnew.
    """
    import re

    try:
        footprint_spec = params.get("footprint")
        if not footprint_spec:
            return {"success": False, "message": "Missing footprint parameter"}

        result = self.library_manager.find_footprint(footprint_spec)
        if not result:
            return {
                "success": False,
                "message": f"Footprint not found: {footprint_spec}",
            }

        library_path, footprint_name = result
        fp_file = Path(library_path) / f"{footprint_name}.kicad_mod"
        if not fp_file.exists():
            return {
                "success": False,
                "message": f"Footprint file not found: {fp_file}",
            }

        with open(fp_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse geometry by layer
        bounds = {}

        # Collect points from lines, rects, circles, arcs, and pads
        # Pattern: (fp_line (start X Y) (end X Y) ... (layer "LAYER"))
        # Pattern: (fp_rect (start X Y) (end X Y) ... (layer "LAYER"))
        # Pattern: (pad ... (at X Y) ... (size W H) ... (layers ...))

        # Extract all coordinate pairs with their layer context
        layer_points = {}  # layer_name -> list of (x, y)

        # Lines and rects: (start X Y) (end X Y) ... (layer "LAYER")
        for m in re.finditer(
            r'\(fp_(?:line|rect)\s+\(start\s+([\d.e+-]+)\s+([\d.e+-]+)\)\s+'
            r'\(end\s+([\d.e+-]+)\s+([\d.e+-]+)\).*?\(layer\s+"([^"]+)"\)',
            content, re.DOTALL
        ):
            x1, y1, x2, y2, layer = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)), m.group(5)
            layer_points.setdefault(layer, []).extend([(x1, y1), (x2, y2)])

        # Circles: (fp_circle (center X Y) (end X Y) ... (layer "LAYER"))
        for m in re.finditer(
            r'\(fp_circle\s+\(center\s+([\d.e+-]+)\s+([\d.e+-]+)\)\s+'
            r'\(end\s+([\d.e+-]+)\s+([\d.e+-]+)\).*?\(layer\s+"([^"]+)"\)',
            content, re.DOTALL
        ):
            cx, cy, ex, ey, layer = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)), m.group(5)
            import math
            radius = math.sqrt((ex - cx) ** 2 + (ey - cy) ** 2)
            layer_points.setdefault(layer, []).extend([
                (cx - radius, cy - radius), (cx + radius, cy + radius)
            ])

        # Pads: (pad NUM TYPE SHAPE (at X Y [angle]) (size W H) ... (layers "L1" "L2" ...))
        for m in re.finditer(
            r'\(pad\s+\S+\s+\S+\s+\S+\s+\(at\s+([\d.e+-]+)\s+([\d.e+-]+)'
            r'(?:\s+[\d.e+-]+)?\)\s+\(size\s+([\d.e+-]+)\s+([\d.e+-]+)\)'
            r'.*?\(layers\s+([^)]+)\)',
            content, re.DOTALL
        ):
            px, py, sw, sh = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
            layers_str = m.group(5)
            pad_layers = re.findall(r'"([^"]+)"', layers_str)
            # Add pad extents to a virtual "Pads" layer and to each pad layer
            for pad_layer in pad_layers:
                layer_points.setdefault(pad_layer, []).extend([
                    (px - sw / 2, py - sh / 2), (px + sw / 2, py + sh / 2)
                ])
            layer_points.setdefault("Pads", []).extend([
                (px - sw / 2, py - sh / 2), (px + sw / 2, py + sh / 2)
            ])

        # Compute bounds per layer
        for layer, points in layer_points.items():
            if not points:
                continue
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            bounds[layer] = {
                "min_x": round(min(xs), 3),
                "min_y": round(min(ys), 3),
                "max_x": round(max(xs), 3),
                "max_y": round(max(ys), 3),
                "width": round(max(xs) - min(xs), 3),
                "height": round(max(ys) - min(ys), 3),
            }

        # Compute courtyard bounds (prefer F.CrtYd, fall back to B.CrtYd)
        courtyard = bounds.get("F.CrtYd", bounds.get("B.CrtYd"))

        # Compute overall bounds (union of all layers)
        all_points = [p for pts in layer_points.values() for p in pts]
        overall = None
        if all_points:
            xs = [p[0] for p in all_points]
            ys = [p[1] for p in all_points]
            overall = {
                "min_x": round(min(xs), 3),
                "min_y": round(min(ys), 3),
                "max_x": round(max(xs), 3),
                "max_y": round(max(ys), 3),
                "width": round(max(xs) - min(xs), 3),
                "height": round(max(ys) - min(ys), 3),
            }

        return {
            "success": True,
            "footprint": footprint_spec,
            "courtyard": courtyard,
            "pads": bounds.get("Pads"),
            "fab_front": bounds.get("F.Fab"),
            "fab_back": bounds.get("B.Fab"),
            "overall": overall,
            "all_layers": bounds,
        }

    except Exception as e:
        logger.error(f"Error getting footprint bounds: {e}")
        return {
            "success": False,
            "message": "Failed to get footprint bounds",
            "errorDetails": str(e),
        }
```

- [ ] **Step 2: Add command_routes entry**

In `python/kicad_interface.py`, add after `"get_footprint_info"` entry (~line 418):

```python
"get_footprint_bounds": self.library_commands.get_footprint_bounds,
```

- [ ] **Step 3: Commit**

```bash
git add python/commands/library.py python/kicad_interface.py
git commit -m "Add get_footprint_bounds Python handler — parses .kicad_mod geometry"
```

---

### Task 5: get_footprint_bounds (TypeScript registration)

**Files:**
- Modify: `src/tools/component.ts` (add tool registration near get_footprint_info)

- [ ] **Step 1: Register the tool**

In `src/tools/component.ts`, add after the `get_footprint_info` tool (find it with grep for `"get_footprint_info"` in that file):

```typescript
// ------------------------------------------------------
// Get Footprint Bounds Tool
// ------------------------------------------------------
server.tool(
  "get_footprint_bounds",
  `Return the bounding box of a library footprint (courtyard, fab layer, and pad extents).
Useful for checking physical dimensions before placing connectors or planning board outlines.
Does not require a board to be loaded — reads directly from .kicad_mod library files.`,
  {
    footprint: z.string().describe("Footprint in 'Library:Footprint' format (e.g. 'Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical')")
  },
  async ({ footprint }) => {
    logger.debug(`Getting footprint bounds: ${footprint}`);
    const result = await callKicadScript("get_footprint_bounds", { footprint });
    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify(result)
      }]
    };
  }
);
```

- [ ] **Step 2: Build TypeScript**

Run: `npm run build`
Expected: Clean compilation.

- [ ] **Step 3: Commit**

```bash
git add src/tools/component.ts
git commit -m "Register get_footprint_bounds MCP tool"
```

---

### Task 6: swap_schematic_symbol (Python handler)

**Files:**
- Modify: `python/kicad_interface.py` (add handler method + command_routes entry)

- [ ] **Step 1: Add `_handle_swap_schematic_symbol` handler**

In `python/kicad_interface.py`, add after the `_handle_edit_schematic_component` method (~line 1177). This is a new handler method on `KiCADInterface`:

```python
def _handle_swap_schematic_symbol(self, params):
    """Change a component's lib_id (symbol library reference) while preserving
    position, wiring, properties, and UUID.

    Steps:
    1. Find the placed symbol block by Reference
    2. Replace (lib_id "old") with (lib_id "new")
    3. Inject new symbol definition into lib_symbols if not present
    4. Remove old lib_symbol definition if no other instances reference it
    """
    logger.info("Swapping schematic symbol")
    try:
        from pathlib import Path
        from commands.dynamic_symbol_loader import DynamicSymbolLoader

        schematic_path = params.get("schematicPath")
        reference = params.get("reference")
        new_lib_id = params.get("newLibId")

        if not schematic_path:
            return {"success": False, "message": "schematicPath is required"}
        if not reference:
            return {"success": False, "message": "reference is required"}
        if not new_lib_id:
            return {"success": False, "message": "newLibId is required (e.g. 'LED:WS2812B-2020')"}

        # Parse library:symbol from newLibId
        if ":" not in new_lib_id:
            return {
                "success": False,
                "message": "newLibId must be in 'Library:Symbol' format (e.g. 'LED:WS2812B-2020')",
            }
        new_library, new_symbol = new_lib_id.split(":", 1)

        sch_file = Path(schematic_path)
        if not sch_file.exists():
            return {"success": False, "message": f"Schematic not found: {schematic_path}"}

        with open(sch_file, "r", encoding="utf-8") as f:
            content = f.read()

        # --- Find the placed symbol block by Reference ---
        def find_matching_paren(s, start):
            depth = 0
            i = start
            while i < len(s):
                if s[i] == "(":
                    depth += 1
                elif s[i] == ")":
                    depth -= 1
                    if depth == 0:
                        return i
                i += 1
            return -1

        # Skip lib_symbols section
        lib_sym_pos = content.find("(lib_symbols")
        lib_sym_end = find_matching_paren(content, lib_sym_pos) if lib_sym_pos >= 0 else -1

        # Find the target symbol block
        import re
        pattern = re.compile(r'\(symbol\s+\(lib_id\s+"([^"]+)"\s*\)')
        target_start = target_end = -1
        old_lib_id = None
        search_start = 0

        while True:
            m = pattern.search(content, search_start)
            if not m:
                break
            pos = m.start()
            # Skip if inside lib_symbols
            if lib_sym_pos >= 0 and lib_sym_pos <= pos <= lib_sym_end:
                search_start = lib_sym_end + 1
                continue
            end = find_matching_paren(content, pos)
            if end < 0:
                search_start = pos + 1
                continue
            block = content[pos:end + 1]
            # Check if this block has the target reference
            ref_match = re.search(
                r'\(property\s+"Reference"\s+"' + re.escape(reference) + r'"',
                block,
            )
            if ref_match:
                target_start = pos
                target_end = end
                old_lib_id = m.group(1)
                break
            search_start = end + 1

        if target_start < 0:
            return {
                "success": False,
                "message": f"Component '{reference}' not found in schematic",
            }

        if old_lib_id == new_lib_id:
            return {
                "success": True,
                "message": f"Component '{reference}' already uses {new_lib_id}",
                "reference": reference,
                "lib_id": new_lib_id,
            }

        # --- Replace lib_id in the placed symbol block ---
        block = content[target_start:target_end + 1]
        new_block = block.replace(
            f'(lib_id "{old_lib_id}")',
            f'(lib_id "{new_lib_id}")',
            1,
        )
        content = content[:target_start] + new_block + content[target_end + 1:]

        # --- Inject new symbol definition into lib_symbols ---
        if f'(symbol "{new_lib_id}"' not in content:
            # Write intermediate content so DynamicSymbolLoader can read it
            with open(sch_file, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            derived_project_path = sch_file.parent
            loader = DynamicSymbolLoader(project_path=derived_project_path)
            loader.inject_symbol_into_schematic(sch_file, new_library, new_symbol)

            # Re-read after injection
            with open(sch_file, "r", encoding="utf-8") as f:
                content = f.read()

        # --- Optionally remove old lib_symbol if no other instances reference it ---
        old_lib_id_escaped = re.escape(old_lib_id)
        # Count remaining placed instances using old_lib_id (outside lib_symbols)
        remaining = 0
        search_start = 0
        while True:
            m2 = re.search(rf'\(lib_id\s+"{old_lib_id_escaped}"\)', content[search_start:])
            if not m2:
                break
            abs_pos = search_start + m2.start()
            if lib_sym_pos >= 0 and lib_sym_pos <= abs_pos <= lib_sym_end:
                search_start = abs_pos + 1
                continue
            remaining += 1
            search_start = abs_pos + 1

        if remaining == 0:
            # Remove old symbol definition from lib_symbols
            # Re-find lib_symbols bounds (may have shifted from injection)
            lib_sym_pos = content.find("(lib_symbols")
            if lib_sym_pos >= 0:
                lib_sym_end = find_matching_paren(content, lib_sym_pos)
                lib_sym_section = content[lib_sym_pos:lib_sym_end + 1]
                # Find the old symbol block within lib_symbols
                old_sym_pattern = f'(symbol "{old_lib_id}"'
                old_sym_start = lib_sym_section.find(old_sym_pattern)
                if old_sym_start >= 0:
                    old_sym_abs = lib_sym_pos + old_sym_start
                    old_sym_end = find_matching_paren(content, old_sym_abs)
                    if old_sym_end > 0:
                        # Trim surrounding whitespace
                        trim_start = old_sym_abs
                        while trim_start > lib_sym_pos and content[trim_start - 1] in (" ", "\t"):
                            trim_start -= 1
                        if trim_start > lib_sym_pos and content[trim_start - 1] == "\n":
                            trim_start -= 1
                        content = content[:trim_start] + content[old_sym_end + 1:]

        # --- Write final result ---
        with open(sch_file, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        # Count pins in old vs new to warn about mismatches
        warning = None
        old_lib = old_lib_id.split(":")[0] if ":" in old_lib_id else ""
        old_sym = old_lib_id.split(":")[1] if ":" in old_lib_id else old_lib_id
        try:
            pins_old = len(self.pin_locator.get_symbol_pins(sch_file, old_lib_id) or {})
        except Exception:
            pins_old = -1
        try:
            pins_new = len(self.pin_locator.get_symbol_pins(sch_file, new_lib_id) or {})
        except Exception:
            pins_new = -1
        if pins_old >= 0 and pins_new >= 0 and pins_old != pins_new:
            warning = f"Pin count changed: {old_lib_id} had {pins_old} pins, {new_lib_id} has {pins_new} pins. Check wiring."

        result = {
            "success": True,
            "message": f"Swapped {reference} from {old_lib_id} to {new_lib_id}",
            "reference": reference,
            "old_lib_id": old_lib_id,
            "new_lib_id": new_lib_id,
        }
        if warning:
            result["warning"] = warning
        return result

    except Exception as e:
        logger.error(f"Error swapping schematic symbol: {e}")
        logger.error(traceback.format_exc())
        return {"success": False, "message": str(e)}
```

- [ ] **Step 2: Add command_routes entry**

In `python/kicad_interface.py`, add in the schematic section of command_routes (~line 445):

```python
"swap_schematic_symbol": self._handle_swap_schematic_symbol,
```

- [ ] **Step 3: Commit**

```bash
git add python/kicad_interface.py
git commit -m "Add swap_schematic_symbol handler — change lib_id preserving position/wiring"
```

---

### Task 7: swap_schematic_symbol (TypeScript registration)

**Files:**
- Modify: `src/tools/schematic.ts` (add tool registration)

- [ ] **Step 1: Register the tool**

In `src/tools/schematic.ts`, add after the `edit_schematic_component` tool registration:

```typescript
// ------------------------------------------------------
// Swap Schematic Symbol Tool
// ------------------------------------------------------
server.tool(
  "swap_schematic_symbol",
  `Change a component's symbol library reference (lib_id) while preserving its position,
wiring, properties, and UUID. Use this instead of delete + re-add + rewire when you need
to switch a component to a different symbol (e.g., WS2812 → WS2812B-2020).

The new symbol definition is automatically loaded from KiCad libraries. If the old symbol
is no longer used by any other component, its definition is removed from the schematic.

WARNING: If the new symbol has different pins than the old one, existing wires may not
connect correctly. The tool warns when pin counts differ.`,
  {
    schematicPath: z.string().describe("Path to the .kicad_sch file"),
    reference: z.string().describe("Reference designator of the component to swap (e.g. 'D5')"),
    newLibId: z.string().describe("New symbol in 'Library:Symbol' format (e.g. 'LED:WS2812B-2020')"),
  },
  async (args: { schematicPath: string; reference: string; newLibId: string }) => {
    const result = await callKicadScript("swap_schematic_symbol", args);
    if (result.success) {
      let msg = `Swapped ${args.reference}: ${result.old_lib_id} → ${result.new_lib_id}`;
      if (result.warning) {
        msg += `\n⚠️ ${result.warning}`;
      }
      return { content: [{ type: "text" as const, text: msg }] };
    }
    return {
      content: [{ type: "text" as const, text: `Failed: ${result.message}` }],
    };
  },
);
```

- [ ] **Step 2: Build TypeScript**

Run: `npm run build`
Expected: Clean compilation.

- [ ] **Step 3: Commit**

```bash
git add src/tools/schematic.ts
git commit -m "Register swap_schematic_symbol MCP tool"
```

---

### Task 8: auto_assign_footprints (Python handler)

**Files:**
- Modify: `python/kicad_interface.py` (add handler method + command_routes entry)

- [ ] **Step 1: Add `_handle_auto_assign_footprints` handler**

In `python/kicad_interface.py`, add after `_handle_swap_schematic_symbol`:

```python
def _handle_auto_assign_footprints(self, params):
    """Bulk-assign footprints to schematic components based on lib_id prefix matching.

    For each placed symbol, check if its lib_id starts with any of the provided
    patterns. If it matches, update the Footprint property. Single read/write cycle.
    """
    logger.info("Auto-assigning footprints")
    try:
        from pathlib import Path

        schematic_path = params.get("schematicPath")
        mappings = params.get("mappings", [])

        if not schematic_path:
            return {"success": False, "message": "schematicPath is required"}
        if not mappings:
            return {"success": False, "message": "mappings array is required"}

        sch_file = Path(schematic_path)
        if not sch_file.exists():
            return {"success": False, "message": f"Schematic not found: {schematic_path}"}

        with open(sch_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Build lookup: list of (lib_id_prefix, footprint)
        mapping_list = []
        for m in mappings:
            prefix = m.get("libIdPattern", "")
            footprint = m.get("footprint", "")
            if prefix and footprint:
                mapping_list.append((prefix, footprint))

        if not mapping_list:
            return {"success": False, "message": "No valid mappings provided"}

        # Find all placed symbols (outside lib_symbols)
        import re

        def find_matching_paren(s, start):
            depth = 0
            i = start
            while i < len(s):
                if s[i] == "(":
                    depth += 1
                elif s[i] == ")":
                    depth -= 1
                    if depth == 0:
                        return i
                i += 1
            return -1

        lib_sym_pos = content.find("(lib_symbols")
        lib_sym_end = find_matching_paren(content, lib_sym_pos) if lib_sym_pos >= 0 else -1

        assigned = []
        skipped = []

        # For each mapping, find matching components and update footprint
        # We use _edit_component_in_content which edits by reference, so we need
        # to first collect (reference, footprint) pairs
        edits_to_apply = []  # list of (reference, footprint)

        search_start = 0
        pattern = re.compile(r'\(symbol\s+\(lib_id\s+"([^"]+)"\s*\)')
        while True:
            m = pattern.search(content, search_start)
            if not m:
                break
            pos = m.start()
            if lib_sym_pos >= 0 and lib_sym_pos <= pos <= lib_sym_end:
                search_start = lib_sym_end + 1
                continue
            end = find_matching_paren(content, pos)
            if end < 0:
                search_start = pos + 1
                continue
            block = content[pos:end + 1]
            lib_id = m.group(1)

            # Extract reference
            ref_match = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
            if not ref_match:
                search_start = end + 1
                continue
            ref = ref_match.group(1)

            # Skip template symbols
            if ref.startswith("_TEMPLATE"):
                search_start = end + 1
                continue

            # Check against mappings
            for prefix, footprint in mapping_list:
                if lib_id.startswith(prefix) or lib_id == prefix:
                    edits_to_apply.append((ref, footprint))
                    break

            search_start = end + 1

        # Apply edits using single read/write cycle
        for ref, footprint in edits_to_apply:
            result = self._edit_component_in_content(
                content, ref, new_footprint=footprint,
            )
            if result is not None:
                content = result
                assigned.append({"reference": ref, "footprint": footprint})
            else:
                skipped.append({"reference": ref, "reason": "edit failed"})

        with open(sch_file, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        return {
            "success": True,
            "message": f"Assigned footprints to {len(assigned)} components ({len(skipped)} skipped)",
            "assigned": assigned,
            "skipped": skipped,
        }

    except Exception as e:
        logger.error(f"Error in auto_assign_footprints: {e}")
        logger.error(traceback.format_exc())
        return {"success": False, "message": str(e)}
```

- [ ] **Step 2: Add command_routes entry**

In `python/kicad_interface.py`, add in the schematic section:

```python
"auto_assign_footprints": self._handle_auto_assign_footprints,
```

- [ ] **Step 3: Commit**

```bash
git add python/kicad_interface.py
git commit -m "Add auto_assign_footprints handler — bulk footprint assignment by lib_id prefix"
```

---

### Task 9: auto_assign_footprints (TypeScript registration)

**Files:**
- Modify: `src/tools/schematic.ts` (add tool registration)

- [ ] **Step 1: Register the tool**

In `src/tools/schematic.ts`, add after `swap_schematic_symbol`:

```typescript
// ------------------------------------------------------
// Auto Assign Footprints Tool
// ------------------------------------------------------
server.tool(
  "auto_assign_footprints",
  `Bulk-assign footprints to schematic components based on symbol library prefix matching.
Instead of editing footprints one component at a time, provide a mapping of lib_id patterns
to footprints and all matching components are updated in a single operation.

Example: map "Device:R" → "Resistor_SMD:R_0603_1608Metric" to assign 0603 footprints to
all resistors, "Device:C" → "Capacitor_SMD:C_0603_1608Metric" for all capacitors, etc.`,
  {
    schematicPath: z.string().describe("Path to the .kicad_sch file"),
    mappings: z.array(z.object({
      libIdPattern: z.string().describe("lib_id prefix to match (e.g. 'Device:R', 'Device:C')"),
      footprint: z.string().describe("Footprint to assign (e.g. 'Resistor_SMD:R_0603_1608Metric')"),
    })).describe("Array of {libIdPattern, footprint} mappings"),
  },
  async (args: {
    schematicPath: string;
    mappings: Array<{ libIdPattern: string; footprint: string }>;
  }) => {
    const result = await callKicadScript("auto_assign_footprints", args);
    if (result.success) {
      const lines = [`Assigned footprints to ${result.assigned?.length ?? 0} components:`];
      for (const a of result.assigned ?? []) {
        lines.push(`  ${a.reference} → ${a.footprint}`);
      }
      if (result.skipped?.length) {
        lines.push(`Skipped ${result.skipped.length}: ${result.skipped.map((s: any) => s.reference).join(", ")}`);
      }
      return { content: [{ type: "text" as const, text: lines.join("\n") }] };
    }
    return {
      content: [{ type: "text" as const, text: `Failed: ${result.message}` }],
    };
  },
);
```

- [ ] **Step 2: Build TypeScript**

Run: `npm run build`
Expected: Clean compilation.

- [ ] **Step 3: Commit**

```bash
git add src/tools/schematic.ts
git commit -m "Register auto_assign_footprints MCP tool"
```

---

### Task 10: Final build, verify, and push

**Files:**
- All modified files from tasks 1-9

- [ ] **Step 1: Full rebuild**

Run: `npm run build`
Expected: Clean compilation, no errors.

- [ ] **Step 2: Verify command_routes count**

Run: `grep -c "self\._handle\|self\.board_commands\|self\.library_commands\|self\.project_commands\|self\.component_commands\|self\.routing_commands\|self\.design_rule_commands\|self\.export_commands\|self\.symbol_library_commands" python/kicad_interface.py | head -1`

This should show 5 more entries than before.

- [ ] **Step 3: Verify tool name consistency**

For each new tool, confirm TypeScript `callKicadScript("name")` matches Python `command_routes["name"]`:
- `delete_board_outline` — TS: board.ts, PY: command_routes
- `replace_board_outline` — TS: board.ts, PY: command_routes
- `get_footprint_bounds` — TS: component.ts, PY: command_routes
- `swap_schematic_symbol` — TS: schematic.ts, PY: command_routes
- `auto_assign_footprints` — TS: schematic.ts, PY: command_routes

- [ ] **Step 4: Push to remix**

```bash
git push remix main
```

- [ ] **Step 5: Update README**

Add the 5 new tools to the README's tool list and commit + push.
