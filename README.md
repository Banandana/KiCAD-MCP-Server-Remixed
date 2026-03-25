# KiCAD MCP Server — Remixed

A heavily reworked fork of [mixelpixx/KiCAD-MCP-Server](https://github.com/mixelpixx/KiCAD-MCP-Server) that fixes the schematic workflow from the ground up. The original project provided a solid foundation for AI-assisted PCB design via the Model Context Protocol, but the schematic side had deep bugs that made it unreliable in practice. This remix fixes those bugs, adds missing tools, and makes the whole system actually work end-to-end with KiCad 9.

## What changed (and why)

### The schematic math was wrong

The single biggest issue: pin position calculations were broken. KiCad schematics use a Y-down coordinate system, but the code was treating pin definitions (which use Y-up) without negating Y. On top of that, the rotation formula used standard counterclockwise math instead of KiCad's actual transform. The result: pins ended up in the wrong place at 90/270 degree rotations, mirrored symbols had reflected pins, and wires connected to empty space.

This remix fixes the coordinate transform everywhere it appears (10 separate call sites — it was copy-pasted liberally), adds mirror support that was completely missing, and uses the correct rotation matrix for KiCad's Y-down space.

### Wire connectivity actually works now

Placing wires between components used to silently fail in KiCad's own ERC even when the MCP tools said everything was connected. The root cause: T-junctions (where one wire meets the middle of another) need explicit junction dots in KiCad, but the tools never added them.

Wire placement now automatically detects T-junctions and adds junction dots. There's also a `fix_connectivity` tool that runs KiCad's own ERC as ground truth and auto-repairs any remaining junction issues — useful as a final sanity check after building a schematic.

### Global labels weren't global

KiCad's global labels have a `(shape ...)` attribute between the name and position that local labels don't have. The original regex patterns didn't account for this, so global labels were invisible to about half the tools — they couldn't be found, moved, rotated, or included in connectivity analysis. This was the most recurring bug in the codebase, fixed across 7+ handlers.

### File writes could silently vanish

Schematic changes weren't being flushed to disk before the tool returned. Since the MCP client reads the file immediately after, it would sometimes see stale data. Every file write now uses `fsync` to guarantee persistence.

### Batch operations were painfully slow

Operations like "delete 20 components" or "add 15 wires" were reading and writing the entire schematic file once per item. Batch handlers now do a single read, apply all changes in memory, and write once. This also eliminates a class of bugs where intermediate writes shifted file offsets and corrupted later operations.

### Prompts returned raw placeholders

All 17 prompt templates had `{{variable}}` placeholders that were never substituted with actual arguments. They now work as intended.

### Unimplemented tools crashed the server

11 TypeScript tool registrations pointed to Python handlers that didn't exist, causing "unknown command" errors at runtime. These are now commented out with TODO markers until the handlers are written.

## New tools

This remix adds 30+ tools. Here are the highlights:

**Building schematics:**
- `add_power_symbol` — Place GND, VCC, +3V3 etc. with auto-numbered hidden references
- `add_junction` / `batch_add_junction` — Wire junction dots for T-intersections
- `add_no_connect` — X flags on unused pins
- `add_schematic_text` — Text annotations
- `batch_add_wire`, `batch_delete`, `batch_connect_to_net` — Bulk operations
- `batch_edit_schematic_components`, `batch_delete_schematic_components` — Bulk component edits
- `bulk_move_schematic_components` — Move multiple components with their fields
- `move_connected` — Move a component and drag all connected wires/labels with it
- `move_region` — Block-select and move everything in a bounding box

**Checking your work:**
- `check_schematic_overlaps` — Detect visual conflicts (component-component, label-component, wire-through-label) with smart suppression of standard pin-endpoint labels
- `get_schematic_layout` — Structured geometry dump of a region (components, labels, wires, overlaps)
- `find_orphan_items` — Dangling wires, orphan labels, unconnected pins
- `get_pin_connections` — Per-pin connection status with power symbol awareness
- `get_connected_items` — Everything touching a component's pins

**Net analysis (union-find based, fast):**
- `get_component_nets` — What nets is this component on?
- `get_net_components` — What components are on this net?
- `get_pin_net_name` — What net is this specific pin on?
- `export_netlist_summary` — Full netlist in one call
- `validate_component_connections` — Check if specific pins are on expected nets
- `find_shorted_nets` — Detect accidentally merged nets
- `find_single_pin_nets` — Find broken connections (nets with only one pin)
- `get_net_connectivity` — Trace everything reachable on a named net
- `validate_wire_connections` — Targeted pin connectivity check without full ERC

**Repair:**
- `fix_connectivity` — Run KiCad ERC, auto-fix T-junctions, report what's left

**Board outline management:**
- `delete_board_outline` — Remove all Edge.Cuts shapes (the board outline)
- `replace_board_outline` — Atomic delete + create: replace the outline in one call

**Symbol and footprint workflow:**
- `swap_schematic_symbol` — Change a component's symbol (lib_id) while preserving position, wiring, properties, and UUID. Auto-loads the new symbol definition and cleans up the old one
- `get_footprint_bounds` — Return courtyard, fab layer, and pad bounding boxes for a library footprint. Parses `.kicad_mod` files directly, no board needed
- `auto_assign_footprints` — Bulk-assign footprints by lib_id prefix mapping (e.g. all `Device:R` get `R_0603_1608Metric`)

## What's the same

Everything else on the PCB/board side is untouched — component placement, trace routing, copper pours, exports, DRC, JLCPCB integration. The TypeScript MCP server infrastructure, Python subprocess lifecycle, and IPC backend are also unchanged (aside from the auto-restart improvement when the Python process crashes).

## Setup

### Prerequisites
- KiCad 9.0+
- Node.js 18+
- Python 3.10+

### Install

```bash
git clone https://github.com/Banandana/KiCAD-MCP-Server-Remixed.git
cd KiCAD-MCP-Server-Remixed
npm install
pip install -r requirements.txt
npm run build
```

Verify KiCad's Python module is accessible:
```bash
python3 -c "import pcbnew; print(pcbnew.GetBuildVersion())"
```

### Configure your MCP client

Add to your Claude Desktop config (`~/.config/Claude/claude_desktop_config.json` on Linux):

```json
{
  "mcpServers": {
    "kicad": {
      "command": "node",
      "args": ["/path/to/KiCAD-MCP-Server-Remixed/dist/index.js"],
      "env": {
        "PYTHONPATH": "/usr/lib/kicad/lib/python3/dist-packages"
      }
    }
  }
}
```

Adjust `PYTHONPATH` for your platform:
- **Linux:** `/usr/lib/kicad/lib/python3/dist-packages`
- **macOS:** `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages`
- **Windows:** `C:\Program Files\KiCad\9.0\lib\python3\dist-packages`

Works with Claude Desktop, Claude Code, and Cline.

### Development

```bash
npm run build          # compile TypeScript
npm run build:watch    # watch mode
npm run lint           # check TS + Python (read-only)
npm run format         # auto-format TS + Python
pytest tests/ -v       # run Python tests
```

## Known limitations

- `kicad_interface.py` is a 7000+ line file with 75+ handlers. It works, but it's not pretty.
- Pin math is duplicated in 10 places. All 10 are now correct, but it's fragile.
- Test coverage is minimal — 4 test files total, none for the core schematic logic.
- `batch_connect_to_net` still does N file read/write cycles (needs fresh pin positions after each placement).
- Label bounding boxes are estimated (0.75mm/char). Non-default fonts will be inaccurate.
- The ERC coordinate auto-detection heuristic can false-positive on schematics with violations near the origin.

## Upstream

Original project: [mixelpixx/KiCAD-MCP-Server](https://github.com/mixelpixx/KiCAD-MCP-Server)

This fork diverges from upstream's `main` branch. The upstream project continues to evolve independently — check both repos for the latest features.

## License

MIT — same as upstream.

---

*This remix is almost as fire as my latest mixtape.*
