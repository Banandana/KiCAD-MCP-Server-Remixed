# KiCAD MCP Server

## What This Project Is

An MCP (Model Context Protocol) server that enables AI assistants to design PCBs and schematics in KiCAD. It has a **TypeScript frontend** (MCP server, tool registration, STDIO transport) and a **Python backend** (KiCAD interaction via pcbnew SWIG API and kicad-skip for schematics).

## Architecture

```
AI Client (Claude, etc.)
  ↕ STDIO (MCP protocol)
TypeScript Server (src/)
  ↕ JSON over stdin/stdout (child process)
Python Backend (python/)
  ↕ pcbnew SWIG API / kicad-skip / S-expression manipulation
KiCAD files (.kicad_pcb, .kicad_sch, .kicad_pro)
```

### TypeScript Layer (`src/`)
- `index.ts` — Entry point, starts MCP server
- `server.ts` — `KiCADMcpServer` class, spawns Python child process (with auto-restart on crash), registers all tools/resources/prompts
- `config.ts` — Zod-validated config loading from `config/`
- `logger.ts` — Logger (stderr only; stdout reserved for MCP protocol)
- `tools/*.ts` — Tool registration using `@modelcontextprotocol/sdk`. Each tool calls `callKicadScript(command, args)` which sends JSON to the Python process
- `resources/*.ts` — MCP resource handlers
- `prompts/*.ts` — MCP prompt templates

### Python Layer (`python/`)
- `kicad_interface.py` — Main entry point. Reads JSON commands from stdin, dispatches to command handlers, returns JSON on stdout. Supports IPC and SWIG backends. **WARNING: This is a 6400-line god file with duplicated pin math in 4+ handlers. When fixing pin calculations, check ALL inline pin calc sites, not just `pin_locator.py`.**
- `commands/` — Command handlers organized by domain:
  - `schematic.py` — `SchematicManager` (create schematic, template-based)
  - `component_schematic.py` — `ComponentManager` (add/edit/delete/move components using kicad-skip + dynamic symbol loading)
  - `connection_schematic.py` — `ConnectionManager` (wires, net labels, connections)
  - `wire_manager.py` — `WireManager` (thin delegation layer to `sexp_writer.py`)
  - `pin_locator.py` — `PinLocator` (pin position discovery via S-expr parsing, handles rotation + mirror)
  - `sexp_writer.py` — Text-based insertion into .kicad_sch files (wires, labels, junctions, no-connects). All writes use `f.flush()` + `os.fsync()`.
  - `component.py` — Board-level component operations (via pcbnew)
  - `routing.py` — Trace routing (via pcbnew)
  - `board.py`, `board/*.py` — Board operations (layers, outline, size, 2D view)
  - `project.py` — Project creation/management
  - `library.py`, `library_schematic.py`, `library_symbol.py` — Library operations
  - `dynamic_symbol_loader.py` — Loads symbols from KiCAD libraries at runtime
  - `footprint.py` — Footprint operations
  - `export.py` — Export (Gerber, PDF, SVG, BOM, 3D, etc.)
  - `design_rules.py` — DRC rules
  - `jlcpcb.py`, `jlcpcb_parts.py`, `jlcsearch.py` — JLCPCB integration
  - `datasheet_manager.py` — Datasheet URL extraction
  - `symbol_creator.py` — Custom symbol creation
  - `svg_import.py` — SVG logo import
- `kicad_api/` — Backend abstraction (SWIG vs IPC)
  - `base.py` — Abstract base class
  - `swig_backend.py` — pcbnew SWIG backend
  - `ipc_backend.py` — KiCAD IPC API backend (experimental)
  - `factory.py` — Backend factory
- `schemas/tool_schemas.py` — Python-side tool schema definitions
- `resources/` — Resource definitions
- `utils/` — Platform detection, KiCAD process management
- `templates/` — Schematic templates (used by `create_schematic`)

## Key Libraries & Dependencies

### TypeScript
- `@modelcontextprotocol/sdk` — MCP server framework
- `zod` — Schema validation for tool inputs

### Python
- `kicad-skip` — S-expression-based KiCAD schematic manipulation
- `sexpdata` — S-expression parsing (used by pin_locator, dynamic_symbol_loader)
- `pcbnew` — KiCAD's SWIG Python API (board operations, must be on PYTHONPATH)
- `Pillow` — Image processing for board rendering
- `cairosvg` — SVG rendering
- `pydantic` — Data validation

## Build & Run

```bash
# Install dependencies
npm install
pip install -r requirements.txt        # production
pip install -r requirements-dev.txt    # development (includes testing/linting)

# Build TypeScript
npm run build          # compile once
npm run build:watch    # watch mode

# Run the MCP server
npm start              # or: node dist/index.js

# Clean & rebuild
npm run rebuild
```

## Testing

```bash
# All tests
npm test               # runs both TS and Python tests

# Python tests only
pytest tests/ -v
pytest python/tests/ -v

# With coverage
pytest tests/ --cov=python --cov-report=html --cov-report=term

# Markers
pytest -m unit         # fast, no KiCAD needed
pytest -m integration  # requires KiCAD installed
```

Test paths: `tests/` (top-level) and `python/tests/`. Config in `pytest.ini`.

**Test coverage is minimal** — only 4 test files exist. None of the 65+ handlers in `kicad_interface.py` have tests. No tests for `sexp_writer.py`, `pin_locator.py`, `dynamic_symbol_loader.py`, or batch operations.

## Linting & Formatting

```bash
# Python
black python/                    # format
mypy python/                     # type check
flake8 python/                   # lint
isort python/                    # import sorting

# TypeScript
npm run lint:ts                  # ESLint (if configured)
npx prettier --write 'src/**/*.ts'

# All
npm run format                   # prettier + black
npm run lint                     # eslint + black + mypy + flake8
```

## Rules

- **ALWAYS rebuild after every change**: Run `npm run build` after any edit to TypeScript files. The MCP server runs from `dist/`, not `src/`. Forgetting to rebuild means your changes won't take effect.
- **ALWAYS commit and push after every change**: After fixing a bug or adding a feature, commit and push to `remix` remote immediately. Don't batch changes.
- **ALWAYS flush writes to disk**: Every file write to `.kicad_sch` or `.kicad_pcb` must use `f.flush()` + `os.fsync(f.fileno())` inside the `with open(...)` block. The MCP client reads the file immediately after the tool call returns — unflushed data causes stale reads.
- **Tool name must match across layers**: The `callKicadScript("command_name", ...)` string in TypeScript **must exactly match** the key in the `command_routes` dict in `python/kicad_interface.py`. A mismatch causes "unknown command" errors.
- **Every tool needs `schematicPath` or `pcbPath`**: All schematic/board tools must include the file path parameter in both the Zod schema (TypeScript) and the Python handler. The Python backend is stateless per-call for schematics.
- **Never use `sexpdata` round-trips for writing**: `sexpdata.loads()` → modify → `sexpdata.dumps()` collapses the entire `.kicad_sch` file to a single line, breaking git diffs and other parsers. Use text insertion via `python/commands/sexp_writer.py` instead.
- **Handle single-line .kicad_sch files**: Some schematic files may already be single-line (from prior sexpdata corruption). All regex parsers must work without `^` line-start anchoring. Use `\(label\b` not `^  \(label`.
- **No stale caches for schematic state**: Never cache loaded `Schematic` objects across operations. The file changes between calls. Pin definition caches (lib_symbols → pin data) are OK since symbol definitions don't change mid-session.
- **List/query tools must be fast**: Tools like `list_schematic_components` and `list_schematic_nets` must not call per-item functions that re-read the file. Load once, iterate in memory.
- **Parameter format normalization**: Python handlers should accept both `{x, y}` objects and `[x, y]` arrays for coordinates, since TypeScript sends objects but some internal callers use arrays.
- **Snap component positions to 1.27mm grid**: All component placements must snap to the KiCad schematic grid (1.27mm). Off-grid components = off-grid pins = broken connections. `DynamicSymbolLoader.create_component_instance` does this automatically.
- **Verify with kicad-cli, not MCP tools**: MCP's own diagnostics (get_pin_connections, etc.) use the same math as the tools that placed the components. Always verify with `kicad-cli sch erc` via Bash as the ground truth.
- **Batch operations must minimize file I/O**: Batch tools that call single-item handlers in a loop (e.g., `batch_add_junction`, `batch_rotate_labels`) re-read and re-write the entire file per item. For performance-critical paths, read once, apply all edits in memory, write once.

## KiCad S-Expression Gotchas

These are hard-won lessons from debugging. Read before touching any schematic file manipulation code.

### Pin coordinate system
- Symbol-local pin definitions use **Y-up** coordinates. Schematic uses **Y-down**.
- **Always negate Y** when converting pin (at) to schematic coords: `pin_rel_y = -pin_data["y"]`
- **Always apply mirror transforms** after Y-negation, before rotation: `if mirror_x: pin_rel_y = -pin_rel_y` / `if mirror_y: pin_rel_x = -pin_rel_x`. Mirror handling was missing from `PinLocator` until recently — if you see wrong pin positions on mirrored symbols, check this first.
- The `(at x y angle)` in a pin definition IS the **connectable endpoint**. The `length` extends from endpoint toward the body, NOT outward. **Do not add length to get the endpoint.**
- Pin angles in definitions point FROM endpoint TOWARD body. For wire stubs going AWAY from body, use `(angle + 180 + symbol_rotation) % 360`. For mirrored symbols, apply mirror to the pin angle before adding symbol rotation.

### Duplicated pin math (known tech debt)
Pin position calculation (Y-negate, mirror, rotate) is duplicated in **5 places**:
1. `pin_locator.py:get_pin_location()` — the canonical implementation
2. `pin_locator.py:get_all_symbol_pins()` — inline for performance
3. `kicad_interface.py:_handle_list_schematic_components()` — inline
4. `kicad_interface.py:_handle_batch_get_schematic_pin_locations()` — inline
5. `kicad_interface.py:_handle_get_net_connectivity()` and `_handle_validate_wire_connections()` — inline

**When fixing pin math, grep for `pin_rel_y = -` to find ALL sites.** A fix in one place that misses the others will cause tools to disagree about pin positions.

### Global label format
KiCad global labels have a `(shape ...)` attribute between the name and `(at ...)`:
```
(global_label "SDA" (shape bidirectional) (at 100 50 0) ...)
```
**Every regex that matches labels must include `(?:\s+\(shape\s+[^)]*\))?` after the label name.** This is the #1 recurring bug — it was found and fixed in 7+ separate handlers. When adding new label-matching code, always use this pattern:
```python
rf'\({label_type}\s+"([^"]*)"(?:\s+\(shape\s+[^)]*\))?\s+\(at\s+([\d.e+-]+)\s+([\d.e+-]+)'
```
And always iterate `["label", "global_label", "hierarchical_label"]`, not just `["label", "global_label"]`.

### (instances) blocks
- KiCad 9 requires `(instances (project "name" (path "/uuid" (reference "R1") (unit 1))))` inside every placed symbol for annotation to work.
- `DynamicSymbolLoader.create_component_instance` already includes this in the template.
- Never add a second one. When checking if a symbol already has `(instances)`, use balanced-paren search to find the symbol block, not newline-based heuristics (single-line files have no newlines).

### (hide yes) placement
- Field visibility: `(hide yes)` goes inside `(effects ...)`, NOT inside `(font ...)`.
- Correct: `(effects (font (size 1.27 1.27)) (hide yes))`
- Wrong: `(effects (font (size 1.27 1.27) (hide yes)))` — malformed, kicad-cli rejects it.
- When toggling visibility, **always strip ALL existing (hide yes) first** (from both effects and font levels), then add one at the effects level if hiding.

### Wire connectivity
- KiCad wires must be strictly horizontal or vertical. Diagonal wires don't form electrical connections.
- Wire endpoints must exactly match label/pin positions for connectivity. Off-grid mismatches = dangling.
- When creating wire stubs from pins, snap the stub endpoint to grid **only along the pin's axis** (vertical pins snap Y only, horizontal pins snap X only).
- T-junction wire connections require junction dots (`add_junction` / `batch_add_junction`). Without them, KiCad reports "pin not connected" ERC errors even though wires visually intersect.

### Power symbols
- Power symbols (#PWR) use `lib_id "power:GND"` etc. They're symbols, not labels.
- `get_net_connections` must search power symbols in addition to labels.
- `get_pin_connections` must detect power symbol pins at wire endpoints.
- Power symbol references must be auto-numbered (#PWR068, not #PWR?) to avoid collisions.
- Their Reference field should be hidden by default (`(hide yes)` in effects).

### String replacement in loops
- **Never modify a string inside a `finditer` loop on that string.** After the first replacement changes string length, all subsequent match positions are wrong. Collect all `(start, end, new_text)` edits first, then apply in reverse order.

## Validation Checklist for New Tools

When adding or modifying a schematic tool, verify:

1. [ ] `callKicadScript` command name matches `command_routes` key exactly
2. [ ] TypeScript schema includes `schematicPath` as required parameter
3. [ ] Python handler accepts both `{x,y}` objects and `[x,y]` arrays for coordinates
4. [ ] Any label regex includes `(?:\s+\(shape\s+[^)]*\))?` for global labels
5. [ ] Label iteration covers all three types: `label`, `global_label`, `hierarchical_label`
6. [ ] Pin Y coordinate is negated when converting from symbol-local to schematic coords
7. [ ] Pin mirror transforms are applied (mirror_x → negate Y, mirror_y → negate X)
8. [ ] File writes use `sexp_writer.py` text insertion, not `sexpdata.dumps()`
9. [ ] File writes include `f.flush()` + `os.fsync(f.fileno())`
10. [ ] No line-start-anchored regex (`^`) — files may be single-line
11. [ ] No string modification inside `finditer` loops
12. [ ] `npm run build` run after TypeScript changes
13. [ ] Commit and push to `remix` remote

## Code Patterns

### Adding a new MCP tool
1. **TypeScript** (`src/tools/<domain>.ts`): Register tool with `server.tool(name, description, zodSchema, handler)`. Handler calls `callKicadScript(command, args)`.
2. **Python handler** (`python/kicad_interface.py`): Add `_handle_<command>` method to `KiCADInterface`.
3. **Python dispatch** (`python/kicad_interface.py`): Add `"command_name": self._handle_command_name` to `command_routes` dict (~line 370).
4. **Rebuild**: Run `npm run build`.

The `callKicadScript` command string and the `command_routes` key **must match exactly**.

### Schematic file manipulation
- **Text insertion** (`python/commands/sexp_writer.py`): Preferred method for adding wires, labels, junctions, etc. Inserts formatted text before `(sheet_instances`, preserving file formatting. All writes are flushed to disk with `os.fsync()`.
- **kicad-skip** (`from skip import Schematic`): Used for reading/querying existing schematic elements (symbols, properties, wires). Good for reads, avoid for writes that go through `sexpdata.dumps()`.
- **DynamicSymbolLoader** (`python/commands/dynamic_symbol_loader.py`): Text-based symbol injection from KiCad libraries. Handles `(instances)` blocks, rotation-aware field positions. Uses text manipulation, not sexpdata.
- **PinLocator** (`python/commands/pin_locator.py`): Returns pin **endpoints** (connectable tip), not body positions. Handles rotation AND mirror transforms. `get_all_symbol_pins()` loads the file once and computes all pins inline (no per-pin re-reads). Pin definition cache is per lib_id and safe to keep.

### Board operations
- Use `pcbnew` SWIG API directly
- Board must be loaded via `pcbnew.LoadBoard(path)`

### Error handling
- Python commands return `{"success": True/False, "message": "...", ...}`
- TypeScript wraps results in MCP content blocks

### Python process lifecycle
- The TypeScript server spawns Python as a child process via `spawnPythonProcess()`
- If the Python process crashes (non-zero exit), it auto-restarts after 1 second, rejects the pending request, and resumes the request queue
- Long-running commands (`run_erc`, `run_drc`, exports) get a 10-minute timeout instead of the default 30 seconds

## Environment Variables

- `KICAD_PYTHON` — Override Python executable path
- `KICAD_BACKEND` — Backend selection: `auto` (default), `ipc`, or `swig`
- `KICAD_AUTO_LAUNCH` — Set to `true` to auto-launch KiCAD UI

## Configuration

Config files in `config/`:
- `default-config.json` — Default settings
- `claude-desktop-config.json` — Claude Desktop integration
- Platform-specific examples: `linux-config.example.json`, `windows-config.example.json`, `macos-config.example.json`

## Logs

Both layers log to `~/.kicad-mcp/logs/`:
- TypeScript: `kicad-mcp-YYYY-MM-DD.log`
- Python: `kicad_interface.log`

## Common Pitfalls

These are bugs that were actually encountered and fixed. If you see these symptoms, check the fix is still in place.

- **"Unknown command" error**: `callKicadScript` string in TS doesn't match `command_routes` key in Python.
- **Tool works in code but not via MCP**: Forgot `npm run build`. Server runs from `dist/`.
- **"Python process for KiCAD scripting is not running"**: Python backend crashed. Should auto-restart now (see `spawnPythonProcess()` in `server.ts`). If it persists, check Python logs at `~/.kicad-mcp/logs/kicad_interface.log`.
- **Schematic file becomes single-line**: Used `sexpdata.dumps()`. Use `sexp_writer.py` instead.
- **Pin positions wrong by ~2.5mm or pins swapped**: Forgot to negate Y (`-pin_data["y"]`), or added pin length to endpoint (the `(at)` IS the endpoint already).
- **Pin positions wrong on mirrored symbols**: Mirror transforms not applied. Check `mirror_x`/`mirror_y` handling before rotation.
- **Pin positions reflected (correct offset, wrong sign)**: Symbol-local Y-up vs schematic Y-down. Must negate Y.
- **Wires/labels placed at wrong location**: Pin angle formula wrong. Outward angle = `(pin_def_angle + 180 + symbol_rotation) % 360`.
- **Labels placed but electrically dangling**: Component off-grid (not 1.27mm aligned), or wire stub creates diagonal (must snap along pin axis only).
- **Global labels not found by tools**: Regex missing `(shape ...)` clause. This was the #1 recurring bug — fixed in 7+ places.
- **T-junction wires not connected**: Missing junction dots. Use `add_junction` or `batch_add_junction` at wire T-intersections.
- **run_erc returns 0 violations**: Was parsing `erc_data["violations"]` but KiCad 9 puts them under `sheets[*].violations`. Also needs `--severity-all` flag.
- **run_erc coordinates don't match schematic**: kicad-cli may output coordinates in 1/100mm scale. The handler auto-detects this and scales to mm, but the heuristic (first coord < 5mm) can false-positive on small schematics.
- **run_erc times out**: Was not in the long-running commands list. Now has 10-minute timeout.
- **Duplicate (instances) blocks corrupt file**: The check for existing instances used newline-based heuristics that fail on single-line files. Must use balanced-paren search.
- **#PWR? reference collisions**: Power symbols weren't auto-numbered. Now scans for highest existing #PWR number.
- **get_net_connections empty for power nets**: Was only searching `(label)` elements, not global labels or power symbols. Now searches all label types + power symbols.
- **(hide yes) inside (font) instead of (effects)**: Malformed S-expression that kicad-cli rejects. Must strip all (hide yes) first, then add at effects level only.
- **move_region moves items outside bbox**: String was modified inside `finditer` loop, corrupting match positions. Must collect edits first, apply in reverse order.
- **Diagnostic tools report false results**: MCP tools use the same pin math as placement tools. A pin math bug makes both placement AND verification wrong in the same way — everything looks correct to itself. Always verify with `kicad-cli sch erc` as ground truth.
- **get_schematic_pin_locations timeout on large MCUs**: `get_all_symbol_pins` was re-parsing the file per pin. Fixed — now loads once, computes all pins inline.
- **Batch operations show stale state**: Each sub-operation re-reads/re-writes the whole file. For N items that's N full file cycles. This is a known performance limitation.
- **Schematic changes not visible immediately**: File writes were not flushed to disk. All writes now use `f.flush()` + `os.fsync()`.

## Important Notes

- **stdout is sacred**: The TypeScript server uses STDIO transport. All logging goes to stderr or files. Never `console.log()` in TS or `print()` in Python (except for JSON responses on the protocol channel).
- **KiCAD 9+ required**: The server targets KiCAD 9.0+ (schema version 20250114).
- **Cross-platform**: Supports Linux, Windows, macOS. Platform detection in `python/utils/platform_helper.py`.

## Known Technical Debt

These are known issues that haven't been fixed yet. Keep them in mind when working on the codebase.

- **`kicad_interface.py` is a 6400-line god file**: 65+ handler methods with duplicated imports (`import re` appears 18 times, `from pathlib import Path` 35 times). Should be decomposed into domain-specific handler modules, but all inline pin math sites must stay in sync until then.
- **No TypeScript tests**: Zero tests for tool registration, request queue, JSON parsing, or Python subprocess communication.
- **Minimal Python tests**: Only 4 test files. No tests for the vast majority of handlers.
- **`_find_insert_position` has no fallback**: If a schematic lacks `(sheet_instances`, all writes via `sexp_writer.py` raise `ValueError`. Should fall back to inserting before the final `)`.
- **Wire connectivity tracing is O(W × P) per iteration**: `get_net_connectivity` and `validate_wire_connections` use naive flood-fill. Fine for small schematics, slow for large ones.
- **ERC coordinate auto-detection heuristic is fragile**: Checks if first coord < 5mm to decide scaling. Can false-positive on schematics with violations near origin.
