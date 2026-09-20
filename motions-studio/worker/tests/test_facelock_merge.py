"""faceLock's merge must be able to keep Wan's own skin/hair texture.

Measured 20/09/2026 on .smoke/ab-face/, where original-02-camera-motion.mp4 and facelock-02.mp4
are the same 452 frames with and without the swap, so the face region can be compared pixel to
pixel: inswapper_128 replaces the whole aligned face with its own 128x128 render, which costs
39% of the region's high-frequency detail (mean Laplacian variance over frames 100-300:
150.7 -> 92.4) and wipes the hair strands falling over the forehead. That is the "waxy" half of
the complaint; CodeFormer (faceLockRestore) then puts detail back as etched edges, which is the
"harsh" half.

detail_keep fixes the first half at the source: transfer only the LOW-frequency part of what the
swap changed, so the identity shift keeps its full amplitude while Wan's high frequencies survive
untouched. Simulating it offline on all 452 frames restored the region to 150.2 (Wan: 150.7).
This is NOT faceLockBlend, which scales the merge mask and so dilutes identity and texture by the
same factor (tried 19/09/2026, reverted the same day).

numpy/cv2 only exist in the worker's own venv, so this skips on a bare `python3 -m unittest`.
"""
import importlib.util
import os
import sys
import unittest

try:
    import cv2
    import numpy as np
except ModuleNotFoundError:  # bare interpreter (the repo gate) — nothing to measure
    cv2 = None
    np = None

_SWAP_VIDEO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "facelock", "swap_video.py")


def _load_swap_video():
    spec = importlib.util.spec_from_file_location("facelock_swap_video", _SWAP_VIDEO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CROP = 128           # inswapper_128's fixed working resolution
SCALE = 2            # the aligned crop covers a 256x256 area of the frame
ORIGIN = (140, 180)  # where that area sits in the synthetic frame (x, y)


class _FakeSwapper:
    """Stands in for insightface's INSwapper: hands back an aligned crop + its affine matrix."""

    def __init__(self, crop):
        self._crop = crop
        self.paste_back_calls = 0

    def get(self, frame, tgt, src_face, paste_back=True):
        x, y = ORIGIN
        if paste_back:
            # Stand-in for upstream's own compositing: the crop lands over the face region.
            self.paste_back_calls += 1
            side = CROP * SCALE
            out = frame.copy()
            out[y:y + side, x:x + side] = cv2.resize(self._crop, (side, side),
                                                     interpolation=cv2.INTER_LINEAR)
            return out
        M = np.array([[1.0 / SCALE, 0.0, -x / SCALE],
                      [0.0, 1.0 / SCALE, -y / SCALE]], dtype=np.float64)
        return self._crop.copy(), M


def _texture_frame(seed=0):
    """Smooth mid-grey base + fine grain, i.e. a stand-in for Wan's rendered skin."""
    rng = np.random.default_rng(seed)
    base = np.full((640, 480, 3), 150.0, dtype=np.float32)
    grain = rng.normal(0.0, 12.0, base.shape).astype(np.float32)
    return np.clip(base + grain, 0, 255).astype(np.uint8)


def _flat_crop():
    """What the swap hands back: a different face colour, and no texture at all."""
    return np.full((CROP, CROP, 3), 190, dtype=np.uint8)


def _region(img):
    x, y = ORIGIN
    side = CROP * SCALE
    inset = side // 4  # stay well inside the eroded/blurred mask edge
    return img[y + inset:y + side - inset, x + inset:x + side - inset]


def _detail(img):
    grey = cv2.cvtColor(_region(img), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(cv2.Laplacian(grey, cv2.CV_32F).var())


def _tone(img):
    return float(_region(img).astype(np.float32).mean())


@unittest.skipIf(cv2 is None, "needs cv2/numpy (worker venv)")
class FaceLockDetailKeepTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load_swap_video()
        self.frame = _texture_frame()
        self.swapper = _FakeSwapper(_flat_crop())
        self.wan_detail = _detail(self.frame)
        self.wan_tone = _tone(self.frame)

    def _swap(self, **kw):
        return self.mod._swap_blended(self.swapper, self.frame, object(), object(), 1.0, **kw)

    def test_plain_merge_loses_wan_texture(self):
        """The baseline this fixes: today's full-strength merge flattens the region."""
        out = self._swap(detail_keep=0.0)
        self.assertLess(_detail(out), 0.25 * self.wan_detail)

    def test_detail_keep_restores_wan_texture(self):
        out = self._swap(detail_keep=2.0)
        self.assertGreater(_detail(out), 0.8 * self.wan_detail)

    def test_detail_keep_still_applies_the_identity_shift(self):
        """Band-limiting the change must not shrink it — that is what faceLockBlend did."""
        plain = self._swap(detail_keep=0.0)  # upstream fast path, the shift to beat
        kept = self._swap(detail_keep=2.0)
        shift_plain = _tone(plain) - self.wan_tone
        shift_kept = _tone(kept) - self.wan_tone
        self.assertGreater(shift_plain, 20.0)  # the fake crop is much lighter than the frame
        self.assertAlmostEqual(shift_kept, shift_plain, delta=0.05 * abs(shift_plain))

    def test_detail_keep_leaves_the_rest_of_the_frame_alone(self):
        out = self._swap(detail_keep=2.0)
        far = (slice(0, 80), slice(0, 80))
        self.assertTrue(np.array_equal(out[far], self.frame[far]))

    def test_blend_one_and_no_detail_keep_is_still_the_upstream_fast_path(self):
        """Today's default must stay bit-for-bit what it was — the reimplemented merge is opt-in."""
        self.mod._swap_blended(self.swapper, self.frame, object(), object(), 1.0)
        self.assertEqual(self.swapper.paste_back_calls, 1)
        self.mod._swap_blended(self.swapper, self.frame, object(), object(), 1.0, detail_keep=2.0)
        self.assertEqual(self.swapper.paste_back_calls, 1)


if __name__ == "__main__":
    sys.exit(unittest.main())
