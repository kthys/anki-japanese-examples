import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest


def _load_check_manifest():
    """Load scripts/check_manifest.py without requiring it to be a package."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "check_manifest.py")
    spec = importlib.util.spec_from_file_location("check_manifest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestManifestCheck(unittest.TestCase):
    """Tests for the manifest.json vs repo-tree drift check (CI-01)."""

    @classmethod
    def setUpClass(cls):
        cls.check_manifest = _load_check_manifest()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write(self, relpath, content=""):
        path = os.path.join(self.tmp, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _write_manifest(self, files):
        # manifest.json itself ships in the release, so fixtures must list it
        # (mirroring the real repo's manifest)
        self._write("manifest.json", json.dumps({"files": ["manifest.json"] + files}))

    def test_in_sync_manifest_passes(self):
        """Every shipped file listed, every listed file present -> no errors."""
        self._write_manifest(["__init__.py", "src/a.py", "locale/en.json"])
        self._write("__init__.py")
        self._write("src/a.py")
        self._write("locale/en.json")
        self.assertEqual(self.check_manifest.validate(self.tmp), [])

    def test_stale_entry_detected(self):
        """A file listed in files[] but absent from the tree must be flagged."""
        self._write_manifest(["__init__.py", "gone.py"])
        self._write("__init__.py")
        errors = self.check_manifest.validate(self.tmp)
        self.assertEqual(len(errors), 1)
        self.assertIn("gone.py", errors[0])

    def test_forgotten_entry_detected(self):
        """A file that would ship but is missing from files[] must be flagged."""
        self._write_manifest(["__init__.py"])
        self._write("__init__.py")
        self._write("src/a.py")
        errors = self.check_manifest.validate(self.tmp)
        self.assertEqual(len(errors), 1)
        self.assertIn("src/a.py", errors[0])

    def test_excluded_dirs_not_required(self):
        """Files under release-excluded directories must not be flagged."""
        self._write_manifest(["__init__.py"])
        self._write("__init__.py")
        for d in ("tests", ".github", "user_files", ".planning", ".pi",
                  "piolium", ".claude", "scripts", "screenshots", "__pycache__"):
            self._write(os.path.join(d, "x.py"))
        self.assertEqual(self.check_manifest.validate(self.tmp), [])

    def test_excluded_root_files_not_required(self):
        """Root files excluded from the release zip must not be flagged."""
        self._write_manifest(["__init__.py"])
        self._write("__init__.py")
        for name in ("README.md", "requirements-dev.txt", ".gitignore",
                     ".gitattributes", ".DS_Store"):
            self._write(name)
        self._write("stale.pyc")
        self.assertEqual(self.check_manifest.validate(self.tmp), [])

    def test_manifest_listed_but_excluded_is_stale(self):
        """A files[] entry pointing at an excluded path must be flagged (it
        would not ship, so the entry is stale)."""
        self._write_manifest(["__init__.py", "tests/test_x.py"])
        self._write("__init__.py")
        self._write("tests/test_x.py")
        errors = self.check_manifest.validate(self.tmp)
        self.assertEqual(len(errors), 1)
        self.assertIn("tests/test_x.py", errors[0])

    def test_real_repo_manifest_is_in_sync(self):
        """The actual repo must pass the check (guards against committing a
        manifest that drifted from the tree)."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(self.check_manifest.validate(root), [])


if __name__ == '__main__':
    unittest.main()
