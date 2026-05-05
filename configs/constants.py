"""
configs/constants.py
---------------------
Define global constants
"""

# Default walking speed used when the caller does not supply one.
# OSMnx assigns speeds from OSM maxspeed tags when available; for foot
# networks these are usually absent, so a uniform default is applied instead.
DEFAULT_WALKING_SPEED_KMH = 4.32

# Extra radius (metres) added to the graph download buffer beyond the maximum
# straight-line reach of the isochrone. This guards against edge effects where
# the nearest graph node sits outside the downloaded area.
GRAPH_BUFFER_MARGIN_M = 200

# Radius (metres) of the buffer drawn around each reachable node to form the
# isochrone polygon. Larger values produce smoother, more generalised shapes.
NODE_BUFFER_RADIUS_M = 25

# Approximate conversion factor: 1 degree of latitude ≈ 111 320 metres.
# Used to convert metre buffers into degree units for Shapely operations
# on unprojected (EPSG:4326) coordinates.
METRES_PER_DEGREE_LAT = 111_320.0