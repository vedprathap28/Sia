"""Computer-use helpers that don't need a Windows desktop: screen diff, grid, vision locate parsing."""
from __future__ import annotations

from PIL import Image, ImageDraw


def test_change_ratio_and_grid():
    from backend.tools.computer import screen
    a = Image.new("RGB", (1280, 720), "white")
    b = a.copy()
    ImageDraw.Draw(b).rectangle([0, 0, 640, 360], fill="black")
    assert screen.change_ratio(a, a) == 0
    assert 0.2 < screen.change_ratio(a, b) < 0.3
    g = screen.with_grid(a)
    assert g.size == a.size and g.getpixel((640, 360)) != (255, 255, 255)
    assert len(screen.to_b64(a)) > 100


async def test_locate_scales_grid_coordinates(monkeypatch):
    from backend.tools.computer import screen
    from backend.tools.registry import load_all_tools
    img = Image.new("RGB", (1920, 1080), "white")
    monkeypatch.setattr(screen, "grab", lambda: img)

    async def fake_vision(q, im, grid=False, json_mode=False):
        assert grid
        return 'Sure: {"found": true, "x": 500, "y": 250, "label": "Save"}'
    monkeypatch.setattr(screen, "vision", fake_vision)
    r = await load_all_tools().execute("computer_locate", {"target": "Save button"})
    assert r.ok and r.data["x"] == 960 and r.data["y"] == 270


def test_risky_desktop_clicks_need_approval():
    from backend.tools.registry import load_all_tools
    reg = load_all_tools()
    assert reg.get("computer_click").risk_for({"x": 1, "y": 1, "target_description": "the Send button"}) == 3
    assert reg.get("computer_click").risk_for({"x": 1, "y": 1, "target_description": "the File menu"}) == 2
    assert reg.get("computer_hotkey").risk_for({"keys": "Alt+F4"}) == 3
    assert reg.get("uia_click").risk_for({"window_title": "Outlook", "name": "Send"}) == 3
