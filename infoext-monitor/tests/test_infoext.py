import base64
import json
from types import SimpleNamespace

import pytest

import infoext
from infoext import (
    CaptchaMaxAttempts,
    ExpedienteNotFound,
    InfoExtClient,
    InfoExtError,
    InfoExtResult,
    RequestRejected,
    decode_captcha_data_url,
    normalize_status,
    parse_result,
)


class FakeLocator:
    def __init__(self, pairs: dict[str, str], body_text: str) -> None:
        self.pairs = pairs
        self.body_text = body_text

    def evaluate(self, _: str) -> dict[str, str]:
        return self.pairs

    def inner_text(self) -> str:
        return self.body_text


class FakePage:
    def __init__(self, pairs: dict[str, str], body_text: str = "") -> None:
        self.locator_instance = FakeLocator(pairs, body_text)

    def locator(self, _: str) -> FakeLocator:
        return self.locator_instance


class CountLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class FormPage(FakePage):
    def __init__(self, body_text: str) -> None:
        super().__init__({}, body_text)

    def locator(self, selector: str):
        if selector == "#captcha":
            return CountLocator(1)
        return super().locator(selector)


def test_decode_captcha_data_url() -> None:
    payload = b"captcha bytes"
    data_url = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
    assert decode_captcha_data_url(data_url) == payload


def test_decode_captcha_rejects_non_image_url() -> None:
    with pytest.raises(InfoExtError):
        decode_captcha_data_url("https://example.invalid/captcha.png")


def test_parse_result_uses_labelled_fields() -> None:
    page = FakePage(
        {
            "Estado": "RESUELTO - FAVORABLE",
            "Número de expediente": "X-123",
            "Fecha de resolución": "03/10/2026",
        }
    )

    result = parse_result(page, captcha_attempts=2)

    assert result.status == "RESUELTO - FAVORABLE"
    assert result.expediente == "X-123"
    assert result.fecha_resolucion == "03/10/2026"
    assert result.captcha_attempts == 2


def test_status_normalization_ignores_case_and_whitespace() -> None:
    assert normalize_status(" EN  TRÁMITE ") == normalize_status("en trámite")


def test_returned_form_is_a_retry_signal() -> None:
    assert InfoExtClient._form_was_returned(FormPage("Formulario")) is True


def test_expediente_not_found_is_distinguished() -> None:
    with pytest.raises(ExpedienteNotFound):
        InfoExtClient._raise_if_expediente_not_found(FormPage("No se ha encontrado expediente"))


@pytest.mark.parametrize("mode", ["check", "debug_captcha"])
def test_browser_errors_are_sanitized_for_both_modes(monkeypatch, tmp_path, mode) -> None:
    import playwright.sync_api

    def fail():
        raise playwright.sync_api.Error("private identity and transport context")

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", fail)
    client = InfoExtClient(SimpleNamespace(), object(), lambda _: None)
    with pytest.raises(InfoExtError, match="browser or network operation failed") as caught:
        if mode == "check":
            client.check()
        else:
            client.debug_captcha(tmp_path)
    assert "private identity" not in str(caught.value)
    assert "transport context" not in str(caught.value)


def test_requested_url_rejection_stops_the_run() -> None:
    with pytest.raises(RequestRejected):
        InfoExtClient._raise_if_request_rejected(
            FormPage("The requested URL was rejected. Please consult with your administrator.")
        )


class RetryPage:
    def __init__(self) -> None:
        self.filled: list[str] = []
        self.submit_clicks = 0

    def locator(self, _: str) -> "RetryPage":
        return self

    def fill(self, value: str) -> None:
        self.filled.append(value)

    def count(self) -> int:
        return 1

    def is_visible(self) -> bool:
        return False

    def evaluate_all(self, _: str):
        return [{"field": "captcha", "message": "Los caracteres escritos no son correctos."}]

    def input_value(self) -> str:
        return "configured"

    def click(self) -> None:
        self.submit_clicks += 1

    def wait_for_load_state(self, _: str) -> None:
        return None


class SubmissionArtifactPage:
    def content(self) -> str:
        return "<html><body>Los caracteres escritos no son correctos.</body></html>"

    def locator(self, selector: str) -> FakeLocator:
        assert selector == "body"
        return FakeLocator({}, "Los caracteres escritos no son correctos.")


class IdentityPage:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def locator(self, selector: str) -> "IdentityPage":
        self.selector = selector
        return self

    def count(self) -> int:
        return int(self.selector in self.values)

    def input_value(self) -> str:
        return self.values[self.selector]


def test_identity_fields_must_be_present_before_captcha_submission() -> None:
    InfoExtClient._require_identity_fields(
        IdentityPage({"#nie": "Z12AB34C", "#fechaPresentacion": "01/02/2026"})
    )

    with pytest.raises(InfoExtError, match="identity fields are empty"):
        InfoExtClient._require_identity_fields(
            IdentityPage({"#nie": "Z12AB34C", "#fechaPresentacion": ""})
        )


def test_withheld_captcha_refreshes_then_waits_before_the_next_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        captcha_max_attempts=2,
        captcha_retry_delay_seconds=0.25,
        result_settle_delay_seconds=0,
    )
    client = InfoExtClient(settings, solver=object(), log=lambda _: None)
    page = RetryPage()
    solutions = iter([infoext.CaptchaSolution("", None), infoext.CaptchaSolution("abc12", 1.0)])
    sleeps: list[float] = []
    refreshes: list[object] = []
    refills: list[object] = []
    events: list[str] = []
    expected = InfoExtResult("EN TRÁMITE", None, None, None, None, 2)

    monkeypatch.setattr(client, "_solve_current_captcha", lambda *_: next(solutions))
    monkeypatch.setattr(infoext.time, "sleep", lambda value: (sleeps.append(value), events.append("sleep")))
    monkeypatch.setattr(client, "_refresh_captcha", lambda current_page: (refreshes.append(current_page), events.append("refresh")))
    monkeypatch.setattr(client, "_fill_identity", lambda current_page: (refills.append(current_page), events.append("refill")))
    monkeypatch.setattr(client, "_raise_if_request_rejected", lambda _: None)
    monkeypatch.setattr(client, "_form_was_returned", lambda _: False)
    monkeypatch.setattr(infoext, "parse_result", lambda _, captcha_attempts: expected)

    assert client._submit_with_captcha(page, captcha_dir=None) == expected
    assert sleeps == [settings.captcha_retry_delay_seconds, settings.result_settle_delay_seconds]
    assert refreshes == [page]
    assert refills == [page]
    assert events[:3] == ["refresh", "sleep", "refill"]
    assert page.filled == ["abc12"]
    assert page.submit_clicks == 1


@pytest.mark.parametrize("url_rejected", [False, True])
def test_refresh_waits_for_new_document_before_inspecting_captcha(url_rejected) -> None:
    from contextlib import contextmanager

    events = []
    loaded = False

    @contextmanager
    def expect_event(event):
        nonlocal loaded
        assert event == "domcontentloaded"
        events.append("registered")
        yield
        assert events[-1] == "clicked"
        loaded = True
        events.append("loaded")

    def get_by_role(role, *, name):
        assert role == "link" and name == "Recargar Captcha"
        assert events == ["registered"]
        return SimpleNamespace(click=lambda: events.append("clicked"))

    def locator(selector):
        assert loaded, "The old document must never satisfy the CAPTCHA wait"
        if selector == "body":
            events.append("checked_response")
            return SimpleNamespace(inner_text=lambda: (
                "The requested URL was rejected. Please consult with your administrator."
                if url_rejected else "New form"
            ))
        assert selector == "img[alt='captcha']"
        return SimpleNamespace(wait_for=lambda *, state: events.append(state))

    page = SimpleNamespace(expect_event=expect_event, get_by_role=get_by_role, locator=locator)
    if url_rejected:
        with pytest.raises(RequestRejected):
            InfoExtClient._refresh_captcha(page)
        assert events == ["registered", "clicked", "loaded", "checked_response"]
    else:
        InfoExtClient._refresh_captcha(page)
        assert events == ["registered", "clicked", "loaded", "checked_response", "visible"]


def test_final_withheld_captcha_does_not_create_an_unused_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(captcha_max_attempts=1, captcha_retry_delay_seconds=0.25)
    client = InfoExtClient(settings, solver=object(), log=lambda _: None)
    sleeps: list[float] = []
    refreshes: list[object] = []
    refills: list[object] = []

    monkeypatch.setattr(
        client, "_solve_current_captcha", lambda *_: infoext.CaptchaSolution("", None)
    )
    monkeypatch.setattr(infoext.time, "sleep", sleeps.append)
    monkeypatch.setattr(client, "_refresh_captcha", lambda page: refreshes.append(page))
    monkeypatch.setattr(client, "_fill_identity", lambda page: refills.append(page))

    with pytest.raises(CaptchaMaxAttempts):
        client._submit_with_captcha(object(), captcha_dir=None)

    assert sleeps == []
    assert refreshes == []
    assert refills == []


def test_server_rejection_reuses_the_returned_captcha_without_a_second_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        captcha_max_attempts=2,
        captcha_retry_delay_seconds=0.25,
        result_settle_delay_seconds=0,
    )
    client = InfoExtClient(settings, solver=object(), log=lambda _: None)
    page = RetryPage()
    solutions = iter([infoext.CaptchaSolution("first", 1.0), infoext.CaptchaSolution("second", 1.0)])
    sleeps: list[float] = []
    refreshes: list[object] = []
    refills: list[object] = []
    expected = InfoExtResult("EN TRÁMITE", None, None, None, None, 2)
    returned_forms = iter([True, False])

    monkeypatch.setattr(client, "_solve_current_captcha", lambda *_: next(solutions))
    monkeypatch.setattr(infoext.time, "sleep", sleeps.append)
    monkeypatch.setattr(client, "_refresh_captcha", lambda current_page: refreshes.append(current_page))
    monkeypatch.setattr(client, "_fill_identity", lambda current_page: refills.append(current_page))
    monkeypatch.setattr(client, "_raise_if_request_rejected", lambda _: None)
    monkeypatch.setattr(client, "_raise_if_expediente_not_found", lambda _: None)
    monkeypatch.setattr(client, "_form_was_returned", lambda _: next(returned_forms))
    monkeypatch.setattr(infoext, "parse_result", lambda _, captcha_attempts: expected)

    assert client._submit_with_captcha(page, captcha_dir=None) == expected
    assert sleeps == [settings.result_settle_delay_seconds, settings.captcha_retry_delay_seconds, settings.result_settle_delay_seconds]
    assert refreshes == []
    assert refills == [page]
    assert page.filled == ["first", "second"]
    assert page.submit_clicks == 2


@pytest.mark.parametrize("debug_enabled", [False, True])
def test_captcha_image_is_saved_without_page_or_result_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path, debug_enabled
) -> None:
    assessment = infoext.CaptchaAssessment(
        solution=infoext.CaptchaSolution("abc12", 1.0),
        variants=(),
        valid_length_variants=1,
        consensus_votes=1,
        submission_allowed=True,
    )
    client = InfoExtClient(SimpleNamespace(debug_submitted_captcha_responses=debug_enabled), solver=SimpleNamespace(assess=lambda _: assessment), log=lambda _: None)
    monkeypatch.setattr(client, "_current_captcha_bytes", lambda _: b"captcha bytes")

    solution = client._solve_current_captcha(object(), tmp_path, attempt=2)

    assert solution.text == "abc12"
    saved = list(tmp_path.glob("*_attempt-2.png"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"captcha bytes"
    report_path = saved[0].with_suffix(".json")
    assert report_path.exists() is debug_enabled
    if debug_enabled:
        assert json.loads(report_path.read_text())["selected_text"] == "abc12"


def test_submitted_captcha_response_artifacts_are_saved_only_when_enabled(tmp_path) -> None:
    events: list[str] = []
    client = InfoExtClient(SimpleNamespace(), solver=object(), log=events.append)

    client._save_submitted_captcha_response(
        object(),
        None,
        attempt=1,
        solution=infoext.CaptchaSolution("first", 1.0),
        form_was_returned=False,
    )
    assert events == []

    client._save_submitted_captcha_response(
        SubmissionArtifactPage(),
        tmp_path,
        attempt=2,
        solution=infoext.CaptchaSolution("k3mgp", 0.5),
        form_was_returned=True,
    )

    metadata_files = list(tmp_path.glob("*_attempt-2_metadata.json"))
    assert len(metadata_files) == 1
    metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
    assert metadata["captured_at"]
    assert metadata["attempt"] == 2
    assert metadata["submitted_captcha"] == "k3mgp"
    assert metadata["ocr_confidence"] == 0.5
    assert metadata["form_was_returned"] is True
    assert list(tmp_path.glob("*_attempt-2_response.html"))[0].read_text(encoding="utf-8") == (
        "<html><body>Los caracteres escritos no son correctos.</body></html>"
    )
    assert list(tmp_path.glob("*_attempt-2_response.txt"))[0].read_text(encoding="utf-8") == (
        "Los caracteres escritos no son correctos."
    )
    assert events == ["Saved submitted CAPTCHA response artifacts for attempt 2"]


@pytest.mark.parametrize("errors, expected", [
    ([{"field": "anio", "message": "Campo obligatorio"}], infoext.FormValidationError),
    ([{"field": "anio", "message": "Campo obligatorio"}, {"field": "captcha", "message": "Los caracteres escritos no son correctos."}], infoext.FormValidationError),
    ([{"field": "captcha", "message": "Los caracteres escritos no son correctos."}], None),
    ([], infoext.ResultParsingError),
])
def test_returned_form_errors_distinguish_captcha_from_identity(errors, expected) -> None:
    page = SimpleNamespace(locator=lambda _: SimpleNamespace(evaluate_all=lambda _: errors))
    if expected:
        with pytest.raises(expected):
            InfoExtClient._validate_returned_form(page)
    else:
        InfoExtClient._validate_returned_form(page)


def test_visible_birth_year_missing_stops_before_filling_the_field() -> None:
    year = SimpleNamespace(count=lambda: 1, is_visible=lambda: True,
                           fill=lambda _: pytest.fail("Must not fill a guessed birth year"))
    page = SimpleNamespace(locator=lambda selector: year if selector == "#anio" else SimpleNamespace(fill=lambda _: None))
    settings = SimpleNamespace(nie="Z12AB34C", fecha_presentacion="01/02/2026", ano_nacimiento="")
    client = InfoExtClient(settings, object(), lambda _: None)
    with pytest.raises(infoext.FormValidationError, match="infoext.ano_nacimiento"):
        client._fill_identity(page)
