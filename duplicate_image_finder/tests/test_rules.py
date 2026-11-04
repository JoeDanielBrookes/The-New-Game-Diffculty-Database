import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402
from core import FolderRule, RULE_KEEP_FIRST, RULE_NEVER_REMOVE, RULE_REMOVE_FIRST  # noqa: E402


def write(path, data, mtime):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    os.utime(path, (mtime, mtime))


class FolderRuleTests(unittest.TestCase):
    """Layout (scan order: Photos, Backup):

    Photos/a.jpg            oldest
    Photos/Favourites/b.jpg
    Backup/c.jpg
    Backup/Old/d.jpg        newest
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = self._tmp.name
        self.photos = os.path.join(t, "Photos")
        self.fav = os.path.join(self.photos, "Favourites")
        self.backup = os.path.join(t, "Backup")
        self.old = os.path.join(self.backup, "Old")
        now = time.time()
        data = b"same picture" * 50
        write(os.path.join(self.photos, "a.jpg"), data, now - 400)
        write(os.path.join(self.fav, "b.jpg"), data, now - 300)
        write(os.path.join(self.backup, "c.jpg"), data, now - 200)
        write(os.path.join(self.old, "d.jpg"), data, now - 100)
        self.group = core.find_duplicates([self.photos, self.backup]).groups[0]

    def tearDown(self):
        self._tmp.cleanup()

    def keeper(self, rules, **kw):
        return os.path.basename(core.choose_keeper(self.group, rules=rules, **kw).path)

    def removed(self, rules, **kw):
        return sorted(os.path.basename(f.path)
                      for f in core.files_to_remove(self.group, rules=rules, **kw))

    def test_without_rules_scan_order_and_tiebreak_decide(self):
        self.assertEqual(self.keeper([]), "a.jpg")

    def test_keep_first_beats_scan_order(self):
        self.assertEqual(self.keeper([FolderRule(self.old, RULE_KEEP_FIRST)]), "d.jpg")

    def test_keep_first_on_a_subfolder(self):
        self.assertEqual(self.keeper([FolderRule(self.fav, RULE_KEEP_FIRST)]), "b.jpg")

    def test_remove_first(self):
        rules = [FolderRule(self.photos, RULE_REMOVE_FIRST)]
        self.assertEqual(self.keeper(rules), "c.jpg")
        self.assertEqual(self.removed(rules), ["a.jpg", "b.jpg", "d.jpg"])

    def test_deepest_rule_wins(self):
        rules = [FolderRule(self.photos, RULE_REMOVE_FIRST),
                 FolderRule(self.fav, RULE_KEEP_FIRST)]
        self.assertEqual(core.rule_for(os.path.join(self.fav, "b.jpg"), rules),
                         RULE_KEEP_FIRST)
        self.assertEqual(core.rule_for(os.path.join(self.photos, "a.jpg"), rules),
                         RULE_REMOVE_FIRST)
        self.assertEqual(self.keeper(rules), "b.jpg")

    def test_never_remove_keeps_every_copy_there(self):
        rules = [FolderRule(self.backup, RULE_NEVER_REMOVE)]
        # Backup is now preferred, and both its copies are protected.
        self.assertEqual(self.keeper(rules), "c.jpg")
        self.assertEqual(self.removed(rules), ["a.jpg", "b.jpg"])

    def test_keep_first_and_never_remove_both_survive(self):
        rules = [FolderRule(self.fav, RULE_NEVER_REMOVE),
                 FolderRule(self.old, RULE_KEEP_FIRST)]
        self.assertEqual(self.keeper(rules), "d.jpg")
        self.assertEqual(self.removed(rules), ["a.jpg", "c.jpg"])

    def test_prefix_named_folder_is_not_inside(self):
        # "Photos2" must not match a rule for "Photos".
        self.assertFalse(core.is_inside(self.photos + "2/x.jpg", self.photos))
        self.assertTrue(core.is_inside(os.path.join(self.fav, "b.jpg"), self.photos))

    def test_auto_plan_and_removal_respect_protection(self):
        rules = [FolderRule(self.old, RULE_NEVER_REMOVE)]
        plan = core.auto_plan([self.group], rules=rules)
        removed = sorted(os.path.basename(f.path) for f in plan[0].remove)
        self.assertEqual(removed, ["a.jpg", "b.jpg", "c.jpg"])
        # Even a hand-made plan can't delete a protected file.
        keeper = next(f for f in self.group.files if f.path.endswith("a.jpg"))
        bad = [core.RemovalItem(keeper, [f for f in self.group.files if f is not keeper])]
        out = core.remove_duplicates(bad, core.METHOD_DELETE, rules=rules)
        self.assertTrue(os.path.exists(os.path.join(self.old, "d.jpg")))
        self.assertEqual(len(out.removed), 2)

    def test_unknown_rule_rejected(self):
        with self.assertRaises(ValueError):
            FolderRule(self.photos, "sometimes")


if __name__ == "__main__":
    unittest.main()
