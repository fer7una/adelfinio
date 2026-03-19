from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from generate_story_catalog import sanitize_story_catalog_payload
from pipeline_common import normalize_year_token, trim_text


class TrimTextTests(unittest.TestCase):
    def test_trim_text_never_exceeds_limit(self) -> None:
        text = "Primera edición: noviembre de 2009 Cualquier forma de reproducción distribución comunicación pública o transformación de esta obra solo puede ser realizada con autorización."
        trimmed = trim_text(text, 80)
        self.assertLessEqual(len(trimmed), 80)
        self.assertTrue(trimmed.endswith("..."))

    def test_normalize_year_token_pads_medieval_years(self) -> None:
        self.assertEqual(normalize_year_token("711"), "0711")
        self.assertEqual(normalize_year_token(722), "0722")
        self.assertEqual(normalize_year_token("2009"), "2009")


class StoryCatalogSanitizationTests(unittest.TestCase):
    def test_sanitize_story_catalog_payload_repairs_event_ids_from_real_chunks(self) -> None:
        source_pack = {
            "chunks": [
                {
                    "chunk_id": "chk-0194",
                    "file": "docs/chronicles/test.pdf",
                    "page_start": 67,
                    "page_end": 67,
                    "checksum": "a" * 64,
                    "normalized_text": "Despues del heroico Pelayo y el anodino Favila, vino a empunar el cetro asturiano alguien a quien ya se puede definir como rey.",
                },
                {
                    "chunk_id": "chk-0296",
                    "file": "docs/chronicles/test.pdf",
                    "page_start": 92,
                    "page_end": 92,
                    "checksum": "b" * 64,
                    "normalized_text": "Mas sobre la reforma religiosa: el 4 de abril de 0759 el rey Fruela funda un convento de monjas en San Miguel de Pedroso.",
                },
            ],
            "derived_events": [
                {
                    "event_id": "evt-075201",
                    "source_ref": {"section": "chk-0194"},
                },
                {
                    "event_id": "evt-075901",
                    "source_ref": {"section": "chk-0296"},
                },
            ],
        }
        payload = {
            "stories": [
                {
                    "story_id": "story-test-favila",
                    "source_event_ids": ["evt-0214", "evt-099701"],
                    "source_refs": [
                        {
                            "file": "bogus",
                            "section": "Después del heroico Pelayo y el anodino Favila, vino a empuñar el cetro asturiano Alfonso I",
                            "checksum": "0" * 64,
                            "excerpt": "Después del heroico Pelayo y el anodino Favila...",
                            "page_start": 66,
                            "page_end": 66,
                            "chunk_id": "chk-0895",
                        },
                        {
                            "file": "bogus",
                            "section": "Más sobre la reforma religiosa: en abril de 759 el rey Fruela funda un convento",
                            "checksum": "1" * 64,
                            "excerpt": "en abril de 759 el rey Fruela funda un convento",
                            "page_start": 131,
                            "page_end": 131,
                            "chunk_id": "chk-7590",
                        },
                    ],
                }
            ]
        }

        sanitize_story_catalog_payload(source_pack, payload)
        story = payload["stories"][0]
        self.assertEqual(story["source_event_ids"], ["evt-075201", "evt-075901"])
        self.assertEqual(story["source_refs"][0]["chunk_id"], "chk-0194")
        self.assertEqual(story["source_refs"][1]["chunk_id"], "chk-0296")


if __name__ == "__main__":
    unittest.main()
