import numpy as np
import pytest

from models.domain import Detection
from services.camera_service import draw_overlay
from services.settings_service import SettingsError, SettingsService
from services.zones import in_zones, inside, keep_in_zones

SQUARE = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
# An L along the bottom and the left edge
L_SHAPE = [[0.0, 0.0], [0.3, 0.0], [0.3, 0.7], [1.0, 0.7], [1.0, 1.0], [0.0, 1.0]]


@pytest.mark.parametrize("x, y, polygon, expected", [
    (0.25, 0.25, SQUARE, True),
    (0.75, 0.25, SQUARE, False),
    (0.1, 0.2, L_SHAPE, True),
    (0.8, 0.9, L_SHAPE, True),
    (0.8, 0.3, L_SHAPE, False),   # in the notch
])
def test_inside(x, y, polygon, expected):
    assert inside(x, y, polygon) is expected


def person(x1, y1, x2, y2):
    return Detection(class_name="person", confidence=0.9, bbox=[x1, y1, x2, y2])


def test_people_count_where_they_stand():
    # 1000x1000 picture, zone = the lower half (the garden), the street is above it
    garden = [[[0.0, 0.5], [1.0, 0.5], [1.0, 1.0], [0.0, 1.0]]]
    in_garden = person(100, 300, 200, 900)      # head above the line, feet in the garden
    on_street = person(500, 50, 600, 400)
    assert in_zones(in_garden, 1000, 1000, garden)
    assert not in_zones(on_street, 1000, 1000, garden)
    assert keep_in_zones([in_garden, on_street], 1000, 1000, garden) == [in_garden]
    assert keep_in_zones([in_garden, on_street], 1000, 1000, []) == [in_garden, on_street], "no zones: everything"


def test_feet_on_the_bottom_edge_still_count():
    whole_bottom = [[[0.0, 0.5], [1.0, 0.5], [1.0, 1.0], [0.0, 1.0]]]
    assert in_zones(person(100, 600, 200, 1000), 1000, 1000, whole_bottom)


@pytest.mark.parametrize("zones", [
    [[[0, 0], [1, 0]]],                       # too few corners
    [[[0, 0], [1, 0], [1, 1.2]]],             # outside the picture
    [[[0, 0], [1, 0], [1]]],                  # not a point
    [SQUARE] * 9,                             # too many zones
])
def test_invalid_zones_are_rejected(tmp_path, zones):
    svc = SettingsService(tmp_path / "settings.json")
    with pytest.raises(SettingsError):
        svc.update_camera("cam1", {"zones": zones})


def test_zones_are_saved_and_survive_a_restart(tmp_path):
    path = tmp_path / "settings.json"
    SettingsService(path).update_camera("cam1", {"zones": [[[0.123456, 0], [1, 0], [1, 1]]]})
    assert SettingsService(path).get().camera("cam1").zones == [[[0.1235, 0.0], [1.0, 0.0], [1.0, 1.0]]]


def test_overlay_outlines_zones():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    out = draw_overlay(frame, [], "Door", True, zones=[SQUARE])
    assert out[25, 98:103].any(), "the zone's right edge is drawn at x = 0.5"
    assert not out[75, 150].any(), "outside the zone stays untouched"
