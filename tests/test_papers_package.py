from __future__ import annotations

import subprocess
import sys
import unittest


class PapersPackageTests(unittest.TestCase):
    def test_parsing_import_does_not_initialize_database_stack(self) -> None:
        script = (
            "import sys; "
            "import app.papers.parsing; "
            "blocked = sorted(name for name in sys.modules "
            "if name in {'app.db.session', 'app.papers.ingestion'}); "
            "print(','.join(blocked))"
        )

        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(completed.stdout.strip(), "")

    def test_lazy_package_exports_preserve_public_imports(self) -> None:
        from app.papers import PaperInput, PaperRecord, PaperRepository

        self.assertEqual(PaperInput.__name__, "PaperInput")
        self.assertEqual(PaperRecord.__name__, "PaperRecord")
        self.assertEqual(PaperRepository.__name__, "PaperRepository")


if __name__ == "__main__":
    unittest.main()
