"""Coordinate mapping: display(px) → device(px) with letterbox + scaling."""

import pytest

from services.request_validators import map_display_to_device
from utils.app_errors import AppError


def test_exact_same_size_identity():
    x, y = map_display_to_device(100, 200, 390, 844, 390, 844)
    assert (x, y) == (100, 200)


def test_uniform_scale_no_letterbox():
    # Display is exactly half the device, same aspect → no bars.
    x, y = map_display_to_device(50, 100, 195, 422, 390, 844)
    assert (x, y) == (100, 200)


def test_letterbox_horizontal_bars():
    # Device 390x844 (tall) shown in a 390x844-ratio... use a wider display box
    # so there are left/right bars; a click on the left bar clamps to x=0.
    # display 800x844, device 390x844 → scale=1 (height-bound), content_w=390,
    # offset_x=(800-390)/2=205. Click at display x=205 → device x≈0.
    x, y = map_display_to_device(205, 100, 800, 844, 390, 844)
    assert x == 0
    assert 0 <= y <= 844


def test_click_in_bar_clamps_in_range():
    x, y = map_display_to_device(0, 0, 800, 844, 390, 844)
    assert 0 <= x < 390
    assert 0 <= y < 844


def test_center_maps_to_center():
    x, y = map_display_to_device(195, 422, 390, 844, 390, 844)
    assert abs(x - 195) <= 1
    assert abs(y - 422) <= 1


def test_zero_dimension_raises():
    with pytest.raises(AppError):
        map_display_to_device(10, 10, 0, 844, 390, 844)
