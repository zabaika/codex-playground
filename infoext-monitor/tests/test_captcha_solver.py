import io
from dataclasses import replace

from PIL import Image, ImageDraw
import pytest

from captcha_solver import (
    AppleVisionCaptchaSolver,
    CaptchaAssessment,
    CaptchaSolution,
    CaptchaVariant,
    prepare_image,
)
from config import OCRSettings


@pytest.fixture
def ocr_settings() -> OCRSettings:
    """Shared synthetic settings; each test overrides only its relevant policy."""
    return OCRSettings(
        character_whitelist="abcdefghijklmnopqrstuvwxyz0123456789",
        scale=1,
        contrast=1.0,
        median_filter_size=3,
        expected_length_min=5,
        expected_length_max=5,
        vision_timeout_seconds=30,
        vision_minimum_confidence=0.5,
        vision_fallback_minimum_confidence=0.5,
        vision_fallback_scales=(1, 2),
        vision_fallback_median_filter_sizes=(1, 3),
        vision_fallback_minimum_consensus_votes=2,
        vision_minimum_text_height=0.0,
        vision_language="en-US",
        vision_recognition_level="accurate",
        vision_diagnostic_candidate_limit=5,
        vision_language_correction=False,
        vision_use_confidence=True,
    )


def test_preprocess_composites_transparent_captcha_on_white_background(ocr_settings: OCRSettings) -> None:
    source = Image.new("RGBA", (20, 10), (0, 0, 0, 0))
    ImageDraw.Draw(source).rectangle((8, 2, 11, 7), fill=(0, 0, 0, 255))
    encoded = io.BytesIO()
    source.save(encoded, format="PNG")

    processed = prepare_image(encoded.getvalue(), ocr_settings)

    assert processed.getpixel((0, 0)) == 255
    assert processed.getpixel((9, 4)) == 0


@pytest.mark.parametrize(
    ("raw", "expected", "allowed"),
    [
        ("xg53р", "xg53p", True),
        ("Yb35еm-", "", False),
        (",fyf45", "fyf45", True),
        ("аб35m", "", False),
    ],
)
def test_vision_normalizes_lookalikes_before_length_and_consensus_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path, ocr_settings: OCRSettings,
    raw: str, expected: str, allowed: bool,
) -> None:
    solver = AppleVisionCaptchaSolver(replace(ocr_settings, vision_use_confidence=False), tmp_path)
    payload = {"candidates": [{"text": raw, "confidence": 0.3}]}
    monkeypatch.setattr(solver, "_run", lambda *_: payload)
    encoded = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(encoded, format="PNG")

    assessment = solver.assess(encoded.getvalue())

    assert assessment.solution.text == expected
    assert assessment.submission_allowed is allowed
    assert all(item == payload for item in assessment.raw_vision_observations)


def test_only_visual_cyrillic_lookalikes_are_normalized(tmp_path, ocr_settings: OCRSettings) -> None:
    solver = AppleVisionCaptchaSolver(ocr_settings, tmp_path)

    assert solver._clean("АЕОРСХУ аеорсху 0123456789") == "aeopcxy aeopcxy 0123456789".replace(" ", "")
    assert solver._clean("бжзилфцчшщыэюя") == ""


def test_assessment_report_exposes_consensus_without_claiming_accuracy() -> None:
    assessment = CaptchaAssessment(
        solution=CaptchaSolution("ekbhh", 0.5),
        variants=(
            CaptchaVariant("apple_vision-primary", "ekbhi", 0.5),
            CaptchaVariant("apple_vision-scale-2-median-1", "ekbhh", 0.5),
            CaptchaVariant("apple_vision-scale-3-median-1", "ekbhh", 0.5),
        ),
        valid_length_variants=3,
        consensus_votes=2,
        submission_allowed=False,
    )

    report = assessment.to_dict()

    assert report["selected_text"] == "ekbhh"
    assert report["consensus_ratio"] == pytest.approx(2 / 3)
    assert report["submission_allowed"] is False
    assert "accuracy" not in report


def test_apple_vision_accepts_only_exact_length_candidates_above_confidence_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path, ocr_settings: OCRSettings
) -> None:
    settings = replace(
        ocr_settings, vision_minimum_confidence=1.0,
        vision_fallback_minimum_confidence=0.6,
    )
    image = Image.new("RGB", (20, 10), "white")
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    solver = AppleVisionCaptchaSolver(settings, tmp_path)
    monkeypatch.setattr(solver, "_run", lambda _, __: {"candidates": [{"text": "K3mgp", "confidence": 1.0}]})

    assessment = solver.assess(encoded.getvalue())

    assert assessment.solution.text == "k3mgp"
    assert assessment.submission_allowed is True
    assert solver.solve(encoded.getvalue()).text == "k3mgp"

    monkeypatch.setattr(solver, "_run", lambda _, __: {"candidates": [{"text": "K3mgp", "confidence": 0.5}]})

    assessment = solver.assess(encoded.getvalue())

    assert assessment.solution.text == "k3mgp"
    assert assessment.submission_allowed is False
    assert solver.solve(encoded.getvalue()).text == ""
    monkeypatch.setattr(solver, "_run", lambda _, __: {"candidates": [
        {"text": "abc", "confidence": 1.0},
        {"text": "abc12", "confidence": 0.5},
    ]})
    assessment = solver.assess(encoded.getvalue())
    assert assessment.solution.text == ""
    assert assessment.submission_allowed is False
    assert assessment.to_dict()["raw_vision_observations"][0]["candidates"][0]["text"] == "abc"
    assert assessment.to_dict()["raw_vision_observations"][0]["candidates"][1]["text"] == "abc12"


def test_apple_vision_fallback_requires_full_variant_agreement(
    monkeypatch: pytest.MonkeyPatch, tmp_path, ocr_settings: OCRSettings
) -> None:
    settings = replace(
        ocr_settings, vision_minimum_confidence=1.0,
        vision_fallback_minimum_consensus_votes=4,
    )
    image = Image.new("RGB", (20, 10), "white")
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    solver = AppleVisionCaptchaSolver(settings, tmp_path)
    matching = iter([[CaptchaSolution("pegr8", 0.5)] for _ in range(4)])
    monkeypatch.setattr(solver, "ranked_candidates_from_prepared", lambda _, limit: next(matching))

    assessment = solver.assess(encoded.getvalue())

    assert assessment.solution == CaptchaSolution("pegr8", 0.5)
    assert assessment.consensus_votes == 4
    assert assessment.submission_allowed is True

    solver.settings = replace(settings, vision_use_confidence=False)
    matching = iter([[CaptchaSolution("pegr8", 0.3)] for _ in range(4)])
    assessment = solver.assess(encoded.getvalue())
    assert assessment.submission_allowed is True

    matching = iter([[CaptchaSolution("pegr8", 1.0)]] + [[CaptchaSolution("other", 0.3)] for _ in range(3)])
    assessment = solver.assess(encoded.getvalue())
    assert assessment.submission_allowed is False


def test_apple_vision_fallback_rejects_a_higher_confidence_disagreement(
    monkeypatch: pytest.MonkeyPatch, tmp_path, ocr_settings: OCRSettings
) -> None:
    settings = replace(
        ocr_settings, vision_minimum_confidence=1.0,
        vision_fallback_minimum_consensus_votes=3,
    )
    image = Image.new("RGB", (20, 10), "white")
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    solver = AppleVisionCaptchaSolver(settings, tmp_path)
    candidates = iter(
        [
            [CaptchaSolution("pegr8", 0.5)],
            [CaptchaSolution("pegr8", 0.5)],
            [CaptchaSolution("other", 0.6)],
            [CaptchaSolution("pegr8", 0.5)],
        ]
    )
    monkeypatch.setattr(solver, "ranked_candidates_from_prepared", lambda _, limit: next(candidates))

    assessment = solver.assess(encoded.getvalue())

    assert assessment.consensus_votes == 3
    assert assessment.submission_allowed is False
