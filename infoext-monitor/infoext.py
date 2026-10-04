"""Browser interaction and result parsing for the official InfoExt form."""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from captcha_solver import CaptchaAssessment, CaptchaSolution, CaptchaSolver, CaptchaSolverError
from config import Settings


INFOEXT_URL = "https://infoext2.delegaciondelgobierno.gob.es/infoext2/consulta.html"


class InfoExtError(RuntimeError):
    """Base class for a failed InfoExt check."""


class CaptchaMaxAttempts(InfoExtError):
    """The shared CAPTCHA budget was exhausted by OCR withholding or rejection."""


class FormValidationError(InfoExtError):
    """The portal rejected identity fields; retrying CAPTCHA cannot fix them."""


class ResultParsingError(InfoExtError):
    """InfoExt accepted the form but its result did not expose Estado."""


class ExpedienteNotFound(InfoExtError):
    """InfoExt reported that the supplied identity data found no expediente."""


class RequestRejected(InfoExtError):
    """InfoExt rejected the requested URL before a status result was available."""


@dataclass(frozen=True)
class InfoExtResult:
    status: str
    expediente: str | None
    tipo_autorizacion: str | None
    fecha_presentacion: str | None
    fecha_resolucion: str | None
    captcha_attempts: int


def normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def decode_captcha_data_url(data_url: str) -> bytes:
    if not data_url.startswith("data:image/") or "," not in data_url:
        raise InfoExtError("InfoExt did not expose CAPTCHA as an image data URL.")
    try:
        return base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except ValueError as exc:
        raise InfoExtError("InfoExt returned an invalid CAPTCHA image.") from exc


def normalize_status(status: str | None) -> str:
    return re.sub(r"\s+", " ", (status or "")).strip().casefold()


def extract_result_pairs(page: object) -> dict[str, str]:
    """Extract label/value pairs from common table, definition-list and form layouts."""
    return page.locator("body").evaluate(
        """body => {
          const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
          const pairs = {};
          for (const row of body.querySelectorAll('tr')) {
            const cells = [...row.querySelectorAll('th, td')].map(node => clean(node.innerText));
            if (cells.length >= 2 && cells[0] && cells[1]) pairs[cells[0]] = cells.slice(1).join(' ');
          }
          for (const dt of body.querySelectorAll('dt')) {
            const value = dt.nextElementSibling;
            if (value && value.tagName === 'DD') pairs[clean(dt.innerText)] = clean(value.innerText);
          }
          for (const label of body.querySelectorAll('label, .label, .etiqueta')) {
            const target = label.htmlFor ? document.getElementById(label.htmlFor) : null;
            const value = target || label.nextElementSibling;
            if (value) pairs[clean(label.innerText)] = clean(value.innerText || value.value);
          }
          return pairs;
        }"""
    )


def find_pair(pairs: dict[str, str], *label_fragments: str) -> str | None:
    for label, value in pairs.items():
        normalized = normalize_label(label)
        if any(fragment in normalized for fragment in label_fragments):
            return value or None
    return None


def parse_result(page: object, captcha_attempts: int) -> InfoExtResult:
    pairs = extract_result_pairs(page)
    status = find_pair(pairs, "estado")
    if not status:
        body_text = page.locator("body").inner_text()
        match = re.search(r"(?:^|\n)\s*Estado\s*[:\-]?\s*([^\n]+)", body_text, flags=re.IGNORECASE)
        status = match.group(1).strip() if match else None
    if not status:
        raise ResultParsingError("InfoExt result did not contain an Estado field.")
    return InfoExtResult(
        status=status,
        expediente=find_pair(pairs, "número de expediente", "numero de expediente", "expediente"),
        tipo_autorizacion=find_pair(pairs, "tipo de autorización", "tipo de autorizacion", "procedimiento"),
        fecha_presentacion=find_pair(pairs, "fecha de presentación", "fecha de presentacion"),
        fecha_resolucion=find_pair(pairs, "fecha de resolución", "fecha de resolucion"),
        captcha_attempts=captcha_attempts,
    )


class InfoExtClient:
    def __init__(self, settings: Settings, solver: CaptchaSolver, log: Callable[[str], None]) -> None:
        self.settings = settings
        self.solver = solver
        self.log = log

    def check(
        self,
        *,
        captcha_dir: Path | None = None,
        submission_artifact_dir: Path | None = None,
    ) -> InfoExtResult:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    # Playwright requires this timeout in milliseconds; the operator value is seconds.
                    page.set_default_timeout(self.settings.timeout_seconds * 1000)
                    self._open_form(page)
                    self._fill_identity(page)
                    return self._submit_with_captcha(
                        page,
                        captcha_dir=captcha_dir,
                        submission_artifact_dir=submission_artifact_dir,
                    )
                finally:
                    browser.close()
        except PlaywrightTimeoutError as exc:
            raise InfoExtError("InfoExt timed out while loading or submitting the form.") from exc
        except PlaywrightError as exc:
            raise InfoExtError("InfoExt browser or network operation failed.") from exc
        except CaptchaSolverError as exc:
            raise InfoExtError("Local CAPTCHA OCR failed.") from exc

    def debug_captcha(self, debug_dir: Path) -> CaptchaAssessment:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                try:
                    page = browser.new_page()
                    page.set_default_timeout(self.settings.timeout_seconds * 1000)
                    self._open_form(page)
                    self._fill_identity(page)
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(debug_dir / "filled-form.png"), full_page=True)
                    image_bytes = self._current_captcha_bytes(page)
                    (debug_dir / "captcha-1.png").write_bytes(image_bytes)
                    assessment = self.solver.assess(image_bytes)
                    report = {
                        "form_submitted": False,
                        "consultar_submitted": False,
                        "captcha": assessment.to_dict(),
                    }
                    (debug_dir / "ocr-report.json").write_text(
                        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                    )
                    return assessment
                finally:
                    browser.close()
        except PlaywrightTimeoutError as exc:
            raise InfoExtError("InfoExt timed out while collecting the CAPTCHA debug sample.") from exc
        except PlaywrightError as exc:
            raise InfoExtError("InfoExt browser or network operation failed during CAPTCHA diagnosis.") from exc
        except CaptchaSolverError as exc:
            raise InfoExtError("Local CAPTCHA OCR failed.") from exc

    def _open_form(self, page: object) -> None:
        self.log("InfoExt loaded")
        page.goto(INFOEXT_URL, wait_until="domcontentloaded")
        self._raise_if_request_rejected(page)
        if page.locator("#nie").count() == 0:
            page.get_by_role("link", name="ENTRAR FORMULARIO").click()
        page.locator("#nie").wait_for(state="visible")
        self._raise_if_request_rejected(page)

    def _fill_identity(self, page: object) -> None:
        page.locator("#nie").fill(self.settings.nie)
        page.locator("#fechaPresentacion").fill(self.settings.fecha_presentacion)
        year_field = page.locator("#anio")
        if year_field.count() and year_field.is_visible():
            if not self.settings.ano_nacimiento:
                raise FormValidationError(
                    "InfoExt requires Año de nacimiento; fill infoext.ano_nacimiento in local config."
                )
            year_field.fill(self.settings.ano_nacimiento)

    @staticmethod
    def _require_identity_fields(page: object) -> None:
        fields = {"NIE": "#nie", "submission date": "#fechaPresentacion"}
        missing = [
            label
            for label, selector in fields.items()
            if page.locator(selector).count() == 0 or not page.locator(selector).input_value().strip()
        ]
        year_field = page.locator("#anio")
        if year_field.count() and year_field.is_visible() and not year_field.input_value().strip():
            raise FormValidationError("InfoExt Año de nacimiento is empty before submission.")
        if missing:
            raise InfoExtError("InfoExt identity fields are empty before CAPTCHA submission.")

    def _submit_with_captcha(
        self,
        page: object,
        *,
        captcha_dir: Path | None,
        submission_artifact_dir: Path | None = None,
    ) -> InfoExtResult:
        for attempt in range(1, self.settings.captcha_max_attempts + 1):
            self.log(f"CAPTCHA attempt {attempt}")
            solution = self._solve_current_captcha(page, captcha_dir, attempt)
            if not solution.text:
                self._prepare_next_captcha_attempt(page, attempt, refresh_captcha=True)
                continue
            self._require_identity_fields(page)
            page.locator("#captcha").fill(solution.text)
            page.locator("#btnConsulta").click()
            page.wait_for_load_state("domcontentloaded")
            time.sleep(self.settings.result_settle_delay_seconds)
            form_was_returned = self._form_was_returned(page)
            self._save_submitted_captcha_response(
                page,
                submission_artifact_dir,
                attempt=attempt,
                solution=solution,
                form_was_returned=form_was_returned,
            )
            self._raise_if_request_rejected(page)
            if form_was_returned:
                self._raise_if_expediente_not_found(page)
                self._validate_returned_form(page)
                self.log("CAPTCHA rejected")
                self._prepare_next_captcha_attempt(page, attempt, refresh_captcha=False)
                continue
            result = parse_result(page, attempt)
            self.log("CAPTCHA accepted")
            return result
        raise CaptchaMaxAttempts(
            f"CAPTCHA was not accepted after {self.settings.captcha_max_attempts} attempts."
        )

    def _solve_current_captcha(
        self, page: object, captcha_dir: Path | None, attempt: int
    ) -> CaptchaSolution:
        image_bytes = self._current_captcha_bytes(page)
        sample_path = None
        if captcha_dir:
            captcha_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f")
            sample_path = captcha_dir / f"{timestamp}_attempt-{attempt}.png"
            sample_path.write_bytes(image_bytes)
        assessment = self.solver.assess(image_bytes)
        self.last_captcha_assessment = assessment
        if sample_path and self.settings.debug_submitted_captcha_responses:
            report_path = sample_path.with_suffix(".json")
            report_path.write_text(
                json.dumps(assessment.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            report_path.chmod(0o600)
        if not assessment.submission_allowed:
            self.log(
                "CAPTCHA withheld: OCR quality requirements not met "
                f"({assessment.consensus_votes}/{len(assessment.variants)})"
            )
            return CaptchaSolution(text="", confidence=assessment.solution.confidence)
        return assessment.solution

    def _save_submitted_captcha_response(
        self,
        page: object,
        artifact_dir: Path | None,
        *,
        attempt: int,
        solution: CaptchaSolution,
        form_was_returned: bool,
    ) -> None:
        if artifact_dir is None:
            return
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f")
        prefix = artifact_dir / f"{timestamp}_attempt-{attempt}"
        metadata = {
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "attempt": attempt,
            "submitted_captcha": solution.text,
            "ocr_confidence": solution.confidence,
            "ocr_assessment": (
                self.last_captcha_assessment.to_dict()
                if hasattr(self, "last_captcha_assessment") else None
            ),
            "form_was_returned": form_was_returned,
        }
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            artifact_dir.chmod(0o700)
            artifacts = {
                prefix.with_name(f"{prefix.name}_response.html"): page.content(),
                prefix.with_name(f"{prefix.name}_response.txt"): page.locator("body").inner_text(),
                prefix.with_name(f"{prefix.name}_metadata.json"): json.dumps(
                    metadata, ensure_ascii=False, indent=2
                )
                + "\n",
            }
            for path, contents in artifacts.items():
                path.write_text(contents, encoding="utf-8")
                path.chmod(0o600)
        except OSError as exc:
            raise InfoExtError("Could not save submitted CAPTCHA debug artifacts.") from exc
        self.log(f"Saved submitted CAPTCHA response artifacts for attempt {attempt}")

    @staticmethod
    def _validate_returned_form(page: object) -> None:
        errors = page.locator("#nie, #fechaPresentacion, #anio, #captcha").evaluate_all(
            """inputs => inputs.flatMap(input =>
                [...input.parentElement.querySelectorAll('.help-block.with-errors, .error')]
                .filter(node => node.textContent.trim())
                .map(node => ({field: input.id, message: node.textContent.trim()})))"""
        )
        labels = {"nie": "NIE", "fechaPresentacion": "submission date", "anio": "Año de nacimiento"}
        invalid_fields = sorted({labels[item["field"]] for item in errors if item["field"] in labels})
        if invalid_fields:
            raise FormValidationError("InfoExt rejected required identity fields: " + ", ".join(invalid_fields))
        if any(
            item["field"] == "captcha"
            and "los caracteres escritos no son correctos" in normalize_label(item["message"])
            for item in errors
        ):
            return
        raise ResultParsingError("InfoExt returned the form without a recognized CAPTCHA error or Estado.")

    @staticmethod
    def _current_captcha_bytes(page: object) -> bytes:
        image = page.locator("img[alt='captcha']")
        return decode_captcha_data_url(image.get_attribute("src") or "")

    @staticmethod
    def _form_was_returned(page: object) -> bool:
        return page.locator("#captcha").count() > 0

    @staticmethod
    def _raise_if_expediente_not_found(page: object) -> None:
        body = normalize_label(page.locator("body").inner_text())
        not_found_terms = (
            "no se ha encontrado",
            "no existe ningún expediente",
            "no existe ningun expediente",
            "expediente no encontrado",
        )
        if any(term in body for term in not_found_terms):
            raise ExpedienteNotFound("InfoExt did not find an expediente for the configured identity data.")

    @staticmethod
    def _raise_if_request_rejected(page: object) -> None:
        body = normalize_label(page.locator("body").inner_text())
        if "the requested url was rejected. please consult with your administrator." in body:
            raise RequestRejected("InfoExt rejected the requested URL; stopping without a retry.")

    @staticmethod
    def _refresh_captcha(page: object) -> None:
        # Refresh POSTs to the same URL; register before clicking so the old
        # document's visible image cannot satisfy the refresh wait.
        with page.expect_event("domcontentloaded"):
            page.get_by_role("link", name="Recargar Captcha").click()
        InfoExtClient._raise_if_request_rejected(page)
        page.locator("img[alt='captcha']").wait_for(state="visible")

    def _prepare_next_captcha_attempt(
        self, page: object, attempt: int, *, refresh_captcha: bool
    ) -> None:
        if attempt >= self.settings.captcha_max_attempts:
            return
        if refresh_captcha:
            self._refresh_captcha(page)
        self._wait_before_captcha_retry()
        # A generated CAPTCHA or returned validation form can clear fields.
        # Refill them before the next submission.
        self._fill_identity(page)

    def _wait_before_captcha_retry(self) -> None:
        time.sleep(self.settings.captcha_retry_delay_seconds)
