# osmutil
Tools to extract information (originaly related with mobility or transportation) from OpenStreetMaps

---

## Project Structure

```
osmutil/
├── configs/
│   ├── constants.py                  # Global variables to be set before execution
│
├── src/
│   ├── isochrone_generator.py        # Generates isochrones. Returns a GeoJSON
│
    ├── data/                         # Stores the resulting data
│
├── requirements.txt
└── README.md                         # This file
```

---

## Quickstart

### 1. Setup environment

```python
pip install -r requirements.txt
```

### 2. Isochrones


```bash
# Generate isochrones arround lat,lon pairs
python isochrone_generator.py --locations 4.6588,-74.1313 4.6097,-74.0817 --time 10 --speed 4.3 --output isocronas.geojson
```


```python
# Run from src
import isochrone_generator

from configs.constants import DEFAULT_WALKING_SPEED_KMH

locs = [(4.6588,-74.1313), (4.6097,-74.0817)]

geojson = isochrone_generator.generate_isochrones(
        locations=locs,
        time_minutes=10,
        walking_speed_kmh=DEFAULT_WALKING_SPEED_KMH,
    )

isochrone_generator.save_isochrones(geojson, "isocronas_test.geojson")
```