"""Local, replaceable OCR implementation for InfoExt CAPTCHA images."""

from __future__ import annotations

import io
import json
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from config import OCRSettings, RUNTIME_ROOT, Settings

# Normalize only visual Cyrillic lookalikes, not arbitrary transliteration.
CYRILLIC_LOOKALIKES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p",
    "с": "c", "х": "x", "у": "y",
})


class CaptchaSolverError(RuntimeError):
    """Raised when a local OCR backend cannot return a usable response."""


@dataclass(frozen=True)
class CaptchaSolution:
    text: str
    confidence: float | None


@dataclass(frozen=True)
class CaptchaVariant:
    source: str
    text: str
    confidence: float | None


@dataclass(frozen=True)
class CaptchaAssessment:
    solution: CaptchaSolution
    variants: tuple[CaptchaVariant, ...]
    valid_length_variants: int
    consensus_votes: int
    submission_allowed: bool
    raw_vision_observations: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        total = len(self.variants)
        return {
            "raw_vision_observations": list(self.raw_vision_observations),
            "selected_text": self.solution.text,
            "selected_confidence": self.solution.confidence,
            "variant_count": total,
            "valid_length_variants": self.valid_length_variants,
            "consensus_votes": self.consensus_votes,
            "consensus_ratio": self.consensus_votes / total if total else 0,
            "submission_allowed": self.submission_allowed,
            "variants": [
                {"source": item.source, "text": item.text, "confidence": item.confidence}
                for item in self.variants
            ],
        }


class CaptchaSolver:
    """OCR backend contract used by the InfoExt browser client."""

    def solve(self, image_bytes: bytes) -> CaptchaSolution:
        assessment = self.assess(image_bytes)
        if assessment.submission_allowed:
            return assessment.solution
        return CaptchaSolution("", assessment.solution.confidence)

    def assess(self, image_bytes: bytes) -> CaptchaAssessment:
        raise NotImplementedError


class AppleVisionCaptchaSolver(CaptchaSolver):
    """macOS Vision implementation invoked through the installed local helper."""

    def __init__(self, settings: OCRSettings, project_root: Path) -> None:
        self.settings = settings
        self.executable = project_root / "bin" / "infoext-vision-ocr"

    def assess(self, image_bytes: bytes) -> CaptchaAssessment:
        prepared = prepare_image(image_bytes, self.settings)
        encoded = io.BytesIO()
        prepared.save(encoded, format="PNG")
        self.raw_observations = []
        candidates = self.ranked_candidates_from_prepared(encoded.getvalue(), limit=1)
        solution = candidates[0] if candidates else CaptchaSolution("", None)
        variants = [CaptchaVariant("apple_vision-primary", solution.text, solution.confidence)]
        direct_submission_allowed = (
            self.settings.vision_use_confidence
            and self._has_expected_length(solution)
            and solution.confidence is not None
            and solution.confidence >= self.settings.vision_minimum_confidence
        )
        if not direct_submission_allowed:
            variants.extend(self._fallback_variants(image_bytes))
        consensus_votes = sum(
            self._has_expected_length(CaptchaSolution(item.text, item.confidence))
            and item.text == solution.text
            for item in variants
        )
        return CaptchaAssessment(
            solution=solution,
            variants=tuple(variants),
            valid_length_variants=sum(
                self._has_expected_length(CaptchaSolution(item.text, item.confidence))
                for item in variants
            ),
            consensus_votes=consensus_votes,
            submission_allowed=direct_submission_allowed
            or self._fallback_submission_allowed(solution, variants, consensus_votes),
            raw_vision_observations=tuple(self.raw_observations),
        )

    def _fallback_variants(self, image_bytes: bytes) -> list[CaptchaVariant]:
        variants: list[CaptchaVariant] = []
        for scale in self.settings.vision_fallback_scales:
            for median_filter_size in self.settings.vision_fallback_median_filter_sizes:
                if (scale, median_filter_size) == (
                    self.settings.scale,
                    self.settings.median_filter_size,
                ):
                    continue
                variant_settings = replace(
                    self.settings, scale=scale, median_filter_size=median_filter_size
                )
                prepared = prepare_image(image_bytes, variant_settings)
                encoded = io.BytesIO()
                prepared.save(encoded, format="PNG")
                candidates = self.ranked_candidates_from_prepared(encoded.getvalue(), limit=1)
                candidate = candidates[0] if candidates else CaptchaSolution("", None)
                variants.append(
                    CaptchaVariant(
                        f"apple_vision-scale-{scale}-median-{median_filter_size}",
                        candidate.text,
                        candidate.confidence,
                    )
                )
        return variants

    def _fallback_submission_allowed(
        self,
        solution: CaptchaSolution,
        variants: list[CaptchaVariant],
        consensus_votes: int,
    ) -> bool:
        if not self.settings.vision_use_confidence:
            return (
                bool(re.fullmatch(r"[a-z0-9]{5}", solution.text))
                and len(variants) >= self.settings.vision_fallback_minimum_consensus_votes
                and consensus_votes == len(variants)
            )
        if (
            not self._has_expected_length(solution)
            or solution.confidence is None
            or solution.confidence < self.settings.vision_fallback_minimum_confidence
            or consensus_votes < self.settings.vision_fallback_minimum_consensus_votes
        ):
            return False
        return not any(
            self._has_expected_length(CaptchaSolution(item.text, item.confidence))
            and item.text != solution.text
            and item.confidence is not None
            and item.confidence > solution.confidence
            for item in variants
        )

    def ranked_candidates_from_prepared(self, image_bytes: bytes, limit: int) -> list[CaptchaSolution]:
        payload = self._run(image_bytes, max(limit, self.settings.vision_diagnostic_candidate_limit))
        if hasattr(self, "raw_observations"):
            self.raw_observations.append(payload)
        values = payload.get("candidates")
        if not isinstance(values, list):
            raise CaptchaSolverError("Apple Vision OCR returned an invalid response.")
        candidates: list[CaptchaSolution] = []
        for value in values[:limit]:
            if not isinstance(value, dict):
                continue
            candidate = CaptchaSolution(
                self._clean(str(value.get("text", ""))), self._confidence(value.get("confidence"))
            )
            if self._has_expected_length(candidate) and candidate.text not in {item.text for item in candidates}:
                candidates.append(candidate)
        return candidates[:limit]

    def _run(self, image_bytes: bytes, candidate_limit: int) -> dict[str, object]:
        if not self.executable.is_file():
            raise CaptchaSolverError("Apple Vision OCR helper is missing; run install.sh.")
        try:
            completed = subprocess.run(
                [
                    str(self.executable),
                    "--recognition-level",
                    self.settings.vision_recognition_level,
                    "--language",
                    self.settings.vision_language,
                    "--minimum-text-height",
                    str(self.settings.vision_minimum_text_height),
                    "--language-correction",
                    "enable" if self.settings.vision_language_correction else "disable",
                    "--candidate-limit",
                    str(candidate_limit),
                ],
                input=image_bytes,
                capture_output=True,
                timeout=self.settings.vision_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CaptchaSolverError("Apple Vision OCR did not complete.") from exc
        if completed.returncode != 0:
            raise CaptchaSolverError("Apple Vision OCR did not complete.")
        try:
            response = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CaptchaSolverError("Apple Vision OCR returned an invalid response.") from exc
        if not isinstance(response, dict):
            raise CaptchaSolverError("Apple Vision OCR returned an invalid response.")
        return response

    @staticmethod
    def _confidence(value: object) -> float | None:
        if isinstance(value, (int, float)) and 0 <= float(value) <= 1:
            return float(value)
        return None

    def _clean(self, value: str) -> str:
        normalized = value.lower().translate(CYRILLIC_LOOKALIKES)
        return re.sub(rf"[^{self.settings.character_whitelist}]", "", normalized)

    def _has_expected_length(self, candidate: CaptchaSolution) -> bool:
        return self.settings.expected_length_min <= len(candidate.text) <= self.settings.expected_length_max


def prepare_image(image_bytes: bytes, settings: OCRSettings) -> Image.Image:
    source = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    background = Image.new("RGBA", source.size, "white")
    image = Image.alpha_composite(background, source).convert("L")
    image = ImageOps.autocontrast(image)
    image = image.resize(
        (image.width * settings.scale, image.height * settings.scale), Image.Resampling.LANCZOS
    )
    image = ImageEnhance.Contrast(image).enhance(settings.contrast)
    if settings.median_filter_size == 1:
        return image
    return image.filter(ImageFilter.MedianFilter(size=settings.median_filter_size))


def create_captcha_solver(settings: Settings) -> CaptchaSolver:
    return AppleVisionCaptchaSolver(settings.ocr, RUNTIME_ROOT)
