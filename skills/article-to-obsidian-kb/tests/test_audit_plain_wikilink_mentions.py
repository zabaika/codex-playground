import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "audit_plain_wikilink_mentions.py"
)
SPEC = importlib.util.spec_from_file_location("audit_plain_wikilink_mentions", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

AuditTerm = MODULE.AuditTerm
collect_findings = MODULE.collect_findings
heading = MODULE.heading
main = MODULE.main
PRACTICE_HEADING = heading("practice")
EVIDENCE_HEADING = heading("evidence")
RELATED_NOTES_HEADING = heading("related_notes")


class AuditPlainWikilinkMentionsTests(unittest.TestCase):
    def test_accepts_body_wikilink_alias(self) -> None:
        markdown = f"""---
title: Тест
source:
  - https://example.com
type: lessons
tags:
  - metrics
date: 2026
---
{PRACTICE_HEADING}
- Измеряйте [[Metric Alpha|metric alpha]] рядом с качеством.
"""
        findings = collect_findings(
            markdown,
            [AuditTerm(title="Metric Alpha", aliases=("metric alpha", "Metric Alpha"))],
        )
        self.assertEqual([], findings)

    def test_reports_plain_alias_without_body_link(self) -> None:
        markdown = f"""---
title: Тест
source:
  - https://example.com
type: lessons
tags:
  - metrics
date: 2026
---
{PRACTICE_HEADING}
- Измеряйте metric alpha рядом с качеством.
"""
        findings = collect_findings(
            markdown,
            [AuditTerm(title="Metric Alpha", aliases=("metric alpha", "Metric Alpha"))],
        )
        self.assertEqual(["plain-wikilink-missing"], [finding.code for finding in findings])

    def test_reports_backticked_audit_term(self) -> None:
        markdown = f"""---
title: Тест
source:
  - https://example.com
type: lessons
tags:
  - metrics
date: 2026
---
{PRACTICE_HEADING}
- Не оставляйте `Metric Alpha` как кодовый идентификатор.
"""
        findings = collect_findings(
            markdown,
            [AuditTerm(title="Metric Alpha", aliases=("metric alpha", "Metric Alpha"))],
        )
        self.assertEqual(["backticked-concept"], [finding.code for finding in findings])

    def test_evidence_link_does_not_satisfy_body_requirement(self) -> None:
        markdown = f"""---
title: Тест
source:
  - https://example.com
type: lessons
tags:
  - metrics
date: 2026
---
{PRACTICE_HEADING}
- Измеряйте metric alpha рядом с качеством.
{EVIDENCE_HEADING}
- 2026-01-01: подтверждено в [[Metric Alpha]].
"""
        findings = collect_findings(
            markdown,
            [AuditTerm(title="Metric Alpha", aliases=("metric alpha", "Metric Alpha"))],
        )
        self.assertEqual(["plain-wikilink-missing"], [finding.code for finding in findings])

    def test_evidence_plain_mention_is_ignored_when_stable_body_is_clean(self) -> None:
        markdown = f"""---
title: Тест
source:
  - https://example.com
type: lessons
tags:
  - metrics
date: 2026
---
{PRACTICE_HEADING}
- Измеряйте поток рядом с качеством.
{EVIDENCE_HEADING}
- 2026-01-01: metric alpha подтверждена источником.
"""
        findings = collect_findings(
            markdown,
            [AuditTerm(title="Metric Alpha", aliases=("metric alpha", "Metric Alpha"))],
        )
        self.assertEqual([], findings)

    def test_related_notes_link_does_not_satisfy_body_requirement(self) -> None:
        markdown = f"""---
title: Тест
source:
  - https://example.com
type: lessons
tags:
  - metrics
date: 2026
---
{PRACTICE_HEADING}
- Измеряйте metric alpha рядом с качеством.
{RELATED_NOTES_HEADING}
[[Metric Alpha]]
"""
        findings = collect_findings(
            markdown,
            [AuditTerm(title="Metric Alpha", aliases=("metric alpha", "Metric Alpha"))],
        )
        self.assertEqual(["plain-wikilink-missing"], [finding.code for finding in findings])

    def test_accepts_cyrillic_alias(self) -> None:
        markdown = f"""---
title: Тест
source:
  - https://example.com
type: lessons
tags:
  - metrics
date: 2026
---
{PRACTICE_HEADING}
- Добавьте проверку к [[Метрики Alpha|метрикам Alpha]] перед релизом.
"""
        findings = collect_findings(
            markdown,
            [AuditTerm(title="Метрики Alpha", aliases=("метрикам Alpha", "Метрики Alpha"))],
        )
        self.assertEqual([], findings)

    def test_cli_accepts_terms_file(self) -> None:
        markdown = f"""---
title: Тест
source:
  - https://example.com
type: lessons
tags:
  - metrics
date: 2026
---
{PRACTICE_HEADING}
- Измеряйте [[Metric Alpha|metric alpha]] рядом с качеством.
"""
        payload = {
            "version": 1,
            "terms": [{"title": "Metric Alpha", "aliases": ["metric alpha"]}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            note_path = Path(tmpdir) / "note.md"
            terms_path = Path(tmpdir) / "terms.json"
            note_path.write_text(markdown, encoding="utf-8")
            terms_path.write_text(json.dumps(payload), encoding="utf-8")
            exit_code = main(["--note", str(note_path), "--terms-file", str(terms_path)])
        self.assertEqual(0, exit_code)


if __name__ == "__main__":
    unittest.main()
