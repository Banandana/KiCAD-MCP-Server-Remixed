/**
 * Routing tools for KiCAD MCP server
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { logger } from "../logger.js";

export function registerRoutingTools(
  server: McpServer,
  callKicadScript: Function,
) {
  // Add net tool
  server.tool(
    "add_net",
    "Create a new net on the PCB",
    {
      name: z.string().describe("Net name"),
      netClass: z.string().optional().describe("Net class name"),
    },
    async (args: { name: string; netClass?: string }) => {
      const result = await callKicadScript("add_net", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Route trace tool
  server.tool(
    "route_trace",
    "Route a trace segment between two XY points on a fixed layer. WARNING: Does NOT handle layer changes — if start and end are on different copper layers, use route_pad_to_pad instead, which automatically inserts a via.",
    {
      start: z
        .object({
          x: z.number(),
          y: z.number(),
          unit: z.string().optional(),
        })
        .describe("Start position"),
      end: z
        .object({
          x: z.number(),
          y: z.number(),
          unit: z.string().optional(),
        })
        .describe("End position"),
      layer: z.string().describe("PCB layer"),
      width: z.number().describe("Trace width in mm"),
      net: z.string().describe("Net name"),
    },
    async (args: any) => {
      const result = await callKicadScript("route_trace", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Add via tool
  server.tool(
    "add_via",
    "Add a via to the PCB",
    {
      position: z
        .object({
          x: z.number(),
          y: z.number(),
          unit: z.string().optional(),
        })
        .describe("Via position"),
      net: z.string().describe("Net name"),
      viaType: z
        .string()
        .optional()
        .describe("Via type (through, blind, buried)"),
    },
    async (args: any) => {
      const result = await callKicadScript("add_via", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Add copper pour tool
  server.tool(
    "add_copper_pour",
    "Add a copper pour (ground/power plane) to the PCB",
    {
      layer: z.string().describe("PCB layer"),
      net: z.string().describe("Net name"),
      clearance: z.number().optional().describe("Clearance in mm"),
      outline: z
        .array(z.object({ x: z.number(), y: z.number() }))
        .optional()
        .describe(
          "Array of {x, y} points defining the pour boundary. If omitted, the board outline is used.",
        ),
    },
    async (args: any) => {
      const result = await callKicadScript("add_copper_pour", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Delete trace tool
  server.tool(
    "delete_trace",
    "Delete traces from the PCB. Can delete by UUID, position, or bulk-delete all traces on a net.",
    {
      traceUuid: z
        .string()
        .optional()
        .describe("UUID of a specific trace to delete"),
      position: z
        .object({
          x: z.number(),
          y: z.number(),
          unit: z.enum(["mm", "inch"]).optional(),
        })
        .optional()
        .describe("Delete trace nearest to this position"),
      net: z
        .string()
        .optional()
        .describe("Delete all traces on this net (bulk delete)"),
      layer: z
        .string()
        .optional()
        .describe("Filter by layer when using net-based deletion"),
      includeVias: z
        .boolean()
        .optional()
        .describe("Include vias in net-based deletion"),
    },
    async (args: any) => {
      const result = await callKicadScript("delete_trace", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Query traces tool
  server.tool(
    "query_traces",
    "Query traces on the board with optional filters by net, layer, or bounding box.",
    {
      net: z.string().optional().describe("Filter by net name"),
      layer: z.string().optional().describe("Filter by layer name"),
      boundingBox: z
        .object({
          x1: z.number(),
          y1: z.number(),
          x2: z.number(),
          y2: z.number(),
          unit: z.enum(["mm", "inch"]).optional(),
        })
        .optional()
        .describe("Filter by bounding box region"),
      unit: z.enum(["mm", "inch"]).optional().describe("Unit for coordinates"),
    },
    async (args: any) => {
      const result = await callKicadScript("query_traces", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Get nets list tool
  server.tool(
    "get_nets_list",
    "Get a list of all nets in the PCB with optional statistics.",
    {
      includeStats: z
        .boolean()
        .optional()
        .describe("Include statistics (track count, total length, etc.)"),
      unit: z
        .enum(["mm", "inch"])
        .optional()
        .describe("Unit for length measurements"),
    },
    async (args: any) => {
      const result = await callKicadScript("get_nets_list", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Modify trace tool
  server.tool(
    "modify_trace",
    "Modify an existing trace (change width, layer, or net).",
    {
      traceUuid: z.string().describe("UUID of the trace to modify"),
      width: z.number().optional().describe("New trace width in mm"),
      layer: z.string().optional().describe("New layer name"),
      net: z.string().optional().describe("New net name"),
    },
    async (args: any) => {
      const result = await callKicadScript("modify_trace", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Create netclass tool
  server.tool(
    "create_netclass",
    "Create a new net class with custom design rules.",
    {
      name: z.string().describe("Net class name"),
      traceWidth: z.number().optional().describe("Default trace width in mm"),
      clearance: z.number().optional().describe("Clearance in mm"),
      viaDiameter: z.number().optional().describe("Via diameter in mm"),
      viaDrill: z.number().optional().describe("Via drill size in mm"),
    },
    async (args: any) => {
      const result = await callKicadScript("create_netclass", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  server.tool(
    "assign_nets_to_netclass",
    `Assign one or more nets to a named netclass. The netclass must already exist.
Example: assign_nets_to_netclass({ netclass: "Power_12V", nets: ["+12V_IN", "12V_BUCK_IN"] })`,
    {
      netclass: z.string().describe("Name of the target netclass (must exist)"),
      nets: z.array(z.string()).describe("Array of net names to assign to this netclass"),
    },
    async (args) => {
      const result = await callKicadScript("assign_nets_to_netclass", args);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  server.tool(
    "get_netclass_list",
    "Query all existing netclasses with their settings (trace width, clearance, via size) and assigned nets/patterns.",
    {},
    async () => {
      const result = await callKicadScript("get_netclass_list", {});
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  server.tool(
    "edit_netclass",
    "Modify an existing netclass's properties (trace width, clearance, via size, etc.).",
    {
      name: z.string().describe("Netclass name to modify"),
      trackWidth: z.number().optional().describe("New trace width in mm"),
      clearance: z.number().optional().describe("New clearance in mm"),
      viaDiameter: z.number().optional().describe("New via diameter in mm"),
      viaDrill: z.number().optional().describe("New via drill in mm"),
    },
    async (args) => {
      const result = await callKicadScript("edit_netclass", args);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  server.tool(
    "delete_netclass",
    "Remove a netclass. Nets assigned to it revert to Default class. Cannot delete 'Default'.",
    {
      name: z.string().describe("Netclass name to delete"),
    },
    async (args) => {
      const result = await callKicadScript("delete_netclass", args);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  server.tool(
    "get_net_to_netclass_map",
    "Quick dump of which nets are in which netclass. Faster than parsing the full nets list.",
    {},
    async () => {
      const result = await callKicadScript("get_net_to_netclass_map", {});
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  server.tool(
    "set_netclass_patterns",
    `Set pattern-based netclass assignment rules. Patterns use wildcards (*) to match
multiple nets. E.g., "+12V*" matches +12V_IN, +12V_FILT, etc.
Example: set_netclass_patterns({ netclass: "Power_12V", patterns: ["+12V*", "VIN_*"] })`,
    {
      netclass: z.string().describe("Name of the target netclass (must exist)"),
      patterns: z.array(z.string()).describe("Array of net name patterns (supports * wildcard)"),
    },
    async (args) => {
      const result = await callKicadScript("set_netclass_patterns", args);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  // ------------------------------------------------------
  // Resize Vias Tool
  // ------------------------------------------------------
  server.tool(
    "resize_vias",
    `Batch resize vias on the board. If oldDrill/oldDiameter are provided, only vias
matching those exact sizes are resized. Otherwise ALL vias are resized.

Example: resize all 0.15mm drill / 0.25mm pad vias to 0.2mm drill / 0.45mm pad:
  resize_vias({ oldDrill: 0.15, oldDiameter: 0.25, drill: 0.2, diameter: 0.45 })`,
    {
      drill: z.number().optional().describe("New drill size in mm"),
      diameter: z.number().optional().describe("New via diameter (pad size) in mm"),
      oldDrill: z.number().optional().describe("Only resize vias with this drill size (mm). Omit to match all."),
      oldDiameter: z.number().optional().describe("Only resize vias with this diameter (mm). Omit to match all."),
    },
    async (args: { drill?: number; diameter?: number; oldDrill?: number; oldDiameter?: number }) => {
      logger.debug("Resizing vias");
      const result = await callKicadScript("resize_vias", args);
      if (result.success) {
        return {
          content: [{
            type: "text" as const,
            text: `Resized ${result.resized} of ${result.total_vias} vias → drill=${result.new_drill_mm ?? "unchanged"}mm, diameter=${result.new_diameter_mm ?? "unchanged"}mm`,
          }],
        };
      }
      return {
        content: [{ type: "text" as const, text: `Failed: ${result.message}` }],
      };
    },
  );

  server.tool(
  "resize_traces",
  `Batch resize traces on the board. If oldWidth is provided, only traces matching
that exact width are resized. Otherwise ALL traces are resized. Use minWidth to only
resize traces below a threshold (e.g., bring all sub-5mil traces up to 5mil).

Example: resize all 4mil traces to 5mil:
  resize_traces({ oldWidth: 0.1016, width: 0.127 })
Example: resize all traces below 5mil to 5mil:
  resize_traces({ minWidth: 0.127, width: 0.127 })`,
  {
    width: z.number().describe("New trace width in mm"),
    oldWidth: z.number().optional().describe("Only resize traces with this exact width (mm). Omit to match all."),
    minWidth: z.number().optional().describe("Only resize traces below this width (mm). Cannot combine with oldWidth."),
    net: z.string().optional().describe("Only resize traces on this net name. Omit for all nets."),
  },
  async (args) => {
    logger.debug("Resizing traces");
    const result = await callKicadScript("resize_traces", args);
    if (result.success) {
      return {
        content: [{
          type: "text" as const,
          text: `Resized ${result.resized} of ${result.total_traces} traces → width=${result.new_width_mm}mm`,
        }],
      };
    }
    return {
      content: [{ type: "text" as const, text: `Failed: ${result.message}` }],
    };
  },
);

  server.tool(
    "get_trace_statistics",
    `Get trace width and via drill size distribution for the board. Returns counts
grouped by width/drill size, sorted by count descending. Use to identify DRC
violations (e.g., traces below manufacturer minimum).`,
    {
      belowWidth: z.number().optional().describe("If set, only count traces below this width (mm). Useful for finding DRC violations."),
    },
    async (args) => {
      logger.debug("Getting trace statistics");
      const result = await callKicadScript("get_trace_statistics", args);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  // Route differential pair tool
  server.tool(
    "route_differential_pair",
    "Route a differential pair between two sets of points.",
    {
      positivePad: z
        .object({
          reference: z.string(),
          pad: z.string(),
        })
        .describe("Positive pad (component and pad number)"),
      negativePad: z
        .object({
          reference: z.string(),
          pad: z.string(),
        })
        .describe("Negative pad (component and pad number)"),
      layer: z.string().describe("PCB layer"),
      width: z.number().describe("Trace width in mm"),
      gap: z.number().describe("Gap between traces in mm"),
      positiveNet: z.string().describe("Positive net name"),
      negativeNet: z.string().describe("Negative net name"),
    },
    async (args: any) => {
      const result = await callKicadScript("route_differential_pair", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Refill zones tool
  server.tool(
    "refill_zones",
    "Refill all copper zones on the board. WARNING: SWIG path has known segfault risk (see KNOWN_ISSUES.md). Prefer using IPC backend (KiCAD open) or triggering zone fill via KiCAD UI instead.",
    {},
    async (args: any) => {
      const result = await callKicadScript("refill_zones", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  // Route pad to pad tool
  server.tool(
    "route_pad_to_pad",
    "PREFERRED tool for pad-to-pad routing. Looks up pad positions automatically, detects the net from the pad, and — critically — if the two pads are on different copper layers (e.g. J1 on F.Cu and J2 on B.Cu) automatically inserts a via at the midpoint so the connection is complete. Always use this instead of route_trace when routing between named component pads.",
    {
      fromRef: z.string().describe("Reference of the source component (e.g. 'U2')"),
      fromPad: z.union([z.string(), z.number()]).describe("Pad number on the source component (e.g. '6' or 6)"),
      toRef: z.string().describe("Reference of the target component (e.g. 'U1')"),
      toPad: z.union([z.string(), z.number()]).describe("Pad number on the target component (e.g. '15' or 15)"),
      layer: z.string().optional().describe("PCB layer (default: F.Cu)"),
      width: z.number().optional().describe("Trace width in mm (default: board default)"),
      net: z.string().optional().describe("Net name override (default: auto-detected from pad)"),
    },
    async (args: any) => {
      const result = await callKicadScript("route_pad_to_pad", args);
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      };
    },
  );

  server.tool(
    "assign_nets_to_layer",
    `Move all existing traces for specified nets to a target layer. Useful for 2-layer
boards to organize power on B.Cu and signals on F.Cu.
Note: This moves EXISTING traces only. Future routing must be done on the correct layer manually.`,
    {
      layer: z.string().describe("Target layer name: 'F.Cu' or 'B.Cu'"),
      nets: z.array(z.string()).describe("Array of net names to move to this layer"),
    },
    async (args) => {
      const result = await callKicadScript("assign_nets_to_layer", args);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  server.tool(
    "set_copper_pour_settings",
    `Configure fill settings for a copper pour zone. Identify the zone by net name.
If multiple zones exist on the same net, all are modified.`,
    {
      net: z.string().describe("Net name of the copper pour zone (e.g., 'GND')"),
      clearance: z.number().optional().describe("Zone clearance in mm"),
      minWidth: z.number().optional().describe("Minimum copper width in zone fill (mm)"),
      thermalReliefGap: z.number().optional().describe("Thermal relief gap (mm)"),
      thermalReliefSpokeWidth: z.number().optional().describe("Thermal relief spoke width (mm)"),
      priority: z.number().optional().describe("Zone fill priority (0 = lowest)"),
    },
    async (args) => {
      const result = await callKicadScript("set_copper_pour_settings", args);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  server.tool(
    "get_unrouted_connections",
    `List all unrouted connections (ratsnest). Returns pad pairs that need traces.
Essential for tracking routing progress.`,
    {
      net: z.string().optional().describe("Filter by net name. Omit for all nets."),
      limit: z.number().optional().describe("Maximum entries to return (default: 100)"),
    },
    async (args) => {
      const result = await callKicadScript("get_unrouted_connections", args);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  server.tool(
    "get_board_statistics",
    "Get board routing statistics: routed vs unrouted count, total trace length by layer, via count by type.",
    {},
    async () => {
      const result = await callKicadScript("get_board_statistics", {});
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    },
  );

  // Copy routing pattern tool
  server.tool(
    "copy_routing_pattern",
    "Copy routing pattern (traces and vias) from a group of source components to a matching group of target components. The offset is calculated automatically from the position difference between the first source and first target component. Useful for replicating routing between identical circuit blocks.",
    {
      sourceRefs: z
        .array(z.string())
        .describe("References of the source components (e.g. ['U1', 'R1', 'C1'])"),
      targetRefs: z
        .array(z.string())
        .describe(
          "References of the target components in same order as sourceRefs (e.g. ['U2', 'R2', 'C2'])",
        ),
      includeVias: z
        .boolean()
        .optional()
        .describe("Also copy vias (default: true)"),
      traceWidth: z
        .number()
        .optional()
        .describe("Override trace width in mm (default: keep original width)"),
    },
    async (args: any) => {
      const result = await callKicadScript("copy_routing_pattern", args);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );
}
