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
- `server.ts` — `KiCADMcpServer` class, spawns Python child process, registers all tools/resources/prompts
- `config.ts` — Zod-validated config loading from `config/`
- `logger.ts` — Logger (stderr only; stdout reserved for MCP protocol)
- `tools/*.ts` — Tool registration using `@modelcontextprotocol/sdk`. Each tool calls `callKicadScript(command, args)` which sends JSON to the Python process
- `resources/*.ts` — MCP resource handlers
- `prompts/*.ts` — MCP prompt templates

### Python Layer (`python/`)
- `kicad_interface.py` — Main entry point. Reads JSON commands from stdin, dispatches to command handlers, returns JSON on stdout. Supports IPC and SWIG backends.
- `commands/` — Command handlers organized by domain:
  - `schematic.py` — `SchematicManager` (create schematic, template-based)
  - `component_schematic.py` — `ComponentManager` (add/edit/delete/move components using kicad-skip + dynamic symbol loading)
  - `connection_schematic.py` — `ConnectionManager` (wires, net labels, connections)
  - `wire_manager.py` — `WireManager` (S-expression manipulation for wire creation)
  - `pin_locator.py` — `PinLocator` (pin position discovery via S-expr parsing)
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
- `express` — (available but primary transport is STDIO)

### Python
- `kicad-skip` — S-expression-based KiCAD schematic manipulation
- `sexpdata` — S-expression parsing (used by wire_manager, pin_locator, dynamic_symbol_loader)
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
- **Tool name must match across layers**: The `callKicadScript("command_name", ...)` string in TypeScript **must exactly match** the key in the `command_routes` dict in `python/kicad_interface.py`. A mismatch causes "unknown command" errors.
- **Every tool needs `schematicPath` or `pcbPath`**: All schematic/board tools must include the file path parameter in both the Zod schema (TypeScript) and the Python handler. The Python backend is stateless per-call for schematics.
- **Never use `sexpdata` round-trips for writing**: `sexpdata.loads()` → modify → `sexpdata.dumps()` collapses the entire `.kicad_sch` file to a single line, breaking git diffs and other parsers. Use text insertion via `python/commands/sexp_writer.py` instead.
- **Handle single-line .kicad_sch files**: Some schematic files may already be single-line (from prior sexpdata corruption). All regex parsers must work without `^` line-start anchoring. Use `\(label\b` not `^  \(label`.
- **No stale caches for schematic state**: Never cache loaded `Schematic` objects across operations. The file changes between calls. Pin definition caches (lib_symbols → pin data) are OK since symbol definitions don't change.
- **List/query tools must be fast**: Tools like `list_schematic_components` and `list_schematic_nets` must not call per-item functions that re-read the file. Load once, iterate in memory.
- **Parameter format normalization**: Python handlers should accept both `{x, y}` objects and `[x, y]` arrays for coordinates, since TypeScript sends objects but some internal callers use arrays.

## Code Patterns

### Adding a new MCP tool
1. **TypeScript** (`src/tools/<domain>.ts`): Register tool with `server.tool(name, description, zodSchema, handler)`. Handler calls `callKicadScript(command, args)`.
2. **Python handler** (`python/kicad_interface.py`): Add `_handle_<command>` method to `KiCADInterface`.
3. **Python dispatch** (`python/kicad_interface.py`): Add `"command_name": self._handle_command_name` to `command_routes` dict (~line 370).
4. **Rebuild**: Run `npm run build`.

The `callKicadScript` command string and the `command_routes` key **must match exactly**.

### Schematic file manipulation
- **Text insertion** (`python/commands/sexp_writer.py`): Preferred method for adding wires, labels, junctions, etc. Inserts formatted text before `(sheet_instances`, preserving file formatting.
- **kicad-skip** (`from skip import Schematic`): Used for reading/querying existing schematic elements (symbols, properties, wires). Good for reads, avoid for writes that go through `sexpdata.dumps()`.
- **DynamicSymbolLoader** (`python/commands/dynamic_symbol_loader.py`): Text-based symbol injection from KiCad libraries. Handles `(instances)` blocks, rotation-aware field positions. Uses text manipulation, not sexpdata.
- **PinLocator** (`python/commands/pin_locator.py`): Returns pin **endpoints** (connectable tip), not body positions. Re-reads file from disk each call (no caching). Pin definition cache is per lib_id and safe to keep.

### Board operations
- Use `pcbnew` SWIG API directly
- Board must be loaded via `pcbnew.LoadBoard(path)`

### Error handling
- Python commands return `{"success": True/False, "message": "...", ...}`
- TypeScript wraps results in MCP content blocks

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

- **"Unknown command" error**: The `callKicadScript` command string in TypeScript doesn't match the `command_routes` key in Python. Check both files.
- **Tool works in tests but not via MCP**: Forgot to run `npm run build`. The server runs from `dist/`, not `src/`.
- **Schematic file becomes single-line**: Used `sexpdata.dumps()` to write the file. Use `sexp_writer.py` text insertion instead.
- **connect_to_net fails for later components**: PinLocator was caching stale Schematic objects. Fixed — now re-reads from disk each call.
- **Pin positions are wrong by 2.54mm**: You're getting body positions instead of endpoints. `PinLocator.get_pin_location()` now returns endpoints (body + pin length along angle).
- **Components show "R?" instead of "R1"**: Missing `(instances)` block. `add_schematic_component` and `annotate_schematic` now add these automatically.
- **list_schematic_nets/components times out**: The handler was re-reading the file per item. Fixed — loads once, iterates in memory.
- **Labels not electrically connected**: Labels placed without a wire stub. `connect_to_net` adds wire stubs automatically.

## Important Notes

- **stdout is sacred**: The TypeScript server uses STDIO transport. All logging goes to stderr or files. Never `console.log()` in TS or `print()` in Python (except for JSON responses on the protocol channel).
- **KiCAD 9+ required**: The server targets KiCAD 9.0+ (schema version 20250114).
- **Cross-platform**: Supports Linux, Windows, macOS. Platform detection in `python/utils/platform_helper.py`.
- **kicad-server.ts is legacy**: `src/kicad-server.ts` is an older implementation with hardcoded Windows paths. The active server is `src/server.ts` (`KiCADMcpServer` class).
