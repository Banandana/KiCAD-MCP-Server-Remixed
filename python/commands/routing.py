"""
Routing-related command implementations for KiCAD interface
"""

import os
import pcbnew
import logging
import math
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("kicad_interface")


class RoutingCommands:
    """Handles routing-related KiCAD operations"""

    def __init__(self, board: Optional[pcbnew.BOARD] = None):
        """Initialize with optional board instance"""
        self.board = board

    def add_net(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new net to the PCB"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            name = params.get("name")
            net_class = params.get("class")

            if not name:
                return {
                    "success": False,
                    "message": "Missing net name",
                    "errorDetails": "name parameter is required",
                }

            # Create new net
            netinfo = self.board.GetNetInfo()
            nets_map = netinfo.NetsByName()
            if nets_map.has_key(name):
                net = nets_map[name]
            else:
                net = pcbnew.NETINFO_ITEM(self.board, name)
                self.board.Add(net)

            # Set net class if provided
            if net_class:
                ns = self.board.GetDesignSettings().m_NetSettings
                if ns.HasNetclass(net_class):
                    nc = ns.GetNetClassByName(net_class)
                    net.SetClass(nc)

            return {
                "success": True,
                "message": f"Added net: {name}",
                "net": {
                    "name": name,
                    "class": net_class if net_class else "Default",
                    "netcode": net.GetNetCode(),
                },
            }

        except Exception as e:
            logger.error(f"Error adding net: {str(e)}")
            return {
                "success": False,
                "message": "Failed to add net",
                "errorDetails": str(e),
            }

    def route_pad_to_pad(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Route a trace directly from one component pad to another.

        Looks up pad positions automatically, then creates a trace.
        Convenience wrapper around route_trace that eliminates the need
        for separate get_pad_position calls.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            from_ref = params.get("fromRef")
            from_pad = str(params.get("fromPad", ""))
            to_ref = params.get("toRef")
            to_pad = str(params.get("toPad", ""))
            layer = params.get("layer", "F.Cu")
            width = params.get("width")
            net = params.get("net")  # optional override

            if not from_ref or not from_pad or not to_ref or not to_pad:
                return {
                    "success": False,
                    "message": "Missing parameters",
                    "errorDetails": "fromRef, fromPad, toRef, toPad are all required",
                }

            scale = 1000000  # nm to mm

            # Find pads
            footprints = {fp.GetReference(): fp for fp in self.board.GetFootprints()}

            for ref in [from_ref, to_ref]:
                if ref not in footprints:
                    return {
                        "success": False,
                        "message": f"Component not found: {ref}",
                        "errorDetails": f"'{ref}' does not exist on the board",
                    }

            def find_pad(ref: str, pad_num: str):
                fp = footprints[ref]
                for pad in fp.Pads():
                    if pad.GetNumber() == pad_num:
                        return pad
                return None

            start_pad = find_pad(from_ref, from_pad)
            end_pad = find_pad(to_ref, to_pad)

            if not start_pad:
                return {
                    "success": False,
                    "message": f"Pad not found: {from_ref} pad {from_pad}",
                    "errorDetails": f"Check pad number for {from_ref}",
                }
            if not end_pad:
                return {
                    "success": False,
                    "message": f"Pad not found: {to_ref} pad {to_pad}",
                    "errorDetails": f"Check pad number for {to_ref}",
                }

            start_pos = start_pad.GetPosition()
            end_pos = end_pad.GetPosition()

            # Use net from start pad if not overridden
            if not net:
                net = start_pad.GetNetname() or end_pad.GetNetname() or ""

            # Detect if pads are on different copper layers → need via.
            # SMD pad.GetLayer() reports F.Cu even on flipped B.Cu footprints in
            # KiCAD 9 SWIG. Use footprint.GetLayer() instead — it always reflects
            # the actual placed layer after Flip().
            fp_start = footprints[from_ref]
            fp_end   = footprints[to_ref]
            start_layer = self.board.GetLayerName(fp_start.GetLayer())
            end_layer   = self.board.GetLayerName(fp_end.GetLayer())
            copper_layers = {"F.Cu", "B.Cu"}
            needs_via = (
                start_layer in copper_layers
                and end_layer in copper_layers
                and start_layer != end_layer
            )

            if needs_via:
                # Place via directly below the start pad (same X).
                # Using the geometric midpoint X causes all vias to stack at
                # the same X when pads are back-to-back mirrored (e.g. J1/J2
                # on F.Cu/B.Cu): midpoint is always the board center.
                via_x = start_pos.x / scale
                via_y = (start_pos.y + end_pos.y) / 2 / scale

                # Trace on start layer: start_pad → via
                r1 = self.route_trace({
                    "start": {"x": start_pos.x / scale, "y": start_pos.y / scale, "unit": "mm"},
                    "end":   {"x": via_x, "y": via_y, "unit": "mm"},
                    "layer": start_layer, "width": width, "net": net,
                })
                # Via connecting both layers
                self.add_via({
                    "position": {"x": via_x, "y": via_y, "unit": "mm"},
                    "net": net,
                    "from_layer": start_layer,
                    "to_layer": end_layer,
                })
                # Trace on end layer: via → end_pad
                r2 = self.route_trace({
                    "start": {"x": via_x, "y": via_y, "unit": "mm"},
                    "end":   {"x": end_pos.x / scale, "y": end_pos.y / scale, "unit": "mm"},
                    "layer": end_layer, "width": width, "net": net,
                })
                success = r1.get("success") and r2.get("success")
                result = {
                    "success": success,
                    "message": f"Routed {from_ref}.{from_pad} → via → {to_ref}.{to_pad} (net: {net}, via at {via_x:.2f},{via_y:.2f})",
                    "via_added": True,
                    "via_position": {"x": via_x, "y": via_y},
                }
            else:
                # Same layer — direct trace
                result = self.route_trace({
                    "start": {"x": start_pos.x / scale, "y": start_pos.y / scale, "unit": "mm"},
                    "end":   {"x": end_pos.x / scale, "y": end_pos.y / scale, "unit": "mm"},
                    "layer": layer if layer else start_layer,
                    "width": width, "net": net,
                })

            if result.get("success"):
                result["fromPad"] = {
                    "ref": from_ref, "pad": from_pad,
                    "x": start_pos.x / scale, "y": start_pos.y / scale,
                }
                result["toPad"] = {
                    "ref": to_ref, "pad": to_pad,
                    "x": end_pos.x / scale, "y": end_pos.y / scale,
                }

            return result

        except Exception as e:
            logger.error(f"Error in route_pad_to_pad: {str(e)}")
            return {
                "success": False,
                "message": "Failed to route pad to pad",
                "errorDetails": str(e),
            }

    def route_trace(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Route a trace between two points or pads"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            start = params.get("start")
            end = params.get("end")
            layer = params.get("layer", "F.Cu")
            width = params.get("width")
            net = params.get("net")
            via = params.get("via", False)

            if not start or not end:
                return {
                    "success": False,
                    "message": "Missing parameters",
                    "errorDetails": "start and end points are required",
                }

            # Get layer ID
            layer_id = self.board.GetLayerID(layer)
            if layer_id < 0:
                return {
                    "success": False,
                    "message": "Invalid layer",
                    "errorDetails": f"Layer '{layer}' does not exist",
                }

            # Get start point
            start_point = self._get_point(start)
            end_point = self._get_point(end)

            # Create track segment
            track = pcbnew.PCB_TRACK(self.board)
            track.SetStart(start_point)
            track.SetEnd(end_point)
            track.SetLayer(layer_id)

            # Set width (default to board's current track width)
            if width:
                track.SetWidth(int(width * 1000000))  # Convert mm to nm
            else:
                track.SetWidth(self.board.GetDesignSettings().GetCurrentTrackWidth())

            # Set net if provided
            if net:
                netinfo = self.board.GetNetInfo()
                nets_map = netinfo.NetsByName()
                if nets_map.has_key(net):
                    net_obj = nets_map[net]
                    track.SetNet(net_obj)

            # Add track to board
            self.board.Add(track)

            # Add via if requested and net is specified
            if via and net:
                via_point = end_point
                self.add_via(
                    {
                        "position": {
                            "x": via_point.x / 1000000,
                            "y": via_point.y / 1000000,
                            "unit": "mm",
                        },
                        "net": net,
                    }
                )

            return {
                "success": True,
                "message": "Added trace",
                "trace": {
                    "start": {
                        "x": start_point.x / 1000000,
                        "y": start_point.y / 1000000,
                        "unit": "mm",
                    },
                    "end": {
                        "x": end_point.x / 1000000,
                        "y": end_point.y / 1000000,
                        "unit": "mm",
                    },
                    "layer": layer,
                    "width": track.GetWidth() / 1000000,
                    "net": net,
                },
            }

        except Exception as e:
            logger.error(f"Error routing trace: {str(e)}")
            return {
                "success": False,
                "message": "Failed to route trace",
                "errorDetails": str(e),
            }

    def add_via(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add a via at the specified location"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            position = params.get("position")
            size = params.get("size")
            drill = params.get("drill")
            net = params.get("net")
            from_layer = params.get("from_layer", "F.Cu")
            to_layer = params.get("to_layer", "B.Cu")

            if not position:
                return {
                    "success": False,
                    "message": "Missing position",
                    "errorDetails": "position parameter is required",
                }

            # Create via
            via = pcbnew.PCB_VIA(self.board)

            # Set position
            scale = (
                1000000 if position["unit"] == "mm" else 25400000
            )  # mm or inch to nm
            x_nm = int(position["x"] * scale)
            y_nm = int(position["y"] * scale)
            via.SetPosition(pcbnew.VECTOR2I(x_nm, y_nm))

            # Set size and drill (default to board's current via settings)
            design_settings = self.board.GetDesignSettings()
            via.SetWidth(
                int(size * 1000000) if size else design_settings.GetCurrentViaSize()
            )
            via.SetDrill(
                int(drill * 1000000) if drill else design_settings.GetCurrentViaDrill()
            )

            # Set layers
            from_id = self.board.GetLayerID(from_layer)
            to_id = self.board.GetLayerID(to_layer)
            if from_id < 0 or to_id < 0:
                return {
                    "success": False,
                    "message": "Invalid layer",
                    "errorDetails": "Specified layers do not exist",
                }
            via.SetLayerPair(from_id, to_id)

            # Set net if provided
            if net:
                netinfo = self.board.GetNetInfo()
                nets_map = netinfo.NetsByName()
                if nets_map.has_key(net):
                    net_obj = nets_map[net]
                    via.SetNet(net_obj)

            # Add via to board
            self.board.Add(via)

            return {
                "success": True,
                "message": "Added via",
                "via": {
                    "position": {
                        "x": position["x"],
                        "y": position["y"],
                        "unit": position["unit"],
                    },
                    "size": via.GetWidth(pcbnew.F_Cu) / 1000000,
                    "drill": via.GetDrill() / 1000000,
                    "from_layer": from_layer,
                    "to_layer": to_layer,
                    "net": net,
                },
            }

        except Exception as e:
            logger.error(f"Error adding via: {str(e)}")
            return {
                "success": False,
                "message": "Failed to add via",
                "errorDetails": str(e),
            }

    def delete_trace(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a trace from the PCB"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            trace_uuid = params.get("traceUuid")
            position = params.get("position")
            net_name = params.get("net")
            layer = params.get("layer")
            include_vias = params.get("includeVias", False)

            if not trace_uuid and not position and not net_name:
                return {
                    "success": False,
                    "message": "Missing parameters",
                    "errorDetails": "One of traceUuid, position, or net must be provided",
                }

            # Delete by net name (bulk delete)
            if net_name:
                tracks_to_remove = []
                for track in list(self.board.Tracks()):
                    if track.GetNetname() != net_name:
                        continue

                    # Skip vias if not requested
                    is_via = track.Type() == pcbnew.PCB_VIA_T
                    if is_via and not include_vias:
                        continue

                    # Filter by layer if specified (only for non-vias)
                    if layer and not is_via:
                        layer_id = self.board.GetLayerID(layer)
                        if track.GetLayer() != layer_id:
                            continue

                    tracks_to_remove.append(track)

                deleted_count = len(tracks_to_remove)
                for track in tracks_to_remove:
                    self.board.Remove(track)
                tracks_to_remove.clear()
                self.board.SetModified()

                return {
                    "success": True,
                    "message": f"Deleted {deleted_count} traces on net '{net_name}'",
                    "deletedCount": deleted_count,
                }

            # Find track by UUID
            if trace_uuid:
                track = None
                for item in list(self.board.Tracks()):
                    if item.m_Uuid.AsString() == trace_uuid:
                        track = item
                        break

                if not track:
                    return {
                        "success": False,
                        "message": "Track not found",
                        "errorDetails": f"Could not find track with UUID: {trace_uuid}",
                    }

                self.board.Remove(track)
                track = None
                self.board.SetModified()
                return {"success": True, "message": f"Deleted track: {trace_uuid}"}

            # No valid parameters provided
            if not position:
                return {
                    "success": False,
                    "message": "No valid search parameter provided",
                    "errorDetails": "Provide traceUuid, position, or net parameter",
                }

            # Find track by position
            if position:
                scale = (
                    1000000 if position["unit"] == "mm" else 25400000
                )  # mm or inch to nm
                x_nm = int(position["x"] * scale)
                y_nm = int(position["y"] * scale)
                point = pcbnew.VECTOR2I(x_nm, y_nm)

                # Find closest track
                closest_track = None
                min_distance = float("inf")
                for track in list(self.board.Tracks()):
                    dist = self._point_to_track_distance(point, track)
                    if dist < min_distance:
                        min_distance = dist
                        closest_track = track

                if closest_track and min_distance < 1000000:  # Within 1mm
                    self.board.Remove(closest_track)
                    closest_track = None
                    self.board.SetModified()
                    return {
                        "success": True,
                        "message": "Deleted track at specified position",
                    }
                else:
                    return {
                        "success": False,
                        "message": "No track found",
                        "errorDetails": "No track found near specified position",
                    }

        except Exception as e:
            logger.error(f"Error deleting trace: {str(e)}")
            return {
                "success": False,
                "message": "Failed to delete trace",
                "errorDetails": str(e),
            }
        return {
            "success": False,
            "message": "No action taken",
            "errorDetails": "No matching trace found for given parameters",
        }

    def get_nets_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get a list of all nets in the PCB"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            nets = []
            netinfo = self.board.GetNetInfo()
            for net_code in range(netinfo.GetNetCount()):
                net = netinfo.GetNetItem(net_code)
                if net:
                    nets.append(
                        {
                            "name": net.GetNetname(),
                            "code": net.GetNetCode(),
                            "class": net.GetNetClassName(),
                        }
                    )

            return {"success": True, "nets": nets}

        except Exception as e:
            logger.error(f"Error getting nets list: {str(e)}")
            return {
                "success": False,
                "message": "Failed to get nets list",
                "errorDetails": str(e),
            }

    def query_traces(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Query traces by net, layer, or bounding box"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            # Get filter parameters
            net_name = params.get("net")
            layer = params.get("layer")
            bbox = params.get("boundingBox")  # {x1, y1, x2, y2, unit}
            include_vias = params.get("includeVias", False)

            scale = 1000000  # nm to mm conversion factor
            traces = []
            vias = []

            # Process tracks
            for track in list(self.board.Tracks()):
                try:
                    # Check if it's a via
                    is_via = track.Type() == pcbnew.PCB_VIA_T

                    if is_via and not include_vias:
                        continue

                    # Filter by net
                    if net_name and track.GetNetname() != net_name:
                        continue

                    # Filter by layer (only for tracks, not vias)
                    if layer and not is_via:
                        layer_id = self.board.GetLayerID(layer)
                        if track.GetLayer() != layer_id:
                            continue

                    # Filter by bounding box
                    if bbox:
                        bbox_unit = bbox.get("unit", "mm")
                        bbox_scale = scale if bbox_unit == "mm" else 25400000
                        x1 = int(bbox.get("x1", 0) * bbox_scale)
                        y1 = int(bbox.get("y1", 0) * bbox_scale)
                        x2 = int(bbox.get("x2", 0) * bbox_scale)
                        y2 = int(bbox.get("y2", 0) * bbox_scale)

                        if is_via:
                            pos = track.GetPosition()
                            if not (x1 <= pos.x <= x2 and y1 <= pos.y <= y2):
                                continue
                        else:
                            start = track.GetStart()
                            end = track.GetEnd()
                            # Check if either endpoint is within bbox
                            start_in = x1 <= start.x <= x2 and y1 <= start.y <= y2
                            end_in = x1 <= end.x <= x2 and y1 <= end.y <= y2
                            if not (start_in or end_in):
                                continue

                    if is_via:
                        pos = track.GetPosition()
                        vias.append(
                            {
                                "uuid": track.m_Uuid.AsString(),
                                "position": {
                                    "x": pos.x / scale,
                                    "y": pos.y / scale,
                                    "unit": "mm",
                                },
                                "net": track.GetNetname(),
                                "netCode": track.GetNetCode(),
                                "diameter": track.GetWidth() / scale,
                                "drill": track.GetDrillValue() / scale,
                            }
                        )
                    else:
                        start = track.GetStart()
                        end = track.GetEnd()
                        traces.append(
                            {
                                "uuid": track.m_Uuid.AsString(),
                                "net": track.GetNetname(),
                                "netCode": track.GetNetCode(),
                                "layer": self.board.GetLayerName(track.GetLayer()),
                                "width": track.GetWidth() / scale,
                                "start": {
                                    "x": start.x / scale,
                                    "y": start.y / scale,
                                    "unit": "mm",
                                },
                                "end": {
                                    "x": end.x / scale,
                                    "y": end.y / scale,
                                    "unit": "mm",
                                },
                                "length": track.GetLength() / scale,
                            }
                        )
                except Exception as track_err:
                    logger.warning(f"Skipping invalid track object: {track_err}")
                    continue

            result = {"success": True, "traceCount": len(traces), "traces": traces}

            if include_vias:
                result["viaCount"] = len(vias)
                result["vias"] = vias

            return result

        except Exception as e:
            logger.error(f"Error querying traces: {str(e)}")
            return {
                "success": False,
                "message": "Failed to query traces",
                "errorDetails": str(e),
            }

    def modify_trace(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Modify properties of an existing trace

        Allows changing trace width, layer, and net assignment.
        Find trace by UUID or position.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            # Identification parameters
            trace_uuid = params.get("uuid")
            position = params.get("position")  # {x, y, unit}

            # Modification parameters
            new_width = params.get("width")  # in mm
            new_layer = params.get("layer")
            new_net = params.get("net")

            if not trace_uuid and not position:
                return {
                    "success": False,
                    "message": "Missing trace identifier",
                    "errorDetails": "Provide either 'uuid' or 'position' to identify the trace",
                }

            scale = 1000000  # nm to mm conversion

            # Find the track
            track = None

            if trace_uuid:
                for item in list(self.board.Tracks()):
                    if item.m_Uuid.AsString() == trace_uuid:
                        track = item
                        break
            elif position:
                pos_unit = position.get("unit", "mm")
                pos_scale = scale if pos_unit == "mm" else 25400000
                x_nm = int(position["x"] * pos_scale)
                y_nm = int(position["y"] * pos_scale)
                point = pcbnew.VECTOR2I(x_nm, y_nm)

                # Find closest track
                min_distance = float("inf")
                for item in list(self.board.Tracks()):
                    dist = self._point_to_track_distance(point, item)
                    if dist < min_distance:
                        min_distance = dist
                        track = item

                # Only accept if within 1mm
                if min_distance >= 1000000:
                    track = None

            if not track:
                return {
                    "success": False,
                    "message": "Track not found",
                    "errorDetails": "Could not find track with specified identifier",
                }

            # Check if it's a via (some modifications don't apply)
            is_via = track.Type() == pcbnew.PCB_VIA_T
            modifications = []

            # Apply modifications
            if new_width is not None:
                width_nm = int(new_width * scale)
                track.SetWidth(width_nm)
                modifications.append(f"width={new_width}mm")

            if new_layer and not is_via:
                layer_id = self.board.GetLayerID(new_layer)
                if layer_id < 0:
                    return {
                        "success": False,
                        "message": "Invalid layer",
                        "errorDetails": f"Layer '{new_layer}' not found",
                    }
                track.SetLayer(layer_id)
                modifications.append(f"layer={new_layer}")

            if new_net:
                netinfo = self.board.GetNetInfo()
                net = netinfo.GetNetItem(new_net)
                if not net:
                    return {
                        "success": False,
                        "message": "Invalid net",
                        "errorDetails": f"Net '{new_net}' not found",
                    }
                track.SetNet(net)
                modifications.append(f"net={new_net}")

            if not modifications:
                return {
                    "success": False,
                    "message": "No modifications specified",
                    "errorDetails": "Provide at least one of: width, layer, net",
                }

            return {
                "success": True,
                "message": f"Modified trace: {', '.join(modifications)}",
                "uuid": track.m_Uuid.AsString(),
                "modifications": modifications,
            }

        except Exception as e:
            logger.error(f"Error modifying trace: {str(e)}")
            return {
                "success": False,
                "message": "Failed to modify trace",
                "errorDetails": str(e),
            }

    def copy_routing_pattern(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Copy routing pattern from source components to target components

        This enables routing replication between identical component groups.
        The pattern is copied with a translation offset calculated from
        the position difference between source and target components.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            source_refs = params.get("sourceRefs", [])  # e.g., ["U1", "U2", "U3"]
            target_refs = params.get("targetRefs", [])  # e.g., ["U4", "U5", "U6"]
            include_vias = params.get("includeVias", True)
            trace_width = params.get("traceWidth")  # Optional override

            if not source_refs or not target_refs:
                return {
                    "success": False,
                    "message": "Missing component references",
                    "errorDetails": "Provide both 'sourceRefs' and 'targetRefs' arrays",
                }

            if len(source_refs) != len(target_refs):
                return {
                    "success": False,
                    "message": "Mismatched component counts",
                    "errorDetails": f"sourceRefs has {len(source_refs)} items, targetRefs has {len(target_refs)}",
                }

            scale = 1000000  # nm to mm conversion

            # Get footprints
            footprints = {fp.GetReference(): fp for fp in self.board.GetFootprints()}

            # Validate all references exist
            for ref in source_refs + target_refs:
                if ref not in footprints:
                    return {
                        "success": False,
                        "message": "Component not found",
                        "errorDetails": f"Component '{ref}' not found on board",
                    }

            # Calculate offset from first source to first target component
            source_fp = footprints[source_refs[0]]
            target_fp = footprints[target_refs[0]]
            source_pos = source_fp.GetPosition()
            target_pos = target_fp.GetPosition()

            offset_x = target_pos.x - source_pos.x
            offset_y = target_pos.y - source_pos.y

            # Build mapping from source refs to target refs
            ref_mapping = dict(zip(source_refs, target_refs))

            # Collect all nets connected to source components
            source_nets = set()
            source_pad_positions = []  # (x, y) in nm for geometric fallback
            for ref in source_refs:
                fp = footprints[ref]
                for pad in fp.Pads():
                    net_name = pad.GetNetname()
                    if net_name and net_name != "":
                        source_nets.add(net_name)
                    pos = pad.GetPosition()
                    source_pad_positions.append((pos.x, pos.y))

            # Build bounding box around source pads (with 5mm tolerance in nm)
            TOLERANCE_NM = int(5 * scale)
            if source_pad_positions:
                xs = [p[0] for p in source_pad_positions]
                ys = [p[1] for p in source_pad_positions]
                bbox_x1 = min(xs) - TOLERANCE_NM
                bbox_x2 = max(xs) + TOLERANCE_NM
                bbox_y1 = min(ys) - TOLERANCE_NM
                bbox_y2 = max(ys) + TOLERANCE_NM
            else:
                # Fall back to component position ± 25mm
                sp = source_fp.GetPosition()
                bbox_x1 = sp.x - int(25 * scale)
                bbox_x2 = sp.x + int(25 * scale)
                bbox_y1 = sp.y - int(25 * scale)
                bbox_y2 = sp.y + int(25 * scale)

            def point_in_bbox(px: int, py: int) -> bool:
                return bbox_x1 <= px <= bbox_x2 and bbox_y1 <= py <= bbox_y2

            # Collect traces: by net name (if available) OR by geometric proximity
            use_net_filter = len(source_nets) > 0
            traces_to_copy = []
            vias_to_copy = []

            for track in list(self.board.Tracks()):
                is_via = track.Type() == pcbnew.PCB_VIA_T

                if use_net_filter:
                    # Primary: net-based filter
                    if track.GetNetname() not in source_nets:
                        continue
                else:
                    # Fallback: geometric filter – trace start OR end inside source bbox
                    if is_via:
                        pos = track.GetPosition()
                        if not point_in_bbox(pos.x, pos.y):
                            continue
                    else:
                        s = track.GetStart()
                        e = track.GetEnd()
                        if not (point_in_bbox(s.x, s.y) or point_in_bbox(e.x, e.y)):
                            continue

                if is_via:
                    if include_vias:
                        vias_to_copy.append(track)
                else:
                    traces_to_copy.append(track)

            filter_method = (
                "net-based" if use_net_filter else "geometric (pads have no nets)"
            )
            logger.info(
                f"copy_routing_pattern: {len(traces_to_copy)} traces, "
                f"{len(vias_to_copy)} vias selected via {filter_method}"
            )

            # Create new traces with offset
            created_traces = 0
            created_vias = 0

            for track in traces_to_copy:
                start = track.GetStart()
                end = track.GetEnd()

                # Create new track
                new_track = pcbnew.PCB_TRACK(self.board)
                new_track.SetStart(
                    pcbnew.VECTOR2I(start.x + offset_x, start.y + offset_y)
                )
                new_track.SetEnd(pcbnew.VECTOR2I(end.x + offset_x, end.y + offset_y))
                new_track.SetLayer(track.GetLayer())

                # Set width (use override or original)
                if trace_width:
                    new_track.SetWidth(int(trace_width * scale))
                else:
                    new_track.SetWidth(track.GetWidth())

                # Try to find corresponding target net
                # This is a simplification - more sophisticated mapping would be needed
                # for complex designs
                self.board.Add(new_track)
                created_traces += 1

            for via in vias_to_copy:
                pos = via.GetPosition()

                # Create new via
                new_via = pcbnew.PCB_VIA(self.board)
                new_via.SetPosition(pcbnew.VECTOR2I(pos.x + offset_x, pos.y + offset_y))
                new_via.SetWidth(via.GetWidth(pcbnew.F_Cu))
                new_via.SetDrill(via.GetDrillValue())
                new_via.SetViaType(via.GetViaType())

                self.board.Add(new_via)
                created_vias += 1

            result = {
                "success": True,
                "message": f"Copied routing pattern: {created_traces} traces, {created_vias} vias",
                "filterMethod": filter_method,
                "offset": {"x": offset_x / scale, "y": offset_y / scale, "unit": "mm"},
                "createdTraces": created_traces,
                "createdVias": created_vias,
                "sourceComponents": source_refs,
                "targetComponents": target_refs,
            }

            return result

        except Exception as e:
            logger.error(f"Error copying routing pattern: {str(e)}")
            return {
                "success": False,
                "message": "Failed to copy routing pattern",
                "errorDetails": str(e),
            }

    def create_netclass(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new net class with specified properties.

        Writes to BOTH the SWIG in-memory board AND the .kicad_pro file,
        since KiCad 9 reads netclass definitions from the project file.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            name = params.get("name")
            clearance = params.get("clearance")
            track_width = params.get("trackWidth")
            via_diameter = params.get("viaDiameter")
            via_drill = params.get("viaDrill")
            uvia_diameter = params.get("uviaDiameter")
            uvia_drill = params.get("uviaDrill")
            diff_pair_width = params.get("diffPairWidth")
            diff_pair_gap = params.get("diffPairGap")
            nets = params.get("nets", [])

            if not name:
                return {
                    "success": False,
                    "message": "Missing netclass name",
                    "errorDetails": "name parameter is required",
                }

            # SWIG path: register in-memory (for immediate board operations)
            scale = 1000000  # mm to nm
            try:
                ns = self.board.GetDesignSettings().m_NetSettings
                if ns.HasNetclass(name):
                    netclass = ns.GetNetClassByName(name)
                else:
                    netclass = pcbnew.NETCLASS(name)
                if clearance is not None:
                    netclass.SetClearance(int(clearance * scale))
                if track_width is not None:
                    netclass.SetTrackWidth(int(track_width * scale))
                if via_diameter is not None:
                    netclass.SetViaDiameter(int(via_diameter * scale))
                if via_drill is not None:
                    netclass.SetViaDrill(int(via_drill * scale))
                if uvia_diameter is not None:
                    netclass.SetuViaDiameter(int(uvia_diameter * scale))
                if uvia_drill is not None:
                    netclass.SetuViaDrill(int(uvia_drill * scale))
                if diff_pair_width is not None:
                    netclass.SetDiffPairWidth(int(diff_pair_width * scale))
                if diff_pair_gap is not None:
                    netclass.SetDiffPairGap(int(diff_pair_gap * scale))
                ns.SetNetclass(name, netclass)
            except Exception as swig_err:
                logger.warning(f"SWIG netclass registration failed: {swig_err}")

            # Project file path: write to .kicad_pro (persistent, authoritative)
            proj, pro_path = self._read_project()
            if proj:
                ns_proj = self._get_net_settings(proj)
                existing = self._find_class(ns_proj, name)
                if existing:
                    cls_entry = existing
                else:
                    cls_entry = {"name": name}
                    ns_proj["classes"].append(cls_entry)

                if track_width is not None:    cls_entry["track_width"] = track_width
                if clearance is not None:      cls_entry["clearance"] = clearance
                if via_diameter is not None:    cls_entry["via_dia"] = via_diameter
                if via_drill is not None:       cls_entry["via_drill"] = via_drill
                if diff_pair_width is not None: cls_entry["diff_pair_width"] = diff_pair_width
                if diff_pair_gap is not None:   cls_entry["diff_pair_gap"] = diff_pair_gap

                # Assign nets if provided
                if nets:
                    assignments = ns_proj["netclass_assignments"]
                    for net_name in nets:
                        assignments[net_name] = name

                self._write_project(proj, pro_path)

            return {
                "success": True,
                "message": f"Created net class: {name}",
                "netClass": {
                    "name": name,
                    "trackWidth": track_width,
                    "clearance": clearance,
                    "viaDiameter": via_diameter,
                    "viaDrill": via_drill,
                    "nets": nets,
                },
            }

        except Exception as e:
            logger.error(f"Error creating net class: {str(e)}")
            return {
                "success": False,
                "message": "Failed to create net class",
                "errorDetails": str(e),
            }

    # ── Project-file helpers for netclass operations ──
    # KiCad 9 stores netclass definitions and assignments in .kicad_pro (JSON),
    # NOT reliably in the SWIG in-memory board.  All netclass tools below
    # read/write the .kicad_pro file directly.

    def _get_project_path(self):
        """Return path to .kicad_pro file from the loaded board."""
        import os
        if not self.board:
            return None
        board_file = self.board.GetFileName()
        if not board_file:
            return None
        base = os.path.splitext(board_file)[0]
        pro = base + ".kicad_pro"
        return pro if os.path.exists(pro) else None

    def _read_project(self):
        """Read and parse the .kicad_pro JSON."""
        import json
        pro_path = self._get_project_path()
        if not pro_path:
            return None, None
        with open(pro_path, "r", encoding="utf-8") as f:
            return json.load(f), pro_path

    def _write_project(self, data, pro_path):
        """Write the .kicad_pro JSON back to disk."""
        import json, os
        with open(pro_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

    def _get_net_settings(self, proj):
        """Get or create net_settings section in project data."""
        if "net_settings" not in proj:
            proj["net_settings"] = {"classes": [{"name": "Default"}], "meta": {"version": 3}}
        ns = proj["net_settings"]
        if "classes" not in ns:
            ns["classes"] = [{"name": "Default"}]
        if "netclass_assignments" not in ns or ns["netclass_assignments"] is None:
            ns["netclass_assignments"] = {}
        if "netclass_patterns" not in ns:
            ns["netclass_patterns"] = []
        return ns

    def _find_class(self, ns, name):
        """Find a netclass dict in the classes list by name."""
        for cls in ns["classes"]:
            if cls.get("name") == name:
                return cls
        return None

    def assign_nets_to_netclass(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Assign nets to an existing netclass via .kicad_pro."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            netclass_name = params.get("netclass")
            nets = params.get("nets", [])

            if not netclass_name:
                return {"success": False, "message": "netclass parameter is required"}
            if not nets:
                return {"success": False, "message": "nets array is required"}

            proj, pro_path = self._read_project()
            if not proj:
                return {"success": False, "message": "Could not find .kicad_pro file"}

            ns = self._get_net_settings(proj)

            if netclass_name != "Default" and not self._find_class(ns, netclass_name):
                return {"success": False, "message": f"Netclass '{netclass_name}' does not exist in project"}

            # Direct net assignments (exact net name → class)
            assignments = ns["netclass_assignments"]
            for net_name in nets:
                assignments[net_name] = netclass_name

            self._write_project(proj, pro_path)

            return {
                "success": True,
                "message": f"Assigned {len(nets)} nets to netclass '{netclass_name}'",
                "netclass": netclass_name,
                "assigned": nets,
            }
        except Exception as e:
            logger.error(f"Error assigning nets to netclass: {e}")
            return {"success": False, "message": str(e)}

    def set_netclass_patterns(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Set pattern-based netclass assignment rules via .kicad_pro."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            netclass_name = params.get("netclass")
            patterns = params.get("patterns", [])

            if not netclass_name:
                return {"success": False, "message": "netclass parameter is required"}
            if not patterns:
                return {"success": False, "message": "patterns array is required"}

            proj, pro_path = self._read_project()
            if not proj:
                return {"success": False, "message": "Could not find .kicad_pro file"}

            ns = self._get_net_settings(proj)

            if netclass_name != "Default" and not self._find_class(ns, netclass_name):
                return {"success": False, "message": f"Netclass '{netclass_name}' does not exist in project"}

            # Pattern assignments: list of {netclass: name, pattern: glob}
            existing_patterns = ns["netclass_patterns"]
            for pat in patterns:
                # Remove any existing pattern with same glob
                existing_patterns = [p for p in existing_patterns if p.get("pattern") != pat]
                existing_patterns.append({"netclass": netclass_name, "pattern": pat})
            ns["netclass_patterns"] = existing_patterns

            self._write_project(proj, pro_path)

            return {
                "success": True,
                "message": f"Set {len(patterns)} patterns for netclass '{netclass_name}'",
                "netclass": netclass_name,
                "patterns": patterns,
            }
        except Exception as e:
            logger.error(f"Error setting netclass patterns: {e}")
            return {"success": False, "message": str(e)}

    def get_netclass_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Query all netclasses from .kicad_pro with settings and assignments."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            proj, pro_path = self._read_project()
            if not proj:
                return {"success": False, "message": "Could not find .kicad_pro file"}

            ns = self._get_net_settings(proj)
            assignments = ns.get("netclass_assignments") or {}
            patterns = ns.get("netclass_patterns") or []

            # Build reverse map: class_name -> [net_names]
            class_nets = {}
            for net_name, cls_name in assignments.items():
                class_nets.setdefault(cls_name, []).append(net_name)

            # Build reverse map: class_name -> [patterns]
            class_patterns = {}
            for p in patterns:
                cls_name = p.get("netclass", "Default")
                pat = p.get("pattern", "")
                class_patterns.setdefault(cls_name, []).append(pat)

            # Build output from classes list
            netclasses = []
            for cls in ns["classes"]:
                name = cls.get("name", "?")
                entry = {
                    "name": name,
                    "trackWidth": cls.get("track_width"),
                    "clearance": cls.get("clearance"),
                    "viaDiameter": cls.get("via_dia"),
                    "viaDrill": cls.get("via_drill"),
                    "nets": sorted(class_nets.get(name, [])),
                    "patterns": class_patterns.get(name, []),
                }
                netclasses.append(entry)

            return {
                "success": True,
                "count": len(netclasses),
                "netclasses": netclasses,
            }
        except Exception as e:
            logger.error(f"Error getting netclass list: {e}")
            return {"success": False, "message": str(e)}

    def edit_netclass(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Modify an existing netclass's properties in .kicad_pro."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            name = params.get("name")
            if not name:
                return {"success": False, "message": "name parameter is required"}

            proj, pro_path = self._read_project()
            if not proj:
                return {"success": False, "message": "Could not find .kicad_pro file"}

            ns = self._get_net_settings(proj)
            cls = self._find_class(ns, name)
            if not cls:
                return {"success": False, "message": f"Netclass '{name}' does not exist"}

            changes = []

            track_width = params.get("trackWidth")
            if track_width is not None:
                cls["track_width"] = track_width
                changes.append(f"trackWidth={track_width}mm")

            clearance = params.get("clearance")
            if clearance is not None:
                cls["clearance"] = clearance
                changes.append(f"clearance={clearance}mm")

            via_diameter = params.get("viaDiameter")
            if via_diameter is not None:
                cls["via_dia"] = via_diameter
                changes.append(f"viaDiameter={via_diameter}mm")

            via_drill = params.get("viaDrill")
            if via_drill is not None:
                cls["via_drill"] = via_drill
                changes.append(f"viaDrill={via_drill}mm")

            if not changes:
                return {"success": False, "message": "No properties to change"}

            self._write_project(proj, pro_path)

            return {
                "success": True,
                "message": f"Updated netclass '{name}': {', '.join(changes)}",
                "netclass": name,
                "changes": changes,
            }
        except Exception as e:
            logger.error(f"Error editing netclass: {e}")
            return {"success": False, "message": str(e)}

    def delete_netclass(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove a netclass from .kicad_pro. Assignments using it are removed."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            name = params.get("name")
            if not name:
                return {"success": False, "message": "name parameter is required"}
            if name == "Default":
                return {"success": False, "message": "Cannot delete the Default netclass"}

            proj, pro_path = self._read_project()
            if not proj:
                return {"success": False, "message": "Could not find .kicad_pro file"}

            ns = self._get_net_settings(proj)

            # Remove from classes list
            before = len(ns["classes"])
            ns["classes"] = [c for c in ns["classes"] if c.get("name") != name]
            if len(ns["classes"]) == before:
                return {"success": False, "message": f"Netclass '{name}' does not exist"}

            # Remove direct assignments pointing to this class
            assignments = ns.get("netclass_assignments") or {}
            removed_nets = [k for k, v in assignments.items() if v == name]
            for k in removed_nets:
                del assignments[k]

            # Remove pattern assignments for this class
            ns["netclass_patterns"] = [
                p for p in (ns.get("netclass_patterns") or [])
                if p.get("netclass") != name
            ]

            self._write_project(proj, pro_path)

            return {
                "success": True,
                "message": f"Deleted netclass '{name}'. Removed {len(removed_nets)} net assignments.",
            }
        except Exception as e:
            logger.error(f"Error deleting netclass: {e}")
            return {"success": False, "message": str(e)}

    def get_net_to_netclass_map(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Quick dump of net-to-netclass assignments from .kicad_pro."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            proj, pro_path = self._read_project()
            if not proj:
                return {"success": False, "message": "Could not find .kicad_pro file"}

            ns = self._get_net_settings(proj)
            assignments = ns.get("netclass_assignments") or {}
            patterns = ns.get("netclass_patterns") or []

            # Build class -> nets map from direct assignments
            net_map = {}
            for net_name, cls_name in assignments.items():
                net_map.setdefault(cls_name, []).append(net_name)

            # Sort nets within each class
            for cls in net_map:
                net_map[cls].sort()

            # Include pattern info
            pattern_map = {}
            for p in patterns:
                cls_name = p.get("netclass", "Default")
                pattern_map.setdefault(cls_name, []).append(p.get("pattern", ""))

            return {
                "success": True,
                "total_assigned_nets": sum(len(v) for v in net_map.values()),
                "netclasses_with_assignments": len(net_map),
                "assignments": net_map,
                "patterns": pattern_map,
            }
        except Exception as e:
            logger.error(f"Error getting net-to-netclass map: {e}")
            return {"success": False, "message": str(e)}

    def resize_vias(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Batch resize vias on the board.

        If oldDrill/oldDiameter are provided, only vias matching those sizes are
        resized. Otherwise ALL vias on the board are resized.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            new_drill = params.get("drill")
            new_diameter = params.get("diameter")
            old_drill = params.get("oldDrill")
            old_diameter = params.get("oldDiameter")

            if new_drill is None and new_diameter is None:
                return {
                    "success": False,
                    "message": "At least one of drill or diameter must be provided",
                }

            scale = 1000000  # mm to nm
            new_drill_nm = int(new_drill * scale) if new_drill is not None else None
            new_diameter_nm = int(new_diameter * scale) if new_diameter is not None else None
            old_drill_nm = int(old_drill * scale) if old_drill is not None else None
            old_diameter_nm = int(old_diameter * scale) if old_diameter is not None else None

            # Tolerance for matching (500nm = 0.0005mm)
            TOL = 500

            resized = 0
            skipped = 0
            total_vias = 0

            for track in self.board.GetTracks():
                if track.Type() != pcbnew.PCB_VIA_T:
                    continue

                via = pcbnew.Cast_to_PCB_VIA(track)
                total_vias += 1

                # Filter by old size if specified
                if old_drill_nm is not None:
                    if abs(via.GetDrill() - old_drill_nm) > TOL:
                        skipped += 1
                        continue
                if old_diameter_nm is not None:
                    if abs(via.GetWidth() - old_diameter_nm) > TOL:
                        skipped += 1
                        continue

                # Apply new sizes
                if new_drill_nm is not None:
                    via.SetDrill(new_drill_nm)
                if new_diameter_nm is not None:
                    via.SetWidth(new_diameter_nm)
                resized += 1

            if resized > 0:
                self.board.SetModified()

            filter_desc = ""
            if old_drill is not None or old_diameter is not None:
                parts = []
                if old_drill is not None:
                    parts.append(f"drill={old_drill}mm")
                if old_diameter is not None:
                    parts.append(f"diameter={old_diameter}mm")
                filter_desc = f" matching {', '.join(parts)}"

            return {
                "success": True,
                "message": f"Resized {resized} of {total_vias} vias{filter_desc}",
                "resized": resized,
                "skipped": skipped,
                "total_vias": total_vias,
                "new_drill_mm": new_drill,
                "new_diameter_mm": new_diameter,
            }

        except Exception as e:
            logger.error(f"Error resizing vias: {str(e)}")
            return {
                "success": False,
                "message": "Failed to resize vias",
                "errorDetails": str(e),
            }

    def resize_traces(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Batch resize traces on the board.

        Supports filtering by oldWidth (exact match), minWidth (below threshold),
        and net name. At least 'width' must be provided.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            new_width = params.get("width")
            old_width = params.get("oldWidth")
            min_width = params.get("minWidth")
            net_filter = params.get("net")

            if new_width is None:
                return {"success": False, "message": "width parameter is required"}
            if old_width is not None and min_width is not None:
                return {"success": False, "message": "Cannot combine oldWidth and minWidth"}

            scale = 1_000_000  # mm to nm
            new_width_nm = int(new_width * scale)
            old_width_nm = int(old_width * scale) if old_width is not None else None
            min_width_nm = int(min_width * scale) if min_width is not None else None

            TOL = 500  # 500nm = 0.0005mm tolerance

            resized = 0
            skipped = 0
            total_traces = 0

            for track in self.board.GetTracks():
                if track.Type() != pcbnew.PCB_TRACE_T:
                    continue

                total_traces += 1
                track_width = track.GetWidth()

                # Filter by net name
                if net_filter is not None:
                    net = track.GetNet()
                    if net and net.GetNetname() != net_filter:
                        skipped += 1
                        continue

                # Filter by exact old width
                if old_width_nm is not None:
                    if abs(track_width - old_width_nm) > TOL:
                        skipped += 1
                        continue

                # Filter by minimum width (only resize traces BELOW this)
                if min_width_nm is not None:
                    if track_width >= min_width_nm:
                        skipped += 1
                        continue

                track.SetWidth(new_width_nm)
                resized += 1

            if resized > 0:
                self.board.SetModified()

            return {
                "success": True,
                "message": f"Resized {resized} of {total_traces} traces",
                "resized": resized,
                "skipped": skipped,
                "total_traces": total_traces,
                "new_width_mm": new_width,
            }

        except Exception as e:
            logger.error(f"Error resizing traces: {str(e)}")
            return {
                "success": False,
                "message": "Failed to resize traces",
                "errorDetails": str(e),
            }

    def get_trace_statistics(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get trace width and via drill/pad size distribution."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            below_width = params.get("belowWidth")
            scale = 1_000_000

            trace_widths = {}   # width_nm -> count
            via_drills = {}     # (drill_nm, pad_nm) -> count
            total_traces = 0
            total_vias = 0

            for track in self.board.GetTracks():
                if track.Type() == pcbnew.PCB_TRACE_T:
                    total_traces += 1
                    w = track.GetWidth()
                    trace_widths[w] = trace_widths.get(w, 0) + 1
                elif track.Type() == pcbnew.PCB_VIA_T:
                    total_vias += 1
                    via = pcbnew.Cast_to_PCB_VIA(track)
                    key = (via.GetDrill(), via.GetWidth())
                    via_drills[key] = via_drills.get(key, 0) + 1

            # Format trace widths
            trace_list = []
            below_count = 0
            below_nm = int(below_width * scale) if below_width else None
            for w_nm, count in sorted(trace_widths.items()):
                w_mm = round(w_nm / scale, 4)
                entry = {"width_mm": w_mm, "count": count}
                if below_nm and w_nm < below_nm:
                    entry["below_threshold"] = True
                    below_count += count
                trace_list.append(entry)

            # Format via sizes
            via_list = []
            for (drill_nm, pad_nm), count in sorted(via_drills.items()):
                via_list.append({
                    "drill_mm": round(drill_nm / scale, 4),
                    "pad_mm": round(pad_nm / scale, 4),
                    "count": count,
                })

            result = {
                "success": True,
                "total_traces": total_traces,
                "total_vias": total_vias,
                "trace_widths": sorted(trace_list, key=lambda x: -x["count"]),
                "via_sizes": sorted(via_list, key=lambda x: -x["count"]),
            }
            if below_nm:
                result["below_threshold_count"] = below_count
                result["threshold_mm"] = below_width
            return result

        except Exception as e:
            logger.error(f"Error getting trace statistics: {str(e)}")
            return {"success": False, "message": str(e)}

    def add_copper_pour(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add a copper pour (zone) to the PCB"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            layer = params.get("layer", "F.Cu")
            net = params.get("net")
            clearance = params.get("clearance")
            min_width = params.get("minWidth", 0.2)
            points = params.get("outline", params.get("points", []))
            priority = params.get("priority", 0)
            fill_type = params.get("fillType", "solid")  # solid or hatched

            # If no outline provided, use board outline
            if not points or len(points) < 3:
                board_box = self.board.GetBoardEdgesBoundingBox()
                if board_box.GetWidth() > 0 and board_box.GetHeight() > 0:
                    scale = 1000000  # nm to mm
                    x1 = board_box.GetX() / scale
                    y1 = board_box.GetY() / scale
                    x2 = (board_box.GetX() + board_box.GetWidth()) / scale
                    y2 = (board_box.GetY() + board_box.GetHeight()) / scale

                    # Detect corner radius from Edge.Cuts arcs so the zone rectangle
                    # stays inside the rounded board corners (avoids zone visually
                    # extending outside Edge.Cuts before refill)
                    corner_radius = 0.0
                    edge_layer_id = self.board.GetLayerID("Edge.Cuts")
                    for item in self.board.GetDrawings():
                        if item.GetLayer() == edge_layer_id and item.GetClass() == "PCB_ARC":
                            r = item.GetRadius() / scale
                            if r > corner_radius:
                                corner_radius = r
                    # Inset the zone rectangle by the corner radius so its corners
                    # lie on the straight portions of the board edge.
                    inset = corner_radius
                    points = [
                        {"x": x1 + inset, "y": y1 + inset},
                        {"x": x2 - inset, "y": y1 + inset},
                        {"x": x2 - inset, "y": y2 - inset},
                        {"x": x1 + inset, "y": y2 - inset},
                    ]
                else:
                    return {
                        "success": False,
                        "message": "Missing outline",
                        "errorDetails": "Provide an outline array or add a board outline first",
                    }

            # Get layer ID
            layer_id = self.board.GetLayerID(layer)
            if layer_id < 0:
                return {
                    "success": False,
                    "message": "Invalid layer",
                    "errorDetails": f"Layer '{layer}' does not exist",
                }

            # Create zone
            zone = pcbnew.ZONE(self.board)
            zone.SetLayer(layer_id)

            # Set net if provided
            if net:
                netinfo = self.board.GetNetInfo()
                nets_map = netinfo.NetsByName()
                if nets_map.has_key(net):
                    net_obj = nets_map[net]
                    zone.SetNet(net_obj)

            # Set zone properties
            scale = 1000000  # mm to nm
            zone.SetAssignedPriority(priority)

            if clearance is not None:
                zone.SetLocalClearance(int(clearance * scale))

            zone.SetMinThickness(int(min_width * scale))

            # Set fill type
            if fill_type == "hatched":
                zone.SetFillMode(pcbnew.ZONE_FILL_MODE_HATCH_PATTERN)
            else:
                zone.SetFillMode(pcbnew.ZONE_FILL_MODE_POLYGONS)

            # Create outline
            outline = zone.Outline()
            outline.NewOutline()  # Create a new outline contour first

            # Add points to outline
            for point in points:
                scale = 1000000 if point.get("unit", "mm") == "mm" else 25400000
                x_nm = int(point["x"] * scale)
                y_nm = int(point["y"] * scale)
                outline.Append(pcbnew.VECTOR2I(x_nm, y_nm))  # Add point to outline

            # Add zone to board
            self.board.Add(zone)

            # Fill zone
            # Note: Zone filling can cause issues with SWIG API
            # Comment out for now - zones will be filled when board is saved/opened in KiCAD
            # filler = pcbnew.ZONE_FILLER(self.board)
            # filler.Fill(self.board.Zones())

            return {
                "success": True,
                "message": "Added copper pour",
                "pour": {
                    "layer": layer,
                    "net": net,
                    "clearance": clearance,
                    "minWidth": min_width,
                    "priority": priority,
                    "fillType": fill_type,
                    "pointCount": len(points),
                },
            }

        except Exception as e:
            logger.error(f"Error adding copper pour: {str(e)}")
            return {
                "success": False,
                "message": "Failed to add copper pour",
                "errorDetails": str(e),
            }

    def route_differential_pair(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Route a differential pair between two sets of points or pads"""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            start_pos = params.get("startPos")
            end_pos = params.get("endPos")
            net_pos = params.get("netPos")
            net_neg = params.get("netNeg")
            layer = params.get("layer", "F.Cu")
            width = params.get("width")
            gap = params.get("gap")

            if not start_pos or not end_pos or not net_pos or not net_neg:
                return {
                    "success": False,
                    "message": "Missing parameters",
                    "errorDetails": "startPos, endPos, netPos, and netNeg are required",
                }

            # Get layer ID
            layer_id = self.board.GetLayerID(layer)
            if layer_id < 0:
                return {
                    "success": False,
                    "message": "Invalid layer",
                    "errorDetails": f"Layer '{layer}' does not exist",
                }

            # Get nets
            netinfo = self.board.GetNetInfo()
            nets_map = netinfo.NetsByName()

            net_pos_obj = nets_map[net_pos] if nets_map.has_key(net_pos) else None
            net_neg_obj = nets_map[net_neg] if nets_map.has_key(net_neg) else None

            if not net_pos_obj or not net_neg_obj:
                return {
                    "success": False,
                    "message": "Nets not found",
                    "errorDetails": "One or both nets specified for the differential pair do not exist",
                }

            # Get start and end points
            start_point = self._get_point(start_pos)
            end_point = self._get_point(end_pos)

            # Calculate offset vectors for the two traces
            # First, get the direction vector from start to end
            dx = end_point.x - start_point.x
            dy = end_point.y - start_point.y
            length = math.sqrt(dx * dx + dy * dy)

            if length <= 0:
                return {
                    "success": False,
                    "message": "Invalid points",
                    "errorDetails": "Start and end points must be different",
                }

            # Normalize direction vector
            dx /= length
            dy /= length

            # Get perpendicular vector
            px = -dy
            py = dx

            # Set default gap if not provided
            if gap is None:
                gap = 0.2  # mm

            # Convert to nm
            gap_nm = int(gap * 1000000)

            # Calculate offsets
            offset_x = int(px * gap_nm / 2)
            offset_y = int(py * gap_nm / 2)

            # Create positive and negative trace points
            pos_start = pcbnew.VECTOR2I(
                int(start_point.x + offset_x), int(start_point.y + offset_y)
            )
            pos_end = pcbnew.VECTOR2I(
                int(end_point.x + offset_x), int(end_point.y + offset_y)
            )
            neg_start = pcbnew.VECTOR2I(
                int(start_point.x - offset_x), int(start_point.y - offset_y)
            )
            neg_end = pcbnew.VECTOR2I(
                int(end_point.x - offset_x), int(end_point.y - offset_y)
            )

            # Create positive trace
            pos_track = pcbnew.PCB_TRACK(self.board)
            pos_track.SetStart(pos_start)
            pos_track.SetEnd(pos_end)
            pos_track.SetLayer(layer_id)
            pos_track.SetNet(net_pos_obj)

            # Create negative trace
            neg_track = pcbnew.PCB_TRACK(self.board)
            neg_track.SetStart(neg_start)
            neg_track.SetEnd(neg_end)
            neg_track.SetLayer(layer_id)
            neg_track.SetNet(net_neg_obj)

            # Set width
            if width:
                trace_width_nm = int(width * 1000000)
                pos_track.SetWidth(trace_width_nm)
                neg_track.SetWidth(trace_width_nm)
            else:
                # Get default width from design rules or net class
                trace_width = self.board.GetDesignSettings().GetCurrentTrackWidth()
                pos_track.SetWidth(trace_width)
                neg_track.SetWidth(trace_width)

            # Add tracks to board
            self.board.Add(pos_track)
            self.board.Add(neg_track)

            return {
                "success": True,
                "message": "Added differential pair traces",
                "diffPair": {
                    "posNet": net_pos,
                    "negNet": net_neg,
                    "layer": layer,
                    "width": pos_track.GetWidth() / 1000000,
                    "gap": gap,
                    "length": length / 1000000,
                },
            }

        except Exception as e:
            logger.error(f"Error routing differential pair: {str(e)}")
            return {
                "success": False,
                "message": "Failed to route differential pair",
                "errorDetails": str(e),
            }

    def _get_point(self, point_spec: Dict[str, Any]) -> pcbnew.VECTOR2I:
        """Convert point specification to KiCAD point"""
        if "x" in point_spec and "y" in point_spec:
            scale = 1000000 if point_spec.get("unit", "mm") == "mm" else 25400000
            x_nm = int(point_spec["x"] * scale)
            y_nm = int(point_spec["y"] * scale)
            return pcbnew.VECTOR2I(x_nm, y_nm)
        elif "pad" in point_spec and "componentRef" in point_spec:
            module = self.board.FindFootprintByReference(point_spec["componentRef"])
            if module:
                pad = module.FindPadByName(point_spec["pad"])
                if pad:
                    return pad.GetPosition()
        raise ValueError("Invalid point specification")

    def _point_to_track_distance(
        self, point: pcbnew.VECTOR2I, track: pcbnew.PCB_TRACK
    ) -> float:
        """Calculate distance from point to track segment"""
        start = track.GetStart()
        end = track.GetEnd()

        # Vector from start to end
        v = pcbnew.VECTOR2I(end.x - start.x, end.y - start.y)
        # Vector from start to point
        w = pcbnew.VECTOR2I(point.x - start.x, point.y - start.y)

        # Length of track squared
        c1 = v.x * v.x + v.y * v.y
        if c1 == 0:
            return self._point_distance(point, start)

        # Projection coefficient
        c2 = float(w.x * v.x + w.y * v.y) / c1

        if c2 < 0:
            return self._point_distance(point, start)
        elif c2 > 1:
            return self._point_distance(point, end)

        # Point on line
        proj = pcbnew.VECTOR2I(int(start.x + c2 * v.x), int(start.y + c2 * v.y))
        return self._point_distance(point, proj)

    def _point_distance(self, p1: pcbnew.VECTOR2I, p2: pcbnew.VECTOR2I) -> float:
        """Calculate distance between two points"""
        dx = p1.x - p2.x
        dy = p1.y - p2.y
        return (dx * dx + dy * dy) ** 0.5

    def assign_nets_to_layer(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Move all traces for specified nets to a target layer."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            layer_name = params.get("layer")
            nets = params.get("nets", [])

            if not layer_name:
                return {"success": False, "message": "layer parameter is required"}
            if not nets:
                return {"success": False, "message": "nets array is required"}

            # Resolve layer ID
            layer_id = self.board.GetLayerID(layer_name)
            if layer_id < 0:
                return {"success": False, "message": f"Unknown layer: {layer_name}"}

            net_set = set(nets)
            moved = 0
            total = 0

            for track in self.board.GetTracks():
                if track.Type() != pcbnew.PCB_TRACE_T:
                    continue
                net = track.GetNet()
                if net and net.GetNetname() in net_set:
                    total += 1
                    if track.GetLayer() != layer_id:
                        track.SetLayer(layer_id)
                        moved += 1

            if moved > 0:
                self.board.SetModified()

            return {
                "success": True,
                "message": f"Moved {moved} of {total} traces to {layer_name}",
                "moved": moved,
                "total_traces": total,
                "layer": layer_name,
            }
        except Exception as e:
            logger.error(f"Error assigning nets to layer: {e}")
            return {"success": False, "message": str(e)}

    def set_copper_pour_settings(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Configure fill settings for copper pour zones by net name."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            net_name = params.get("net")
            if not net_name:
                return {"success": False, "message": "net parameter is required"}

            scale = 1_000_000
            modified = 0

            for zone in self.board.Zones():
                if zone.GetNetname() != net_name:
                    continue

                changes = []
                clearance = params.get("clearance")
                if clearance is not None:
                    zone.SetLocalClearance(int(clearance * scale))
                    changes.append(f"clearance={clearance}mm")

                min_width = params.get("minWidth")
                if min_width is not None:
                    zone.SetMinThickness(int(min_width * scale))
                    changes.append(f"minWidth={min_width}mm")

                gap = params.get("thermalReliefGap")
                if gap is not None:
                    zone.SetThermalReliefGap(int(gap * scale))
                    changes.append(f"thermalReliefGap={gap}mm")

                spoke = params.get("thermalReliefSpokeWidth")
                if spoke is not None:
                    zone.SetThermalReliefSpokeWidth(int(spoke * scale))
                    changes.append(f"thermalReliefSpokeWidth={spoke}mm")

                priority = params.get("priority")
                if priority is not None:
                    zone.SetAssignedPriority(int(priority))
                    changes.append(f"priority={priority}")

                if changes:
                    modified += 1

            if modified == 0:
                return {"success": False, "message": f"No zones found on net '{net_name}'"}

            self.board.SetModified()

            return {
                "success": True,
                "message": f"Updated {modified} zone(s) on net '{net_name}'",
                "zones_modified": modified,
            }
        except Exception as e:
            logger.error(f"Error setting copper pour settings: {e}")
            return {"success": False, "message": str(e)}

    def get_unrouted_connections(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """List unrouted connections (ratsnest pad pairs)."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            net_filter = params.get("net")
            limit = params.get("limit", 100)
            scale = 1_000_000

            connectivity = self.board.GetConnectivity()
            connectivity.RecalculateRatsnest()

            unrouted = []
            total_unrouted = 0

            # Build net name lookup
            net_names = {}
            netinfo = self.board.GetNetInfo()
            for i in range(netinfo.GetNetCount()):
                net = netinfo.GetNetItem(i)
                if net:
                    net_names[net.GetNetCode()] = net.GetNetname()

            # Iterate all ratsnest edges
            for net_code, name in net_names.items():
                if net_filter and name != net_filter:
                    continue
                if not name:
                    continue

                try:
                    ratsnest = connectivity.GetRatsnestForNet(net_code)
                    for edge in ratsnest:
                        if edge.IsValid() and not edge.IsRouted():
                            total_unrouted += 1
                            if len(unrouted) < limit:
                                src = edge.GetSourcePos()
                                tgt = edge.GetTargetPos()
                                unrouted.append({
                                    "net": name,
                                    "from": {"x": round(src.x / scale, 4), "y": round(src.y / scale, 4)},
                                    "to": {"x": round(tgt.x / scale, 4), "y": round(tgt.y / scale, 4)},
                                })
                except Exception:
                    continue

            return {
                "success": True,
                "total_unrouted": total_unrouted,
                "returned": len(unrouted),
                "connections": unrouted,
            }
        except Exception as e:
            logger.error(f"Error getting unrouted connections: {e}")
            return {"success": False, "message": str(e)}

    def get_board_statistics(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get board routing statistics."""
        try:
            if not self.board:
                return {"success": False, "message": "No board is loaded"}

            scale = 1_000_000

            # Count traces by layer and total length
            trace_stats: Dict[str, Any] = {}  # layer_name -> {count, total_length_mm}
            via_count = 0
            via_types: Dict[Any, int] = {}  # (drill_mm, pad_mm) -> count
            total_traces = 0

            for track in self.board.GetTracks():
                if track.Type() == pcbnew.PCB_TRACE_T:
                    total_traces += 1
                    layer_name = self.board.GetLayerName(track.GetLayer())
                    if layer_name not in trace_stats:
                        trace_stats[layer_name] = {"count": 0, "total_length_mm": 0}
                    trace_stats[layer_name]["count"] += 1
                    trace_stats[layer_name]["total_length_mm"] += track.GetLength() / scale
                elif track.Type() == pcbnew.PCB_VIA_T:
                    via_count += 1
                    via = pcbnew.Cast_to_PCB_VIA(track)
                    key = (round(via.GetDrill() / scale, 4), round(via.GetWidth() / scale, 4))
                    via_types[key] = via_types.get(key, 0) + 1

            # Round trace lengths
            for layer in trace_stats:
                trace_stats[layer]["total_length_mm"] = round(trace_stats[layer]["total_length_mm"], 2)

            # Unrouted count
            unrouted = 0
            try:
                connectivity = self.board.GetConnectivity()
                connectivity.RecalculateRatsnest()
                unrouted = connectivity.GetUnconnectedCount()
            except Exception:
                pass

            # Component count
            component_count = len(self.board.GetFootprints())

            # Via types formatted
            via_list = [
                {"drill_mm": k[0], "pad_mm": k[1], "count": v}
                for k, v in sorted(via_types.items())
            ]

            return {
                "success": True,
                "components": component_count,
                "traces": total_traces,
                "vias": via_count,
                "unrouted": unrouted,
                "trace_length_by_layer": trace_stats,
                "via_types": via_list,
            }
        except Exception as e:
            logger.error(f"Error getting board statistics: {e}")
            return {"success": False, "message": str(e)}
