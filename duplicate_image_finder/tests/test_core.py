import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402


def write(path, data: bytes, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class CoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.a = os.path.join(self.tmp, "A")
        self.b = os.path.join(self.tmp, "B")
        self.img = b"\x89PNG fake image " * 100
        big = os.urandom(200 * 1024)  # Bigger than the partial-hash window.
        self.big = big
        now = time.time()
        write(os.path.join(self.a, "holiday.png"), self.img, now - 100)
        write(os.path.join(self.b, "copy of holiday.png"), self.img, now)
        write(os.path.join(self.b, "sub", "IMG_0001.png"), self.img, now - 50)
        write(os.path.join(self.a, "big.jpg"), big)
        write(os.path.join(self.b, "big-renamed.jpg"), big)
        # Same size and same first 64 KB as big, but different at the end.
        write(os.path.join(self.b, "almost.jpg"), big[:-1] + b"X")
        write(os.path.join(self.a, "unique.png"), b"something else")
        write(os.path.join(self.b, "notes.txt"), self.img)  # Not an image.
        write(os.path.join(self.b, "empty1.png"), b"")
        write(os.path.join(self.a, "empty2.png"), b"")

    def tearDown(self):
        self._tmp.cleanup()

    def scan(self, **kw):
        return core.find_duplicates([self.a, self.b], **kw)

    def names(self, group):
        return sorted(os.path.basename(f.path) for f in group.files)

    def test_finds_identical_files_with_different_names(self):
        result = self.scan()
        found = sorted(self.names(g) for g in result.groups)
        self.assertEqual(found, [
            ["IMG_0001.png", "copy of holiday.png", "holiday.png"],
            ["big-renamed.jpg", "big.jpg"],
        ])

    def test_all_file_types(self):
        result = self.scan(extensions=None)
        holiday = [g for g in result.groups if len(g.files) == 4]
        self.assertEqual(len(holiday), 1)
        self.assertIn("notes.txt", self.names(holiday[0]))

    def test_keeper_uses_folder_priority(self):
        result = self.scan()
        for g in result.groups:
            keeper = core.choose_keeper(g)
            self.assertTrue(keeper.path.startswith(self.a))
        # Reverse the priority: B now wins.
        result = core.find_duplicates([self.b, self.a])
        for g in result.groups:
            self.assertTrue(core.choose_keeper(g).path.startswith(self.b))

    def test_tiebreak_within_same_folder(self):
        result = core.find_duplicates([self.b])
        group = next(g for g in result.groups if len(g.files) == 2
                     and g.size == len(self.img))
        self.assertEqual(os.path.basename(core.choose_keeper(group, "Oldest file").path),
                         "IMG_0001.png")
        self.assertEqual(os.path.basename(core.choose_keeper(group, "Newest file").path),
                         "copy of holiday.png")

    def test_nested_folder_priority(self):
        sub = os.path.join(self.b, "sub")
        # B/sub is ranked first, so its copy should be kept even though B
        # (which contains it) is ranked last.
        result = core.find_duplicates([sub, self.a, self.b])
        group = next(g for g in result.groups if len(g.files) == 3)
        self.assertEqual(os.path.basename(core.choose_keeper(group).path),
                         "IMG_0001.png")

    def test_overlapping_folders_not_double_counted(self):
        result = core.find_duplicates([self.b, os.path.join(self.b, "sub")])
        for g in result.groups:
            paths = [f.path for f in g.files]
            self.assertEqual(len(paths), len(set(paths)))

    def test_move_removal_keeps_one_copy(self):
        result = self.scan()
        plan = core.auto_plan(result.groups)
        dest = os.path.join(self.tmp, "removed")
        out = core.remove_duplicates(plan, core.METHOD_MOVE, dest, [self.a, self.b])
        self.assertEqual(len(out.removed), 3)
        self.assertEqual(out.skipped, [])
        self.assertTrue(os.path.exists(os.path.join(self.a, "holiday.png")))
        self.assertTrue(os.path.exists(os.path.join(self.a, "big.jpg")))
        self.assertFalse(os.path.exists(os.path.join(self.b, "big-renamed.jpg")))
        self.assertTrue(os.path.exists(os.path.join(dest, "B", "sub", "IMG_0001.png")))
        self.assertTrue(os.path.exists(os.path.join(self.b, "almost.jpg")))
        self.assertEqual(core.find_duplicates([self.a, self.b]).groups, [])

    def test_delete_removal(self):
        plan = core.auto_plan(self.scan().groups)
        out = core.remove_duplicates(plan, core.METHOD_DELETE)
        self.assertEqual(len(out.removed), 3)
        for p in out.removed:
            self.assertFalse(os.path.exists(p))

    def test_changed_file_is_not_removed(self):
        plan = core.auto_plan(self.scan().groups)
        victim = os.path.join(self.b, "big-renamed.jpg")
        write(victim, b"edited after the scan")
        out = core.remove_duplicates(plan, core.METHOD_DELETE)
        self.assertTrue(os.path.exists(victim))
        self.assertIn(victim, [p for p, _ in out.skipped])

    def test_nothing_removed_if_keeper_missing(self):
        plan = core.auto_plan(self.scan().groups)
        os.remove(os.path.join(self.a, "big.jpg"))
        out = core.remove_duplicates(plan, core.METHOD_DELETE)
        self.assertTrue(os.path.exists(os.path.join(self.b, "big-renamed.jpg")))
        self.assertEqual(len(out.removed), 2)

    def test_move_does_not_overwrite(self):
        dest = os.path.join(self.tmp, "removed")
        write(os.path.join(dest, "B", "big-renamed.jpg"), b"already here")
        plan = core.auto_plan(self.scan().groups)
        core.remove_duplicates(plan, core.METHOD_MOVE, dest, [self.a, self.b])
        self.assertTrue(os.path.exists(os.path.join(dest, "B", "big-renamed (1).jpg")))

    def test_cancel(self):
        with self.assertRaises(core.Cancelled):
            self.scan(cancel=lambda: True)

    def test_parse_extensions(self):
        self.assertEqual(core.parse_extensions("jpg, .PNG ;webp"),
                         frozenset({".jpg", ".png", ".webp"}))


if __name__ == "__main__":
    unittest.main()
