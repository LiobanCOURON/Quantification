#!/usr/bin/env python3
"""Headless test for Window2 draggable markers (no display needed).

We exercise the real drag/hit-test logic from Window2Screen without Tk by
subclassing and stubbing only the few attributes/methods the new code touches.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screens.window2_mask import Window2Screen


class FakeScreen(Window2Screen):
    """Minimal harness: skip BaseScreen/Tk, set just what drag code needs."""

    def __init__(self):
        # Bypass super().__init__ (which needs a real app/Tk).
        class _Frame:
            def winfo_exists(self):
                return True
        self.frame = _Frame()
        self.labels = {}
        self.images = {}
        self.viewports = {}
        self.marker_points = {"tl": [], "tr": []}
        self.marker_active = False
        self.drag_state = None
        # Simulated displayed pixel sizes per key.
        self._disp = {}
        # Simulated label widget geometry per key.
        self._label_geom = {}

    def _displayed_image_size(self, key):
        return self._disp.get(key)

    def _screen_to_source_normalized(self, key, event):
        # Mirror the real projection math for a letterboxed label.
        img_w, img_h = self._disp[key]
        geom = self._label_geom[key]
        label_w, label_h = geom
        offset_x = (label_w - img_w) // 2
        offset_y = (label_h - img_h) // 2
        px = event.x - offset_x
        py = event.y - offset_y
        if px < 0 or py < 0 or px >= img_w or py >= img_h:
            return None, None
        nx_view = px / img_w
        ny_view = py / img_h
        nx0, ny0, nx1, ny1 = self.viewports.get(key, (0.0, 0.0, 1.0, 1.0))
        nx = nx0 + nx_view * (nx1 - nx0)
        ny = ny0 + ny_view * (ny1 - ny0)
        return nx, ny

    def _update_images(self):
        # Record that a re-render was requested during drag.
        self.update_called = True

    # Fake label widget that records cursor and geometry.
    class _Label:
        def __init__(self, w, h):
            self._w, self._h = w, h
            self.cursor = ""

        def winfo_width(self):
            return self._w

        def winfo_height(self):
            return self._h

        def config(self, **kw):
            if "cursor" in kw:
                self.cursor = kw["cursor"]


def make_event(x, y):
    class E:
        pass
    e = E()
    e.x = x
    e.y = y
    return e


# ---- setup ----------------------------------------------------------------
s = FakeScreen()
IMG_W, IMG_H = 700, 500          # displayed image size (tl)
LABEL_W, LABEL_H = 720, 520      # label is slightly bigger (letterboxed)
s._disp["tl"] = (IMG_W, IMG_H)
s._label_geom["tl"] = (LABEL_W, LABEL_H)
s.labels["tl"] = FakeScreen._Label(LABEL_W, LABEL_H)
s.viewports["tl"] = (0.0, 0.0, 1.0, 1.0)

# Two markers at normalized coords -> pixel centers.
# marker 0 at (0.2, 0.3): px = 0.2*700=140 ; py = 0.3*500=150
# marker 1 at (0.6, 0.7): px = 0.6*700=420 ; py = 0.7*500=350
s.marker_points["tl"] = [(0.2, 0.3), (0.6, 0.7)]

fails = []

# ---- Test 1: hit-test detects marker 0 near its center --------------------
ev = make_event(140 + (LABEL_W - IMG_W)//2, 150 + (LABEL_H - IMG_H)//2)
hit = s._hit_test_marker("tl", ev)
if hit != 0:
    fails.append(f"hit-test center of marker0 -> expected 0, got {hit}")

# ---- Test 2: hit-test misses empty space ----------------------------------
ev = make_event(10 + (LABEL_W - IMG_W)//2, 10 + (LABEL_H - IMG_H)//2)
hit = s._hit_test_marker("tl", ev)
if hit is not None:
    fails.append(f"hit-test empty space -> expected None, got {hit}")

# ---- Test 3: drag marker 0 by holding + moving --------------------------
# Press on marker 0.
press = make_event(140 + (LABEL_W - IMG_W)//2, 150 + (LABEL_H - IMG_H)//2)
s.update_called = False
s._on_image_button1("tl", press)
if s.drag_state is None or s.drag_state["index"] != 0:
    fails.append(f"drag start -> drag_state={s.drag_state}")
else:
    # Move cursor to new pixel position (shift +100px x, +50px y within image).
    new_px = 240
    new_py = 200
    move = make_event(new_px + (LABEL_W - IMG_W)//2, new_py + (LABEL_H - IMG_H)//2)
    s._on_image_drag_motion("tl", move)
    got = s.marker_points["tl"][0]
    exp_nx = new_px / IMG_W
    exp_ny = new_py / IMG_H
    if abs(got[0] - exp_nx) > 1e-6 or abs(got[1] - exp_ny) > 1e-6:
        fails.append(f"drag move -> got {got}, expected ({exp_nx},{exp_ny})")
    if not s.update_called:
        fails.append("drag move did not trigger _update_images()")
    # Release.
    s._on_image_drag_end("tl")
    if s.drag_state is not None:
        fails.append("drag end did not clear drag_state")
    if s.labels["tl"].cursor != "":  # marker_active is False -> cursor reset to ""
        fails.append(f"cursor after drag end -> {s.labels['tl'].cursor}")

# ---- Test 4: press on empty space (no marker mode) does not place --------
s.marker_active = False
before = len(s.marker_points["tl"])
ev = make_event(10 + (LABEL_W - IMG_W)//2, 10 + (LABEL_H - IMG_H)//2)
s._on_image_button1("tl", ev)
if len(s.marker_points["tl"]) != before:
    fails.append("press on empty space placed a point when marker mode off")

# ---- Test 5: hit tolerance near edge of dot ------------------------------
# After Test 3, marker 0 sits at pixel (240, 200).
# radius = max(4, min(700,500)//35) = 14 ; tol = 18 -> test at +18px.
mx, my = 240, 200
ev = make_event((mx + 18) + (LABEL_W - IMG_W)//2, (my) + (LABEL_H - IMG_H)//2)
hit = s._hit_test_marker("tl", ev)
if hit != 0:
    fails.append(f"hit within tolerance -> expected 0, got {hit}")

if fails:
    print("FAILURES:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL DRAG TESTS PASSED")
