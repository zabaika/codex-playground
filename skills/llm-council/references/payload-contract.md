# Payload Contract

This file is the single source of truth for the canonical `llm-council` payload JSON.

## Scope

- Treat `council-payload-YYYYMMDD-HHMMSS.json` as the canonical operational artifact of a council run.
- Treat the payload as the handoff contract between council orchestration and downstream artifact rendering.
- Do not treat rendered markdown files as the source of truth when the payload exists.

## Required shape

The payload must satisfy this structure:

```json
{
  "type": "council-verdict",
  "title": "Главная тема - Решение совета",
  "timestamp": "2026-05-02 23:45:00",
  "year": "2026",
  "question": "Original user question",
  "framed_question": "Neutral brief used for all agents",
  "payload_source": "scratch/llm-council/council-payload-YYYYMMDD-HHMMSS.json",
  "run_status": {
    "status": "full",
    "details": "Optional explanation when the run was degraded"
  },
  "related_notes": [
    "Relevant Note"
  ],
  "verdict": {
    "agrees": "Text for agreement section",
    "clashes": "Text for disagreement section",
    "blind_spots": "Text for blind spots section",
    "recommendation": "Text for recommendation section",
    "first_step": "Text for first step section"
  },
  "advisors": [
    {
      "name": "Contrarian",
      "headline": "Main takeaway in one sentence",
      "stance": "No-go",
      "response": "Full advisor response"
    }
  ],
  "peer_reviews": [
    {
      "reviewer": "Reviewer 1",
      "response": "Full peer review text"
    }
  ],
  "anonymization_mapping": [
    {
      "label": "Response A",
      "advisor": "Contrarian"
    }
  ]
}
```

## Field rules

- `type`, `title`, `timestamp`, `question`, `framed_question`, `payload_source`, `verdict`, `advisors`, `peer_reviews`, and `anonymization_mapping` are required.
- `type` must be `council-verdict`.
- `year`, `run_status`, and `related_notes` are optional.
- `headline` and `stance` are mandatory for every advisor entry.
- `anonymization_mapping` is mandatory for council payloads and must be a full bijection across the advisor set.
- If `run_status` is absent, treat the run as `full`, but only if the payload contains exactly 5 completed advisor responses and a non-empty `peer_reviews` list.
- `run_status.status = "full"` is valid only when the payload contains exactly 5 completed advisor responses and a non-empty `peer_reviews` list.
- If the payload contains fewer than 5 advisor responses or no peer-review responses, mark the run as `degraded`.
- If the run degraded, include `run_status.status = "degraded"` and explain why in non-empty `run_status.details`.
- Always include `payload_source` and point it at the saved canonical `council-payload-...json`.

## Text-format rules

All human-readable text fields in the payload must follow these rules:

- Use the same dominant language as the original user question and the framed brief.
- For Russian questions, write the prose in Russian.
- Keep English only for standard technical, product, or market terms when recognition benefits from it.
- Prefer plain text.
- Ordinary paragraphs are allowed.
- Numbered and bulleted lists are allowed when they improve clarity.
- Do not use markdown headings inside payload text fields.
- Do not use markdown links, Obsidian wikilinks, HTML, or fenced code blocks.
- Do not use decorative emphasis such as `**bold**`, `__underline__`, or inline backticks around ordinary words, labels, or advisor names.
- If you need to mention anonymized labels inside text, write plain `Response A`, `Response B`, and so on, without extra formatting.

## Sanitization boundary

- The prompt layer is the primary defense against unwanted formatting noise.
- The sanitizer may remove residual decorative formatting, but it must not reinterpret, paraphrase, translate, summarize, or sharpen the meaning.
- Payload cleanup is a formatting-normalization step, not an editorial step.
- Payload cleanup is a best-effort step and may be disabled through `payload_cleanup.enabled` in local config for raw debug flows.
- If payload cleanup is enabled, apply it before saving the canonical `council-payload-...json`, not only during later rendering.
