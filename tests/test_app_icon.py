import struct
from pathlib import Path

ICON_PATH = Path(__file__).parents[1] / "src" / "assets" / "icon.png"


def test_ios_app_icon_is_full_size_and_opaque():
    """iOS app icons must not contain transparency, which renders as a white rim."""
    data = ICON_PATH.read_bytes()

    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    width, height = struct.unpack(">II", data[16:24])
    color_type = data[25]

    assert (width, height) == (1024, 1024)
    assert color_type in (0, 2), "app icon PNG must not have an alpha channel"
