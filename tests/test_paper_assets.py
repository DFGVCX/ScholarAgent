from __future__ import annotations

import unittest


class PaperAssetInventoryTest(unittest.TestCase):
    def test_legacy_manifest_is_flattened_and_deduplicated(self) -> None:
        from app.papers.assets import inventory_from_manifest

        manifest = {
            "visual_blocks": [
                {
                    "block_type": "figure",
                    "page_number": 2,
                    "metadata": {
                        "asset_name": "page_002_figure_1.png",
                        "label": "Figure 1",
                        "quality_status": "usable",
                    },
                }
            ],
            "equations": [
                {
                    "asset_name": "page_003_equation_2.png",
                    "page_number": 3,
                    "label": "2",
                },
                {"asset_name": "page_002_figure_1.png", "page_number": 2},
            ],
        }

        inventory = inventory_from_manifest(manifest)

        self.assertEqual(
            [item["name"] for item in inventory],
            ["page_002_figure_1.png", "page_003_equation_2.png"],
        )
        self.assertEqual(inventory[0]["type"], "figure")
        self.assertEqual(inventory[1]["type"], "equation")

    def test_orphan_candidates_are_limited_to_safe_generated_png_names(self) -> None:
        from app.papers.assets import unreferenced_generated_assets

        manifests = [
            {
                "asset_inventory": [
                    {"name": "page_001_figure_1.png", "type": "figure"}
                ]
            }
        ]
        existing = {
            "page_001_figure_1.png",
            "page_001_figure_old.png",
            "notes.txt",
            "../outside.png",
            "nested/image.png",
        }

        self.assertEqual(
            unreferenced_generated_assets(existing, manifests),
            ["page_001_figure_old.png"],
        )


if __name__ == "__main__":
    unittest.main()
