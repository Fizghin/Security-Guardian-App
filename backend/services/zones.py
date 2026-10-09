"""
Detection zones: areas of a camera's picture where people count. A person counts when the
point where they stand (bottom-centre of their box) is inside a zone, so someone walking past
behind a garden wall doesn't count just because their head shows above it.

Zones are polygons with corners in 0..1 picture coordinates. No zones = the whole picture.
"""
from models.domain import Detection

Polygon = list[list[float]]

MAX_CORNERS = 32
MIN_AREA = 0.001  # share of the picture; the dashboard uses the same limit
# Zones are stored in 0..1, so on a picture of another shape (a phone turned on its side) they
# cover different places. Small differences come from rounding the picture size.
SHAPE_TOLERANCE = 0.05


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


def _orient(a, b, c) -> int:
    turn = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (turn > 0) - (turn < 0)


def _touch(a, b, c, d) -> bool:
    """Whether the segments ab and cd share any point."""
    def between(p, q, r):  # r is on the line through p and q: is it on the segment?
        return min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[1] <= max(p[1], q[1])

    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and between(a, b, c)) or (o2 == 0 and between(a, b, d))
            or (o3 == 0 and between(c, d, a)) or (o4 == 0 and between(c, d, b)))


def clean_zone(polygon: Polygon) -> Polygon:
    """The zone with corners rounded to 4 decimals and repeated corners (a double click) removed.
    Raises ValueError when it can't be used: where an outline crosses itself, which parts are
    inside is a matter of convention, and a zone without area holds nobody."""
    if not 3 <= len(polygon) <= MAX_CORNERS:
        raise ValueError(f"A zone needs 3 to {MAX_CORNERS} corners")
    if any(len(p) != 2 or not (0 <= p[0] <= 1 and 0 <= p[1] <= 1) for p in polygon):
        raise ValueError("Zone corners must lie inside the picture")
    corners: Polygon = []
    for corner in ([round(x, 4), round(y, 4)] for x, y in polygon):
        if not corners or corner != corners[-1]:
            corners.append(corner)
    while len(corners) > 1 and corners[-1] == corners[0]:  # ended back on the first corner
        corners.pop()
    if len(corners) < 3:
        raise ValueError(f"A zone needs 3 to {MAX_CORNERS} corners")
    grid = [(round(x * 10000), round(y * 10000)) for x, y in corners]  # whole numbers keep the checks exact
    n = len(grid)
    for i in range(n):
        for j in range(i + 2, n):
            if (i, j) != (0, n - 1) and _touch(grid[i], grid[i + 1], grid[j], grid[(j + 1) % n]):
                raise ValueError("A zone's outline must not cross or touch itself")
    twice_area = abs(sum(grid[i - 1][0] * grid[i][1] - grid[i][0] * grid[i - 1][1] for i in range(n)))
    if twice_area < 2 * MIN_AREA * 10000 ** 2:
        raise ValueError("A zone is too small; outline a bigger area")
    return corners


def shape_changed(zones_aspect: float | None, picture_aspect: float | None) -> bool:
    """Whether the picture's shape (width / height) differs from the one the zones were drawn on."""
    return bool(zones_aspect and picture_aspect and abs(picture_aspect / zones_aspect - 1) > SHAPE_TOLERANCE)


def in_zones(detection: Detection, width: int, height: int, zones: list[Polygon]) -> bool:
    if not zones:
        return True
    x1, _, x2, y2 = detection.bbox
    foot_x, foot_y = (x1 + x2) / 2 / width, min(y2 / height, 0.999)
    return any(inside(foot_x, foot_y, zone) for zone in zones)


def keep_in_zones(detections: list[Detection], width: int, height: int, zones: list[Polygon]) -> list[Detection]:
    return [d for d in detections if in_zones(d, width, height, zones)] if zones else detections
