import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
except ImportError:  # pragma: no cover
    Image = None


def make_picture(seed: int, size=(640, 480)) -> "Image.Image":
    """A random but photo-like picture: gradient background plus shapes."""
    rnd = random.Random(seed)
    w, h = size
    base = Image.linear_gradient("L").resize(size).convert("RGB")
    tint = Image.new("RGB", size, tuple(rnd.randrange(256) for _ in range(3)))
    im = Image.blend(base, tint, 0.5)
    draw = ImageDraw.Draw(im)
    for _ in range(12):
        x0, y0 = rnd.randrange(w), rnd.randrange(h)
        x1, y1 = x0 + rnd.randrange(40, 260), y0 + rnd.randrange(40, 200)
        colour = tuple(rnd.randrange(256) for _ in range(3))
        if rnd.random() < 0.5:
            draw.ellipse((x0, y0, x1, y1), fill=colour)
        else:
            draw.rectangle((x0, y0, x1, y1), fill=colour)
    return im.filter(ImageFilter.GaussianBlur(3))


@unittest.skipIf(Image is None, "Pillow is not installed")
class SimilarImageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.a = os.path.join(self.tmp, "Originals")
        self.b = os.path.join(self.tmp, "Downloads")
        os.makedirs(self.a)
        os.makedirs(os.path.join(self.b, "sub"))
        p1, p2, p3 = make_picture(1), make_picture(2), make_picture(3)
        p1.save(os.path.join(self.a, "beach.png"))
        p1.resize((320, 240)).save(os.path.join(self.b, "beach_small.jpg"), quality=70)
        p1.save(os.path.join(self.b, "sub", "IMG_1234.webp"), quality=60)
        ImageEnhance.Brightness(p1).enhance(1.1).save(
            os.path.join(self.b, "beach_edited.jpg"))
        p2.save(os.path.join(self.a, "mountain.jpg"), quality=95)
        p2.convert("L").save(os.path.join(self.b, "mountain_bw.png"))
        p3.save(os.path.join(self.a, "city.png"))  # No similar image.
        # Plain colours have identical difference hashes; the colour check
        # must stop these from being grouped with each other.
        Image.new("RGB", (200, 200), "red").save(os.path.join(self.a, "red.png"))
        Image.new("RGB", (200, 200), "blue").save(os.path.join(self.b, "blue.png"))
        with open(os.path.join(self.b, "broken.jpg"), "wb") as fh:
            fh.write(b"not really an image")

    def tearDown(self):
        self._tmp.cleanup()

    def names(self, group):
        return sorted(os.path.basename(f.path) for f in group.files)

    def test_groups_similar_images(self):
        result = core.find_similar_images([self.a, self.b])
        found = sorted(self.names(g) for g in result.groups)
        self.assertEqual(found, [
            ["IMG_1234.webp", "beach.png", "beach_edited.jpg", "beach_small.jpg"],
            ["mountain.jpg", "mountain_bw.png"],
        ])
        self.assertEqual(result.mode, core.MODE_SIMILAR)
        self.assertIn(os.path.join(self.b, "broken.jpg"),
                      [p for p, _ in result.errors])

    def test_dimensions_and_similarity(self):
        result = core.find_similar_images([self.a, self.b])
        beach = next(g for g in result.groups if len(g.files) == 4)
        small = next(f for f in beach.files if f.path.endswith("beach_small.jpg"))
        self.assertEqual((small.width, small.height), (320, 240))
        for f in beach.files:
            self.assertGreaterEqual(f.similarity, 90)

    def test_strict_threshold_finds_fewer(self):
        loose = core.find_similar_images([self.a, self.b], similarity=85)
        strict = core.find_similar_images([self.a, self.b], similarity=100)
        self.assertLessEqual(sum(len(g.files) for g in strict.groups),
                             sum(len(g.files) for g in loose.groups))

    def test_folder_priority_and_quality_keeper(self):
        result = core.find_similar_images([self.b, self.a])  # Downloads first
        beach = next(g for g in result.groups if len(g.files) == 4)
        keeper = core.choose_keeper(beach, core.DEFAULT_SIMILAR_TIEBREAK)
        self.assertTrue(keeper.path.startswith(self.b))
        self.assertNotIn("small", keeper.path)  # Best image within Downloads.
        keeper = core.choose_keeper(beach, core.DEFAULT_SIMILAR_TIEBREAK,
                                    prefer_quality=True)
        self.assertEqual(keeper.pixels, 640 * 480)

    def test_remove_similar(self):
        result = core.find_similar_images([self.a, self.b])
        plan = core.auto_plan(result.groups, core.DEFAULT_SIMILAR_TIEBREAK)
        dest = os.path.join(self.tmp, "removed")
        out = core.remove_duplicates(plan, core.METHOD_MOVE, dest, [self.a, self.b])
        self.assertEqual(len(out.removed), 4)
        self.assertTrue(os.path.exists(os.path.join(self.a, "beach.png")))
        self.assertTrue(os.path.exists(os.path.join(self.a, "mountain.jpg")))
        self.assertTrue(os.path.exists(os.path.join(dest, "Downloads", "sub",
                                                    "IMG_1234.webp")))

    def test_modified_file_not_removed(self):
        result = core.find_similar_images([self.a, self.b])
        plan = core.auto_plan(result.groups)
        victim = os.path.join(self.b, "mountain_bw.png")
        with open(victim, "ab") as fh:
            fh.write(b"changed")
        out = core.remove_duplicates(plan, core.METHOD_DELETE)
        self.assertTrue(os.path.exists(victim))
        self.assertIn(victim, [p for p, _ in out.skipped])

    def test_cancel(self):
        with self.assertRaises(core.Cancelled):
            core.find_similar_images([self.a, self.b], cancel=lambda: True)


if __name__ == "__main__":
    unittest.main()
