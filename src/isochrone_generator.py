"""
isochrone_generator.py
======================
Generate walking isochrone polygons (GeoJSON) for one or more points using
OSMnx and OpenStreetMap data only. No API keys required.

Optimized for sparse input data points: one small graph download per input point,
instead of one giant bbox covering all of them).
The resulting isochrone polygons are merged into a single geometry at the end.

Main methods:
    generate_isochrones(locations, time_minutes, walking_speed_kmh) -> dict
    save_isochrones(geojson, output_path) -> None

CLI:
    python isochrone_generator.py \\
        --locations 4.6588,-74.1313 4.6097,-74.0817 \\
        --time 10 --speed 4.3 --output isocronas.geojson
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Iterable

# Ensure the project root (parent of src/) is on sys.path so `configs` is found
# regardless of the working directory the script is launched from.
sys.path.insert(0, str(Path(__file__).parent.parent))

import networkx as nx
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import LineString, MultiPoint, mapping
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

from configs.constants import (
    DEFAULT_WALKING_SPEED_KMH,
    GRAPH_BUFFER_MARGIN_M,
    NODE_BUFFER_RADIUS_M,
)

log = logging.getLogger(__name__)

Coord = tuple[float, float]


def _normalize_locations(locations) -> list[Coord]:
    """Accept (lat, lon) or an iterable of (lat, lon) and return a list."""
    # Single (lat, lon). If ints, return as floats
    if (
        isinstance(locations, (tuple, list))
        and len(locations) == 2
        and all(isinstance(c, (int, float)) for c in locations)
    ):
        return [(float(locations[0]), float(locations[1]))]

    if isinstance(locations, Iterable):
        out: list[Coord] = []
        # Each pair of coords has to be a tuple or a list
        for loc in locations:
            if not (
                isinstance(loc, (tuple, list))
                and len(loc) == 2
                and all(isinstance(c, (int, float)) for c in loc)
            ):
                raise ValueError(f"Each location must be (lat, lon). Got: {loc!r}")
            out.append((float(loc[0]), float(loc[1])))
        if not out:
            raise ValueError("locations is empty.")
        return out

    raise ValueError("locations must be a (lat, lon) tuple or an iterable of them.")


def _isochrone_geometry(
    Gp: nx.MultiDiGraph,
    source: int,
    time_limit_s: float,
    buffer_m: float,
):
    """Return a Shapely polygon (in the graph's projected CRS) and node count."""
    lengths = nx.single_source_dijkstra_path_length(
        Gp, source, cutoff=time_limit_s, weight="travel_time"
    )
    if not lengths:
        return None, 0

    nodes = Gp.nodes
    reachable = lengths.keys()

    # Edges with both endpoints reachable: include their full geometry. This
    # turns the result from a scatter of points into a connected network shape
    # before the buffer is applied.
    edge_geoms = []
    for u, v, data in Gp.edges(data=True):
        if u in lengths and v in lengths:
            geom = data.get("geometry")
            if geom is None:
                geom = LineString((
                    (nodes[u]["x"], nodes[u]["y"]),
                    (nodes[v]["x"], nodes[v]["y"]),
                ))
            edge_geoms.append(geom)

    node_points = MultiPoint([(nodes[n]["x"], nodes[n]["y"]) for n in reachable])
    base = unary_union(edge_geoms + [node_points]) if edge_geoms else node_points

    # resolution=4 -> octagonal buffer ends; faster and still smooth enough.
    polygon = base.buffer(buffer_m, resolution=4)
    return polygon, len(lengths)


def generate_isochrones(
    locations,
    time_minutes: float,
    walking_speed_kmh: float = DEFAULT_WALKING_SPEED_KMH,
) -> dict:
    """Build walking isochrones for one or more points and return their union.

    Downloads a small graph around each point individually (better than one
    giant bbox when locations are sparse). The per-point isochrone polygons
    are merged into a single geometry before being returned.

    Args:
        locations: A (lat, lon) tuple or an iterable of (lat, lon) tuples.
        time_minutes: Walking time budget in minutes.
        walking_speed_kmh: Uniform walking speed applied to every edge.

    Returns:
        GeoJSON FeatureCollection with a single Feature: the union of all
        isochrone polygons. Properties: time_minutes, walking_speed_kmh,
        location_count.
    """
    locs = _normalize_locations(locations)

    if time_minutes <= 0 or walking_speed_kmh <= 0:
        raise ValueError("time_minutes and walking_speed_kmh must be positive.")

    speed_ms = walking_speed_kmh / 3.6
    time_limit_s = time_minutes * 60.0
    max_reach_m = speed_ms * time_limit_s
    # Graph radius = max walkable distance + safety margin so edges aren't cut.
    margin_m = max_reach_m + GRAPH_BUFFER_MARGIN_M

    polygons_wgs: list = []

    for lat, lon in locs:
        log.info("Downloading walk graph around (%.6f, %.6f)...", lat, lon)
        try:
            G = ox.graph_from_point(
                (lat, lon),
                dist=margin_m,
                network_type="walk",
                simplify=True,
                truncate_by_edge=True,
                )

            Gp = ox.project_graph(G)

            for _, _, data in Gp.edges(data=True):
                data["travel_time"] = data.get("length", 0.0) / speed_ms

            node = ox.distance.nearest_nodes(G, X=lon, Y=lat)

            polygon_proj, n_nodes = _isochrone_geometry(
                Gp, node, time_limit_s, NODE_BUFFER_RADIUS_M
            )

            if polygon_proj is None or polygon_proj.is_empty:
                log.warning("No reachable nodes for (%.6f, %.6f); skipping.", lat, lon)
                continue

            # Each graph may be in a different UTM zone, so transform individually.
            to_wgs84 = Transformer.from_crs(
                Gp.graph["crs"], "EPSG:4326", always_xy=True
            ).transform
            polygons_wgs.append(shp_transform(to_wgs84, polygon_proj))
            log.info("  → %d reachable nodes", n_nodes)
        except Exception as e:
            log.error("Error processing (%.6f, %.6f): %s", lat, lon, e)

    if not polygons_wgs:
        return {"type": "FeatureCollection", "features": []}

    merged = unary_union(polygons_wgs)

    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": mapping(merged),
            "properties": {
                "time_minutes": time_minutes,
                "walking_speed_kmh": walking_speed_kmh,
                "location_count": len(locs),
            },
        }],
    }


def save_isochrones(geojson: dict, file_name: str) -> None:
    """Write a GeoJSON FeatureCollection to disk."""
    output_path = os.path.join('..\\data', file_name) 
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    log.info("Saved %d isochrone(s) to %s", len(geojson.get("features", [])), output_path)


def _parse_latlon(s: str) -> Coord:
    parts = s.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"Expected 'lat,lon', got: {s!r}"
        )
    return (float(parts[0].strip()), float(parts[1].strip()))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Walking isochrones from OpenStreetMap (OSMnx).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--locations", nargs="+", required=True, type=_parse_latlon,
        metavar="LAT,LON",
        help="One or more 'lat,lon' coordinate pairs, space-separated.",
    )
    parser.add_argument(
        "--time", type=float, required=True, metavar="MINUTES",
        help="Walking time budget in minutes.",
    )
    parser.add_argument(
        "--speed", type=float, default=DEFAULT_WALKING_SPEED_KMH, metavar="KMH",
        help=f"Walking speed in km/h (default: {DEFAULT_WALKING_SPEED_KMH}).",
    )
    parser.add_argument(
        "--output", default="isochrones.geojson",
        help="Output GeoJSON path (default: isochrones.geojson).",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_arg_parser().parse_args()

    locs = args.locations[0] if len(args.locations) == 1 else args.locations
    geojson = generate_isochrones(
        locations=locs,
        time_minutes=args.time,
        walking_speed_kmh=args.speed,
    )
    save_isochrones(geojson, args.output)


if __name__ == "__main__":
    main()
