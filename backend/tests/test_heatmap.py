from models.domain import Detection
from services.heatmap_service import COLS, ROWS, HeatmapStore, Trails


def person(x1, y1, x2, y2, track, status="unknown", simulated=False):
    return Detection(confidence=0.9, bbox=[x1, y1, x2, y2], track_id=track, status=status, simulated=simulated)


def test_counts_feet_once_per_second_per_person(tmp_path):
    store = HeatmapStore(tmp_path / "heat.json")
    d = person(90, 50, 110, 200, track=1)
    for i in range(10):
        store.add("cam1", [d], 200, 200, now=100.0 + i * 0.25)  # 2.5 s of standing still
    h = store.get("cam1")
    assert h["samples"] == 3 and h["strangers"] == 3
    grid = h["grid"]
    assert len(grid) == ROWS and len(grid[0]) == COLS
    assert grid[ROWS - 1][COLS // 2] == 3  # bottom middle: where their feet are
    assert h["hotspots"][0]["where"] == "bottom centre"


def test_ignores_test_people_and_persists(tmp_path):
    path = tmp_path / "heat.json"
    store = HeatmapStore(path)
    store.add("cam1", [person(0, 0, 20, 20, 1, simulated=True), person(0, 0, 20, 20, 2, status="known")], 200, 200, 5.0)
    store.save()
    again = HeatmapStore(path)
    h = again.get("cam1")
    assert h["samples"] == 1 and h["strangers"] == 0 and h["grid"][2][2] == 1
    again.reset("cam1")
    assert HeatmapStore(path).get("cam1")["samples"] == 0


def test_trails_follow_tracks_and_expire():
    trails = Trails()
    for i in range(5):
        trails.update([person(10 * i, 0, 10 * i + 20, 100, track=7)], 200, 100, now=float(i))
    (status, points), = trails.lines(4.0)
    assert status == "unknown" and len(points) == 5 and points[0][0] < points[-1][0]
    trails.update([], 200, 100, now=20.0)
    assert trails.lines(20.0) == []
