"""Chicago community-area code -> name resolution (see
app/chicago_community_areas.py). Source: City of Chicago Data Portal,
"Boundaries - Community Areas" dataset (igwz-8jzy).
"""

from app.chicago_community_areas import COMMUNITY_AREA_NAMES, resolve_community_area_name


def test_resolves_code_25_to_austin():
    assert resolve_community_area_name("25") == "Austin"


def test_resolves_none_code_to_none():
    assert resolve_community_area_name(None) is None


def test_unrecognized_code_falls_back_to_none_without_crashing():
    # Chicago community areas are numbered 1-77; "9999" is not a real
    # code -- resolution must degrade gracefully, not raise, so a
    # malformed/future/out-of-range source value can't crash the API.
    assert resolve_community_area_name("9999") is None


def test_all_77_official_community_areas_present():
    assert len(COMMUNITY_AREA_NAMES) == 77
    assert set(COMMUNITY_AREA_NAMES) == {str(n) for n in range(1, 78)}
