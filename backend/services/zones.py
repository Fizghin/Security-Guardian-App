"""
Detection zones: areas of a camera's picture where people count. A person counts when the
point where they stand (bottom-centre of their box) is inside a zone, so someone walking past
behind a garden wall doesn't count just because their head shows above it.

Zones are polygons with corners in 0..1 picture coordinates. No zones = the whole picture.
"""
from models.domain import Detection

Polygon = list[list[float]]


def inside(x: float, y: float, polygon: Polygon) -> bool:
    """Even-odd ray casting; points exactly on an edge may fall either way."""
    hit = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def in_zones(detection: Detection, width: int, height: int, zones: list[Polygon]) -> bool:
    if not zones:
        return True
    x1, _, x2, y2 = detection.bbox
    foot_x, foot_y = (x1 + x2) / 2 / width, min(y2 / height, 0.999)
    return any(inside(foot_x, foot_y, zone) for zone in zones)


def keep_in_zones(detections: list[Detection], width: int, height: int, zones: list[Polygon]) -> list[Detection]:
    return [d for d in detections if in_zones(d, width, height, zones)] if zones else detections
