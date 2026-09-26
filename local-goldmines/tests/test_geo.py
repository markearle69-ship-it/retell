import pytest

from app.geo import bearing_degrees, distance_miles, sector_for


@pytest.mark.parametrize(
    "bearing,sector",
    [(0, "N"), (22.4, "N"), (22.5, "NE"), (90, "E"), (135, "SE"), (180, "S"),
     (225, "SW"), (270, "W"), (315, "NW"), (337.4, "NW"), (337.5, "N"), (359.9, "N")],
)
def test_sector_for(bearing, sector):
    assert sector_for(bearing) == sector


def test_distance_and_bearing_manchester_to_altrincham():
    # Altrincham is ~8 miles south-west of Manchester city centre.
    d = distance_miles(53.4794, -2.2453, 53.3876, -2.3487)
    assert 7 < d < 8.5
    assert sector_for(bearing_degrees(53.4794, -2.2453, 53.3876, -2.3487)) == "SW"
