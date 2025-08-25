from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
import math, requests
from shapely.geometry import Polygon, shape, mapping
from shapely.ops import transform as shp_transform
from pyproj import CRS, Transformer

app = FastAPI(title="Solar Alt Service", version="0.1.0")

# ---------- Models ----------
class GeoJSONPolygon(BaseModel):
    type: Literal["Polygon"]
    coordinates: List[List[List[float]]]  # [[[lon,lat],...]]

class EstimateRequest(BaseModel):
    lat: float
    lon: float
    polygon: Optional[GeoJSONPolygon] = None
    tilt_deg: Optional[float] = Field(default=None, ge=0, le=90)
    azimuth_deg: Optional[float] = Field(default=None, ge=0, le=360)
    system_kw: Optional[float] = Field(default=None, gt=0)
    panel_watts: Optional[int] = Field(default=400, gt=0)
    panel_area_m2: Optional[float] = Field(default=1.95, gt=0)  # 400W ~ 1.95 m2
    packing_ratio: Optional[float] = Field(default=0.85, gt=0, le=1.0)
    losses_percent: Optional[float] = Field(default=14.0, ge=0, le=40)

# ---------- Helpers ----------
def utm_crs_for_lon(lon: float, lat: float) -> CRS:
    zone = int(math.floor((lon + 180) / 6) + 1)
    south = lat < 0
    return CRS.from_epsg(32700 + zone if south else 32600 + zone)

def polygon_area_m2(poly_lonlat: Polygon) -> float:
    # Project to UTM for area
    centroid = poly_lonlat.centroid
    crs_utm = utm_crs_for_lon(centroid.x, centroid.y)
    transformer = Transformer.from_crs("EPSG:4326", crs_utm, always_xy=True)
    proj_poly = shp_transform(lambda x, y: transformer.transform(x, y), poly_lonlat)
    return proj_poly.area

def default_tilt(lat: float) -> float:
    # Simple heuristic for tropics
    return 10.0

def default_azimuth(lat: float) -> float:
    # North hemisphere -> face south
    return 180.0

def fetch_power_irradiance(lat: float, lon: float) -> Dict[str, float]:
    # NASA POWER climatology
    url = (
        "https://power.larc.nasa.gov/api/temporal/climatology/point"
        f"?parameters=ALLSKY_SFC_SW_DWN,DNI,DHI&community=solar&longitude={lon}&latitude={lat}&format=JSON"
    )
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"NASA POWER error {r.status_code}")
    data = r.json()
    params = data.get("properties", {}).get("parameter", {})
    # Monthly kWh/m2/day -> convert to annual kWh/m2
    def annual(param_key: str) -> float:
        monthly = params.get(param_key, {})
        if not monthly:
            return None
        # POWER climatology returns monthly means (kWh/m2/day). Multiply by days per month and sum.
        days = [31,28,31,30,31,30,31,31,30,31,30,31]
        vals = []
        for i, m in enumerate(["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]):
            v = monthly.get(m)
            if v is None:
                continue
            vals.append(v * days[i])
        return float(sum(vals)) if vals else None

    return {
        "GHI_kWh_m2_yr": annual("ALLSKY_SFC_SW_DWN"),
        "DNI_kWh_m2_yr": annual("DNI"),
        "DHI_kWh_m2_yr": annual("DHI"),
    }

def pvgis_energy(lat: float, lon: float, tilt_deg: float, azimuth_deg: float, peak_kw: float, losses_percent: float):
    # PVGIS aspect convention: 0 = South, 90 = West, -90 = East, 180/-180 = North
    # Convert standard azimuth (0=north, 90=east, 180=south, 270=west) to PVGIS aspect
    # Standard 180 (south) -> aspect 0
    aspect = 180.0 - azimuth_deg
    url = (
        "https://re.jrc.ec.europa.eu/api/v5_2/PVcalc"
        f"?lat={lat}&lon={lon}&peakpower={peak_kw}&loss={losses_percent}"
        f"&angle={tilt_deg}&aspect={aspect}&outputformat=json"
    )
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"PVGIS error {r.status_code}")
    data = r.json()
    # Extract monthly and annual energy (kWh)
    try:
        monthly = [data["outputs"]["monthly"]["fixed"]["E_d"][str(i)].get("E_m", None) for i in range(1,13)]
        # Some responses use array
    except Exception:
        # Fallback to array format if present
        monthly_list = data.get("outputs", {}).get("monthly", {}).get("fixed", [])
        monthly = [m.get("E_m") for m in monthly_list] if isinstance(monthly_list, list) else [None]*12
    # Annual
    try:
        annual = data["outputs"]["totals"]["fixed"]["E_y"]
    except Exception:
        annual = sum([v for v in monthly if isinstance(v, (int, float))])

    # Backfill None with 0
    monthly = [float(v) if isinstance(v, (int,float)) else 0.0 for v in monthly]
    annual = float(annual) if isinstance(annual, (int,float)) else float(sum(monthly))
    return monthly, annual, data

@app.post("/solar/estimate")
def estimate(req: EstimateRequest):
    lat, lon = req.lat, req.lon
    tilt = req.tilt_deg if req.tilt_deg is not None else default_tilt(lat)
    az = req.azimuth_deg if req.azimuth_deg is not None else default_azimuth(lat)
    panel_watts = req.panel_watts or 400
    panel_area = req.panel_area_m2 or 1.95
    pack = req.packing_ratio or 0.85

    # Roof segment
    segments = []
    total_area = None
    if req.polygon:
        poly = shape(req.polygon.model_dump())
        area_m2 = polygon_area_m2(poly)
        total_area = area_m2
        segments.append({
            "id": "seg_0",
            "area_m2": round(area_m2, 2),
            "tilt_deg": round(tilt, 2),
            "azimuth_deg": round(az, 1)
        })

    # Panel count and system size
    if total_area:
        max_panels = int((total_area * pack) / panel_area)
    else:
        max_panels = None

    # If user supplied system_kw use it, else derive from area or default 3 kW
    if req.system_kw:
        system_kw = req.system_kw
        rec_panels = int(round((system_kw * 1000) / panel_watts))
    else:
        if max_panels:
            rec_panels = max_panels
            system_kw = rec_panels * panel_watts / 1000.0
        else:
            system_kw = 3.0
            rec_panels = int(round((system_kw * 1000) / panel_watts))

    # External data
    irr = fetch_power_irradiance(lat, lon)
    monthly_kwh, annual_kwh, _ = pvgis_energy(lat, lon, tilt, az, system_kw, req.losses_percent or 14.0)

    resp = {
        "buildingInsights": {
            "imageryQuality": "EXTERNAL",
            "note": "Computed from NASA POWER + PVGIS. Not Google Solar API."
        },
        "solarPotential": {
            "panelCapacityWatts": panel_watts,
            "maxArrayPanelsCount": max_panels if max_panels is not None else rec_panels,
            "recommendedPanelCount": rec_panels,
            "capacityKw": round(system_kw, 3),
            "annualKwh": round(annual_kwh, 1),
            "monthlyKwh": [round(v, 1) for v in monthly_kwh],
            "roofSegments": segments,
            "irradianceStats": irr,
            "assumptions": {
                "panel_area_m2": panel_area,
                "packing_ratio": pack,
                "tilt_deg": tilt,
                "azimuth_deg": az,
                "losses_percent": req.losses_percent or 14.0
            },
            "dataSources": [
                "NASA POWER Climatology",
                "PVGIS v5_2 PVcalc"
            ]
        }
    }
    return resp
