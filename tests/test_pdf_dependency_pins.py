from pathlib import Path
import unittest


class PdfDependencyPinsTest(unittest.TestCase):
    def test_pdf_text_stack_is_pinned_to_verified_versions(self) -> None:
        requirements = Path("requirements.txt").read_text(encoding="utf-8")

        self.assertIn("pypdf==6.11.0", requirements)
        self.assertIn("PyMuPDF==1.27.2.3", requirements)


if __name__ == "__main__":
    unittest.main()
