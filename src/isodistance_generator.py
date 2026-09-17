"""
isodistance_generator.py
========================
Generate walking isodistance polygons (GeoJSON) for one or more points using
OSMnx and OpenStreetMap data only. No API keys required.

Optimized for sparse input data points: one small graph download per input point,
instead of one giant bbox covering all of them).
The resulting isodistance polygons are merged into a single geometry at the end.

Main methods:
    generate_isodistances(locations, distance_m) -> dict
    build_points_layer(locations) -> dict
    save_isodistances(geojson, output_path) -> None

CLI:
    python isodistance_generator.py \\
        --locations 4.6588,-74.1313 4.6097,-74.0817 \\
        --distance 500 --output isodistancias.geojson
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


def _isodistance_geometry(
    Gp: nx.MultiDiGraph,
    source: int,
    distance_limit_m: float,
    buffer_m: float,
):
    """Return a Shapely polygon (in the graph's projected CRS) and node count."""
    # Cut the network by walked distance along edges, not by travel time.
    lengths = nx.single_source_dijkstra_path_length(
        Gp, source, cutoff=distance_limit_m, weight="length"
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


def generate_isodistances(
    locations,
    distance_m: float,
) -> dict:
    """Build walking isodistances for one or more points and return their union.

    Downloads a small graph around each point individually (better than one
    giant bbox when locations are sparse). The per-point isodistance polygons
    are merged into a single geometry before being returned.

    Args:
        locations: A (lat, lon) tuple or an iterable of (lat, lon) tuples.
        distance_m: Network distance budget in metres, walked along edges.

    Returns:
        GeoJSON FeatureCollection with one Feature per location: the
        isodistance polygon. Properties: distance_m, reachable_nodes, lat, lon.
    """
    locs = _normalize_locations(locations)

    if distance_m <= 0:
        raise ValueError("distance_m must be positive.")

    # Graph radius = max walkable distance + safety margin so edges aren't cut.
    margin_m = distance_m + GRAPH_BUFFER_MARGIN_M

    features_data: list = []

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

            node = ox.distance.nearest_nodes(G, X=lon, Y=lat)

            polygon_proj, n_nodes = _isodistance_geometry(
                Gp, node, distance_m, NODE_BUFFER_RADIUS_M
            )

            if polygon_proj is None or polygon_proj.is_empty:
                log.warning("No reachable nodes for (%.6f, %.6f); skipping.", lat, lon)
                continue

            # Each graph may be in a different UTM zone, so transform individually.
            to_wgs84 = Transformer.from_crs(
                Gp.graph["crs"], "EPSG:4326", always_xy=True
            ).transform
            polygon_wgs = shp_transform(to_wgs84, polygon_proj)
            features_data.append({
                "polygon": polygon_wgs,
                "lat": lat,
                "lon": lon,
                "reachable_nodes": n_nodes,
            })
            log.info("  → %d reachable nodes", n_nodes)
        except Exception as e:
            log.error("Error processing (%.6f, %.6f): %s", lat, lon, e)
    if not features_data:
        return {"type": "FeatureCollection", "features": []}

    features = [
        {
            "type": "Feature",
            "geometry": mapping(item["polygon"]),
            "properties": {
                "lat": item["lat"],
                "lon": item["lon"],
                "reachable_nodes": item["reachable_nodes"],
                "distance_m": distance_m,
            },
        }
        for item in features_data
    ]

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def build_points_layer(locations) -> dict:
    """Return a GeoJSON FeatureCollection with the input points used."""
    locs = _normalize_locations(locations)
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"lat": lat, "lon": lon},
        }
        for lat, lon in locs
    ]
    return {"type": "FeatureCollection", "features": features}


def save_isodistances(geojson: dict, file_name: str) -> None:
    """Write a GeoJSON FeatureCollection to disk."""
    output_path = os.path.join('..\\data', file_name)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    log.info("Saved %d feature(s) to %s", len(geojson.get("features", [])), output_path)


def _parse_latlon(s: str) -> Coord:
    parts = s.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"Expected 'lat,lon', got: {s!r}"
        )
    return (float(parts[0].strip()), float(parts[1].strip()))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Walking isodistances from OpenStreetMap (OSMnx).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--locations", nargs="+", required=True, type=_parse_latlon,
        metavar="LAT,LON",
        help="One or more 'lat,lon' coordinate pairs, space-separated.",
    )
    parser.add_argument(
        "--distance", type=float, nargs="+", required=True, metavar="METRES",
        help="Network distance budget in metres.",
    )
    parser.add_argument(
        "--output", default="isodistances.geojson",
        help="Output GeoJSON path (default: isodistances.geojson).",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_arg_parser().parse_args()

    locs = args.locations[0] if len(args.locations) == 1 else args.locations

    base = Path(args.output)

    # Capa con los puntos de entrada usados
    points_geojson = build_points_layer(locs)
    points_path = base.with_stem(f"{base.stem}_points")
    save_isodistances(points_geojson, str(points_path))

    for d in args.distance:
        geojson = generate_isodistances(
            locations=locs,
            distance_m=d,
        )

        # Incluye la distancia en metros en el nombre del archivo de salida
        d_str = f"{d:g}".replace(".", "_") + "m"
        out_path = base.with_stem(f"{base.stem}_{d_str}")

        save_isodistances(geojson, str(out_path))


if __name__ == "__main__":
    main()
