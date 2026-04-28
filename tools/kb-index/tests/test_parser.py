from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kb_index.parser import parse_note


class ParserTests(unittest.TestCase):
    def test_extracts_lead_summary_as_entry_chunk(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            vault_root = Path(tmp_dir)
            note_path = vault_root / "Ideas" / "Example.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text(
                "---\n"
                "tags:\n"
                "  - ai-tools\n"
                "---\n"
                "Это суть заметки.\n\n"
                "## Детали\n"
                "Подробности и [[Связанная заметка]].\n",
                encoding="utf-8",
            )
            parsed = parse_note(vault_root, note_path)
            self.assertEqual(parsed.lead_summary, "Это суть заметки.")
            self.assertEqual(parsed.chunks[0].chunk_role, "entry")
            self.assertEqual(parsed.chunks[0].heading, "Суть")
            self.assertEqual(len(parsed.chunks), 1)
            self.assertEqual(parsed.headings, ["Детали"])
            self.assertEqual(parsed.tags, ["ai-tools"])
            self.assertEqual(parsed.links_out, ["Связанная заметка"])


if __name__ == "__main__":
    unittest.main()
