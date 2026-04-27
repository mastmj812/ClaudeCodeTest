"""
Geospatial utilities: CRS handling, shapefile reading, spatial joins, distance math.
"""

import io
import zipfile
import tempfile
import os
import math
import numpy as np
import pandas as pd

try:
    import geopandas as gpd
    from shapely.geometry import Point
    HAS_GEO = True
except ImportError:
    HAS_GEO = False

# EPSG:3083 — Texas Centric Albers Equal Area (for area calculations in TX)
TX_ALBERS_EPSG = 3083
WGS84_EPSG = 4326


def geopandas_available() -> bool:
    return HAS_GEO


def read_shapefile_zip(file) -> "gpd.GeoDataFrame":
    """
    Read a shapefile from a .zip upload (must contain .shp + sidecar files).
    Returns a GeoDataFrame in WGS84 (EPSG:4326).
    """
    if not HAS_GEO:
        raise ImportError("geopandas is required for shapefile support. Run: pip install geopandas")

    content = file.read() if hasattr(file, "read") else open(file, "rb").read()

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = zf.namelist()
        shp_files = [n for n in names if n.lower().endswith(".shp")]
        if not shp_files:
            raise ValueError("Zip file does not contain a .shp file.")

        with tempfile.TemporaryDirectory() as tmpdir:
            zf.extractall(tmpdir)
            shp_path = os.path.join(tmpdir, shp_files[0])
            gdf = gpd.read_file(shp_path)

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=WGS84_EPSG)
    else:
        gdf = gdf.to_crs(epsg=WGS84_EPSG)

    return gdf


def wells_in_polygon(wells_df: pd.DataFrame, polygon_gdf: "gpd.GeoDataFrame") -> pd.DataFrame:
    """
    Return rows of wells_df whose lat/lon fall within any geometry in polygon_gdf.
    wells_df must have 'latitude' and 'longitude' columns.
    """
    if not HAS_GEO:
        raise ImportError("geopandas is required for shapefile filtering.")

    valid = wells_df.dropna(subset=["latitude", "longitude"])
    if valid.empty:
        return wells_df.iloc[0:0]

    gdf_wells = gpd.GeoDataFrame(
        valid,
        geometry=gpd.points_from_xy(valid["longitude"], valid["latitude"]),
        crs=WGS84_EPSG,
    )
    union_poly = polygon_gdf.to_crs(epsg=WGS84_EPSG).union_all()
    mask = gdf_wells.geometry.within(union_poly)
    return valid[mask.values].copy()


def wells_within_radius(
    wells_df: pd.DataFrame,
    center_lat: float,
    center_lon: float,
    radius_miles: float,
) -> pd.DataFrame:
    """
    Filter wells_df to rows within radius_miles of (center_lat, center_lon).
    Uses haversine distance — no external library needed.
    """
    valid = wells_df.dropna(subset=["latitude", "longitude"]).copy()
    if valid.empty:
        return valid

    valid["_dist_mi"] = haversine_miles(
        center_lat, center_lon,
        valid["latitude"].values,
        valid["longitude"].values,
    )
    result = valid[valid["_dist_mi"] <= radius_miles].copy()
    result = result.drop(columns=["_dist_mi"])
    return result


def haversine_miles(
    lat1: float, lon1: float,
    lat2, lon2,
) -> np.ndarray:
    """Vectorized haversine distance in miles."""
    R = 3958.8  # Earth radius in miles
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2 = np.radians(np.asarray(lat2, dtype=float))
    lon2 = np.radians(np.asarray(lon2, dtype=float))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + math.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(a))


def polygon_area_acres(polygon_gdf: "gpd.GeoDataFrame") -> float:
    """Return total area of a GeoDataFrame's geometries in acres using TX Albers projection."""
    if not HAS_GEO:
        return 640.0
    projected = polygon_gdf.to_crs(epsg=TX_ALBERS_EPSG)
    area_m2 = projected.geometry.area.sum()
    return area_m2 * 0.000247105  # m² → acres


def centroid_latlon(polygon_gdf: "gpd.GeoDataFrame") -> tuple[float, float]:
    """Return (lat, lon) centroid of the polygon in WGS84."""
    if not HAS_GEO:
        return (31.5, -104.0)
    wgs = polygon_gdf.to_crs(epsg=WGS84_EPSG)
    union = wgs.union_all()
    return (union.centroid.y, union.centroid.x)
