"""
Board outline command implementations for KiCAD interface
"""

import pcbnew
import logging
import math
from typing import Dict, Any, Optional

logger = logging.getLogger("kicad_interface")


class BoardOutlineCommands:
    """Handles board outline operations"""

    def __init__(self, board: Optional[pcbnew.BOARD] = None):
        """Initialize with optional board instance"""
        self.board = board

    def add_board_outline(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add a board outline to the PCB"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            # Claude sends dimensions nested inside a "params" key:
            # {"shape": "rectangle", "params": {"x": 0, "y": 0, "width": 38, ...}}
            # Unwrap the inner dict if present so we read dimensions from the right level.
            inner = params.get("params", params)

            shape = params.get("shape", "rectangle")
            width = inner.get("width")
            height = inner.get("height")
            radius = inner.get("radius")
            # Accept both "cornerRadius" and "radius" regardless of shape name.
            # The AI often sends shape="rectangle" with radius=2.5 — we treat that as rounded_rectangle.
            corner_radius = inner.get("cornerRadius", inner.get("radius", 0))
            if shape == "rectangle" and corner_radius > 0:
                shape = "rounded_rectangle"
            points = inner.get("points", [])
            unit = inner.get("unit", "mm")

            # Position: accept top-left corner (x/y) or center (centerX/centerY).
            # Default: top-left at (0,0) so the board occupies positive coordinate space
            # and is consistent with component placement coordinates.
            x = inner.get("x")
            y = inner.get("y")
            if x is not None or y is not None:
                ox = x if x is not None else 0.0
                oy = y if y is not None else 0.0
                center_x = ox + (width or 0) / 2.0
                center_y = oy + (height or 0) / 2.0
            else:
                raw_cx = inner.get("centerX")
                raw_cy = inner.get("centerY")
                if raw_cx is not None or raw_cy is not None:
                    center_x = raw_cx if raw_cx is not None else 0.0
                    center_y = raw_cy if raw_cy is not None else 0.0
                else:
                    # No position given → place top-left at (0,0)
                    center_x = (width or 0) / 2.0
                    center_y = (height or 0) / 2.0

            if shape not in ["rectangle", "circle", "polygon", "rounded_rectangle"]:
                return {
                    "success": False,
                    "message": "Invalid shape",
                    "errorDetails": f"Shape '{shape}' not supported",
                }

            # Convert to internal units (nanometers)
            scale = 1000000 if unit == "mm" else 25400000  # mm or inch to nm

            # Create drawing for edge cuts
            edge_layer = self.board.GetLayerID("Edge.Cuts")

            if shape == "rectangle":
                if width is None or height is None:
                    return {
                        "success": False,
                        "message": "Missing dimensions",
                        "errorDetails": "Both width and height are required for rectangle",
                    }

                width_nm = int(width * scale)
                height_nm = int(height * scale)
                center_x_nm = int(center_x * scale)
                center_y_nm = int(center_y * scale)

                # Create rectangle
                top_left = pcbnew.VECTOR2I(
                    center_x_nm - width_nm // 2, center_y_nm - height_nm // 2
                )
                top_right = pcbnew.VECTOR2I(
                    center_x_nm + width_nm // 2, center_y_nm - height_nm // 2
                )
                bottom_right = pcbnew.VECTOR2I(
                    center_x_nm + width_nm // 2, center_y_nm + height_nm // 2
                )
                bottom_left = pcbnew.VECTOR2I(
                    center_x_nm - width_nm // 2, center_y_nm + height_nm // 2
                )

                # Add lines for rectangle
                self._add_edge_line(top_left, top_right, edge_layer)
                self._add_edge_line(top_right, bottom_right, edge_layer)
                self._add_edge_line(bottom_right, bottom_left, edge_layer)
                self._add_edge_line(bottom_left, top_left, edge_layer)

            elif shape == "rounded_rectangle":
                if width is None or height is None:
                    return {
                        "success": False,
                        "message": "Missing dimensions",
                        "errorDetails": "Both width and height are required for rounded rectangle",
                    }

                width_nm = int(width * scale)
                height_nm = int(height * scale)
                center_x_nm = int(center_x * scale)
                center_y_nm = int(center_y * scale)
                corner_radius_nm = int(corner_radius * scale)

                # Create rounded rectangle
                self._add_rounded_rect(
                    center_x_nm,
                    center_y_nm,
                    width_nm,
                    height_nm,
                    corner_radius_nm,
                    edge_layer,
                )

            elif shape == "circle":
                if radius is None:
                    return {
                        "success": False,
                        "message": "Missing radius",
                        "errorDetails": "Radius is required for circle",
                    }

                center_x_nm = int(center_x * scale)
                center_y_nm = int(center_y * scale)
                radius_nm = int(radius * scale)

                # Create circle
                circle = pcbnew.PCB_SHAPE(self.board)
                circle.SetShape(pcbnew.SHAPE_T_CIRCLE)
                circle.SetCenter(pcbnew.VECTOR2I(center_x_nm, center_y_nm))
                circle.SetEnd(pcbnew.VECTOR2I(center_x_nm + radius_nm, center_y_nm))
                circle.SetLayer(edge_layer)
                circle.SetWidth(0)  # Zero width for edge cuts
                self.board.Add(circle)

            elif shape == "polygon":
                if not points or len(points) < 3:
                    return {
                        "success": False,
                        "message": "Missing points",
                        "errorDetails": "At least 3 points are required for polygon",
                    }

                # Convert points to nm
                polygon_points = []
                for point in points:
                    x_nm = int(point["x"] * scale)
                    y_nm = int(point["y"] * scale)
                    polygon_points.append(pcbnew.VECTOR2I(x_nm, y_nm))

                # Add lines for polygon
                for i in range(len(polygon_points)):
                    self._add_edge_line(
                        polygon_points[i],
                        polygon_points[(i + 1) % len(polygon_points)],
                        edge_layer,
                    )

            return {
                "success": True,
                "message": f"Added board outline: {shape}",
                "outline": {
                    "shape": shape,
                    "width": width,
                    "height": height,
                    "center": {"x": center_x, "y": center_y, "unit": unit},
                    "radius": radius,
                    "cornerRadius": corner_radius,
                    "points": points,
                },
            }

        except Exception as e:
            logger.error(f"Error adding board outline: {str(e)}")
            return {
                "success": False,
                "message": "Failed to add board outline",
                "errorDetails": str(e),
            }

    def _get_edge_cuts_shapes(self):
        """Collect all PCB_SHAPE objects on the Edge.Cuts layer."""
        edge_layer = self.board.GetLayerID("Edge.Cuts")
        shapes = []
        for drawing in self.board.GetDrawings():
            if hasattr(drawing, "GetLayer") and drawing.GetLayer() == edge_layer:
                shapes.append(drawing)
        return shapes

    def _shape_endpoints(self, shape):
        """Return (start, end) as (x, y) tuples in nm for a PCB_SHAPE.
        For circles, returns None (they are self-contained closed shapes)."""
        shape_type = shape.GetShape()
        # Circle = self-contained, no endpoints to chain
        if shape_type == pcbnew.SHAPE_T_CIRCLE:
            return None
        start = shape.GetStart()
        end = shape.GetEnd()
        return ((start.x, start.y), (end.x, end.y))

    def _find_connected_chains(self, shapes):
        """Group Edge.Cuts shapes into connected chains using endpoint matching.
        Returns list of lists, where each inner list is a group of connected shapes.
        Circles are each their own group."""
        # Tolerance for endpoint matching (100nm = 0.0001mm)
        TOL = 100

        def close(p1, p2):
            return abs(p1[0] - p2[0]) <= TOL and abs(p1[1] - p2[1]) <= TOL

        groups = []  # list of (set_of_indices, set_of_endpoint_tuples)
        shape_endpoints = []  # index -> endpoints or None

        for i, s in enumerate(shapes):
            ep = self._shape_endpoints(s)
            shape_endpoints.append(ep)
            if ep is None:
                # Circle: standalone group
                groups.append(({i}, set()))
            else:
                # Try to attach to an existing group
                merged = False
                for g_indices, g_points in groups:
                    # Check if either endpoint matches any group endpoint
                    for gp in list(g_points):
                        if close(ep[0], gp) or close(ep[1], gp):
                            g_indices.add(i)
                            g_points.add(ep[0])
                            g_points.add(ep[1])
                            merged = True
                            break
                    if merged:
                        break
                if not merged:
                    groups.append(({i}, {ep[0], ep[1]}))

        # Merge groups that share endpoints (multi-pass until stable)
        changed = True
        while changed:
            changed = False
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    gi, gp_i = groups[i]
                    gj, gp_j = groups[j]
                    if not gi or not gj:
                        continue
                    # Check if any endpoints are close
                    overlap = False
                    for pi in gp_i:
                        for pj in gp_j:
                            if close(pi, pj):
                                overlap = True
                                break
                        if overlap:
                            break
                    if overlap:
                        gi.update(gj)
                        gp_i.update(gp_j)
                        gj.clear()
                        gp_j.clear()
                        changed = True

        return [[shapes[i] for i in g_indices] for g_indices, _ in groups if g_indices]

    def _chain_bbox_area(self, chain):
        """Compute bounding box area (in nm^2) for a list of shapes."""
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for s in chain:
            shape_type = s.GetShape()
            if shape_type == pcbnew.SHAPE_T_CIRCLE:
                center = s.GetCenter()
                end = s.GetEnd()
                r = int(math.sqrt((end.x - center.x) ** 2 + (end.y - center.y) ** 2))
                min_x = min(min_x, center.x - r)
                min_y = min(min_y, center.y - r)
                max_x = max(max_x, center.x + r)
                max_y = max(max_y, center.y + r)
            else:
                start = s.GetStart()
                end = s.GetEnd()
                min_x = min(min_x, start.x, end.x)
                min_y = min(min_y, start.y, end.y)
                max_x = max(max_x, start.x, end.x)
                max_y = max(max_y, start.y, end.y)
                # For arcs, also check the center offset
                if shape_type == pcbnew.SHAPE_T_ARC:
                    center = s.GetCenter()
                    r = int(math.sqrt(
                        (start.x - center.x) ** 2 + (start.y - center.y) ** 2
                    ))
                    min_x = min(min_x, center.x - r)
                    min_y = min(min_y, center.y - r)
                    max_x = max(max_x, center.x + r)
                    max_y = max(max_y, center.y + r)
        return (max_x - min_x) * (max_y - min_y)

    def delete_board_outline(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove the board outline from Edge.Cuts.

        By default (deleteAll=false), only removes the outer outline — the connected
        shape chain with the largest bounding box. Internal cutouts (smaller closed
        loops for mounting holes, USB slots, etc.) are preserved.

        Set deleteAll=true to remove ALL Edge.Cuts shapes including internal cutouts.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            delete_all = params.get("deleteAll", False)
            edge_shapes = self._get_edge_cuts_shapes()

            if not edge_shapes:
                return {
                    "success": True,
                    "message": "No board outline found to delete",
                    "deleted_count": 0,
                }

            if delete_all:
                shapes_to_remove = edge_shapes
            else:
                # Find connected chains and remove only the largest one (outer outline)
                chains = self._find_connected_chains(edge_shapes)
                if not chains:
                    return {
                        "success": True,
                        "message": "No board outline found to delete",
                        "deleted_count": 0,
                    }
                # Pick chain with largest bbox area = outer outline
                largest = max(chains, key=self._chain_bbox_area)
                shapes_to_remove = largest

            for shape in shapes_to_remove:
                self.board.Remove(shape)

            preserved = len(edge_shapes) - len(shapes_to_remove)
            msg = f"Deleted board outline ({len(shapes_to_remove)} shapes removed)"
            if preserved > 0:
                msg += f", preserved {preserved} internal cutout shapes"

            return {
                "success": True,
                "message": msg,
                "deleted_count": len(shapes_to_remove),
                "preserved_count": preserved,
            }

        except Exception as e:
            logger.error(f"Error deleting board outline: {str(e)}")
            return {
                "success": False,
                "message": "Failed to delete board outline",
                "errorDetails": str(e),
            }

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

    def add_mounting_hole(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add a mounting hole to the PCB"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            position = params.get("position")
            diameter = params.get("diameter")
            pad_diameter = params.get("padDiameter")
            plated = params.get("plated", False)

            if not position or not diameter:
                return {
                    "success": False,
                    "message": "Missing parameters",
                    "errorDetails": "position and diameter are required",
                }

            # Convert to internal units (nanometers)
            scale = (
                1000000 if position.get("unit", "mm") == "mm" else 25400000
            )  # mm or inch to nm
            x_nm = int(position["x"] * scale)
            y_nm = int(position["y"] * scale)
            diameter_nm = int(diameter * scale)
            pad_diameter_nm = (
                int(pad_diameter * scale) if pad_diameter else diameter_nm + scale
            )  # 1mm larger by default

            # Create footprint for mounting hole with unique reference
            existing_mh = [
                fp.GetReference()
                for fp in self.board.GetFootprints()
                if fp.GetReference().startswith("MH")
            ]
            next_num = 1
            while f"MH{next_num}" in existing_mh:
                next_num += 1

            module = pcbnew.FOOTPRINT(self.board)
            module.SetReference(f"MH{next_num}")
            module.SetValue(f"MountingHole_{diameter}mm")

            # Create the pad for the hole
            pad = pcbnew.PAD(module)
            pad.SetNumber(1)
            pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetAttribute(
                pcbnew.PAD_ATTRIB_PTH if plated else pcbnew.PAD_ATTRIB_NPTH
            )
            pad.SetSize(pcbnew.VECTOR2I(pad_diameter_nm, pad_diameter_nm))
            pad.SetDrillSize(pcbnew.VECTOR2I(diameter_nm, diameter_nm))
            pad.SetPosition(pcbnew.VECTOR2I(0, 0))  # Position relative to module
            module.Add(pad)

            # Position the mounting hole
            module.SetPosition(pcbnew.VECTOR2I(x_nm, y_nm))

            # Add to board
            self.board.Add(module)

            return {
                "success": True,
                "message": "Added mounting hole",
                "mountingHole": {
                    "position": position,
                    "diameter": diameter,
                    "padDiameter": pad_diameter or diameter + 1,
                    "plated": plated,
                },
            }

        except Exception as e:
            logger.error(f"Error adding mounting hole: {str(e)}")
            return {
                "success": False,
                "message": "Failed to add mounting hole",
                "errorDetails": str(e),
            }

    def add_text(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add text annotation to the PCB"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            text = params.get("text")
            position = params.get("position")
            layer = params.get("layer", "F.SilkS")
            size = params.get("size", 1.0)
            thickness = params.get("thickness", 0.15)
            rotation = params.get("rotation", 0)
            mirror = params.get("mirror", False)

            if not text or not position:
                return {
                    "success": False,
                    "message": "Missing parameters",
                    "errorDetails": "text and position are required",
                }

            # Convert to internal units (nanometers)
            scale = (
                1000000 if position.get("unit", "mm") == "mm" else 25400000
            )  # mm or inch to nm
            x_nm = int(position["x"] * scale)
            y_nm = int(position["y"] * scale)
            size_nm = int(size * scale)
            thickness_nm = int(thickness * scale)

            # Get layer ID
            layer_id = self.board.GetLayerID(layer)
            if layer_id < 0:
                return {
                    "success": False,
                    "message": "Invalid layer",
                    "errorDetails": f"Layer '{layer}' does not exist",
                }

            # Create text
            pcb_text = pcbnew.PCB_TEXT(self.board)
            pcb_text.SetText(text)
            pcb_text.SetPosition(pcbnew.VECTOR2I(x_nm, y_nm))
            pcb_text.SetLayer(layer_id)
            pcb_text.SetTextSize(pcbnew.VECTOR2I(size_nm, size_nm))
            pcb_text.SetTextThickness(thickness_nm)

            # Set rotation angle - KiCAD 9.0 uses EDA_ANGLE
            try:
                # Try KiCAD 9.0+ API (EDA_ANGLE)
                angle = pcbnew.EDA_ANGLE(rotation, pcbnew.DEGREES_T)
                pcb_text.SetTextAngle(angle)
            except (AttributeError, TypeError):
                # Fall back to older API (decidegrees as integer)
                pcb_text.SetTextAngle(int(rotation * 10))

            pcb_text.SetMirrored(mirror)

            # Add to board
            self.board.Add(pcb_text)

            return {
                "success": True,
                "message": "Added text annotation",
                "text": {
                    "text": text,
                    "position": position,
                    "layer": layer,
                    "size": size,
                    "thickness": thickness,
                    "rotation": rotation,
                    "mirror": mirror,
                },
            }

        except Exception as e:
            logger.error(f"Error adding text: {str(e)}")
            return {
                "success": False,
                "message": "Failed to add text",
                "errorDetails": str(e),
            }

    def _add_edge_line(
        self, start: pcbnew.VECTOR2I, end: pcbnew.VECTOR2I, layer: int
    ) -> None:
        """Add a line to the edge cuts layer"""
        line = pcbnew.PCB_SHAPE(self.board)
        line.SetShape(pcbnew.SHAPE_T_SEGMENT)
        line.SetStart(start)
        line.SetEnd(end)
        line.SetLayer(layer)
        line.SetWidth(0)  # Zero width for edge cuts
        self.board.Add(line)

    def _add_rounded_rect(
        self,
        center_x_nm: int,
        center_y_nm: int,
        width_nm: int,
        height_nm: int,
        radius_nm: int,
        layer: int,
    ) -> None:
        """Add a rounded rectangle to the edge cuts layer"""
        if radius_nm <= 0:
            # If no radius, create regular rectangle
            top_left = pcbnew.VECTOR2I(
                center_x_nm - width_nm // 2, center_y_nm - height_nm // 2
            )
            top_right = pcbnew.VECTOR2I(
                center_x_nm + width_nm // 2, center_y_nm - height_nm // 2
            )
            bottom_right = pcbnew.VECTOR2I(
                center_x_nm + width_nm // 2, center_y_nm + height_nm // 2
            )
            bottom_left = pcbnew.VECTOR2I(
                center_x_nm - width_nm // 2, center_y_nm + height_nm // 2
            )

            self._add_edge_line(top_left, top_right, layer)
            self._add_edge_line(top_right, bottom_right, layer)
            self._add_edge_line(bottom_right, bottom_left, layer)
            self._add_edge_line(bottom_left, top_left, layer)
            return

        # Calculate corner centers
        half_width = width_nm // 2
        half_height = height_nm // 2

        # Ensure radius is not larger than half the smallest dimension
        max_radius = min(half_width, half_height)
        if radius_nm > max_radius:
            radius_nm = max_radius

        # Calculate corner centers
        top_left_center = pcbnew.VECTOR2I(
            center_x_nm - half_width + radius_nm, center_y_nm - half_height + radius_nm
        )
        top_right_center = pcbnew.VECTOR2I(
            center_x_nm + half_width - radius_nm, center_y_nm - half_height + radius_nm
        )
        bottom_right_center = pcbnew.VECTOR2I(
            center_x_nm + half_width - radius_nm, center_y_nm + half_height - radius_nm
        )
        bottom_left_center = pcbnew.VECTOR2I(
            center_x_nm - half_width + radius_nm, center_y_nm + half_height - radius_nm
        )

        # Add arcs for corners
        self._add_corner_arc(top_left_center, radius_nm, 180, 270, layer)
        self._add_corner_arc(top_right_center, radius_nm, 270, 0, layer)
        self._add_corner_arc(bottom_right_center, radius_nm, 0, 90, layer)
        self._add_corner_arc(bottom_left_center, radius_nm, 90, 180, layer)

        # Add lines for straight edges
        # Top edge
        self._add_edge_line(
            pcbnew.VECTOR2I(top_left_center.x, top_left_center.y - radius_nm),
            pcbnew.VECTOR2I(top_right_center.x, top_right_center.y - radius_nm),
            layer,
        )
        # Right edge
        self._add_edge_line(
            pcbnew.VECTOR2I(top_right_center.x + radius_nm, top_right_center.y),
            pcbnew.VECTOR2I(bottom_right_center.x + radius_nm, bottom_right_center.y),
            layer,
        )
        # Bottom edge
        self._add_edge_line(
            pcbnew.VECTOR2I(bottom_right_center.x, bottom_right_center.y + radius_nm),
            pcbnew.VECTOR2I(bottom_left_center.x, bottom_left_center.y + radius_nm),
            layer,
        )
        # Left edge
        self._add_edge_line(
            pcbnew.VECTOR2I(bottom_left_center.x - radius_nm, bottom_left_center.y),
            pcbnew.VECTOR2I(top_left_center.x - radius_nm, top_left_center.y),
            layer,
        )

    def _add_corner_arc(
        self,
        center: pcbnew.VECTOR2I,
        radius: int,
        start_angle: float,
        end_angle: float,
        layer: int,
    ) -> None:
        """Add an arc for a rounded corner"""
        # Create arc for corner
        arc = pcbnew.PCB_SHAPE(self.board)
        arc.SetShape(pcbnew.SHAPE_T_ARC)
        arc.SetCenter(center)

        # Calculate start and end points
        start_x = center.x + int(radius * math.cos(math.radians(start_angle)))
        start_y = center.y + int(radius * math.sin(math.radians(start_angle)))
        end_x = center.x + int(radius * math.cos(math.radians(end_angle)))
        end_y = center.y + int(radius * math.sin(math.radians(end_angle)))

        arc.SetStart(pcbnew.VECTOR2I(start_x, start_y))
        arc.SetEnd(pcbnew.VECTOR2I(end_x, end_y))
        arc.SetLayer(layer)
        arc.SetWidth(0)  # Zero width for edge cuts
        self.board.Add(arc)
