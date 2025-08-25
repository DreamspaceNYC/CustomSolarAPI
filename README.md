Purpose: Provide Solar-API-shaped outputs anywhere, including Nigeria, using open/global data.
- NASA POWER for irradiance (GHI, DNI, DHI).
- PVGIS for PV energy yield (monthly + annual).
- Optional roof polygon to estimate area and panel count.
- Returns JSON shaped like Google Solar API's `buildingInsights` and `solarPotential`.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run dev server
uvicorn main:app --reload --port 8000
```

Example request

```bash
curl -X POST http://localhost:8000/solar/estimate -H "Content-Type: application/json" -d '{
  "lat": 6.5244,
  "lon": 3.3792,
  "tilt_deg": 10,
  "azimuth_deg": 180,
  "panel_watts": 400,
  "packing_ratio": 0.85
}'
```

With a roof polygon (GeoJSON-like ring, lon/lat)

```
{
  "lat": 6.5244,
  "lon": 3.3792,
  "polygon": {
    "type": "Polygon",
    "coordinates": [[[3.3790,6.5242],[3.3796,6.5242],[3.3796,6.5246],[3.3790,6.5246],[3.3790,6.5242]]]
  },
  "tilt_deg": 10,
  "azimuth_deg": 180
}
```

Notes
•tilt_deg default 10. azimuth_deg default 180 (south-facing). Override if known.
•If polygon is included, area is computed in UTM and used to estimate panel count.
•PVGIS aspect convention uses degrees clockwise from south. 180 means north; 0 means south. We pass aspect=180-azimuth_deg to respect PVGIS convention.
•This service calls external APIs (internet required). No keys needed.

### `schema.json`
```json
{
  "type": "object",
  "properties": {
    "buildingInsights": {
      "type": "object",
      "properties": {
        "imageryQuality": { "type": "string" },
        "note": { "type": "string" }
      },
      "required": ["imageryQuality"]
    },
    "solarPotential": {
      "type": "object",
      "properties": {
        "panelCapacityWatts": { "type": "integer" },
        "maxArrayPanelsCount": { "type": "integer" },
        "recommendedPanelCount": { "type": "integer" },
        "capacityKw": { "type": "number" },
        "annualKwh": { "type": "number" },
        "monthlyKwh": {
          "type": "array",
          "items": { "type": "number" },
          "minItems": 12,
          "maxItems": 12
        },
        "roofSegments": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "id": { "type": "string" },
              "area_m2": { "type": "number" },
              "tilt_deg": { "type": "number" },
              "azimuth_deg": { "type": "number" }
            },
            "required": ["id"]
          }
        },
        "irradianceStats": {
          "type": "object",
          "properties": {
            "GHI_kWh_m2_yr": { "type": "number" },
            "DNI_kWh_m2_yr": { "type": "number" },
            "DHI_kWh_m2_yr": { "type": "number" }
          }
        },
        "assumptions": { "type": "object" },
        "dataSources": { "type": "array", "items": { "type": "string" } }
      },
      "required": [
        "panelCapacityWatts",
        "recommendedPanelCount",
        "capacityKw",
        "annualKwh",
        "monthlyKwh"
      ]
    }
  },
  "required": ["buildingInsights", "solarPotential"]
}
```
