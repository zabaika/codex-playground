"""User-visible scope and selection explanations in actual HTML/Markdown views."""
import copy

import pytest

from telegram_connector import analysis_evidence as evidence
from telegram_connector import analysis_rendering as rendering
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_delivery import sourced_result
from telegram_connector.tests.test_analysis_relations import records_fixture
from telegram_connector.tests.test_analysis_review_regressions import answer_result
from telegram_connector.tests.test_analysis_fact_support import FACTS


def render(job, result, format):
    if format == 'html':
        return '\n\n'.join(rendering._analysis_html_chunks(job, result))
    return rendering._full_analysis_markdown(job, result)


@pytest.mark.parametrize('format', ['html', 'markdown'])
@pytest.mark.parametrize('command', ['topics', 'search', 'qa'])
def test_constraints_are_inline_without_duplicate_blocks(manifest_job, format, command):
    store, job = manifest_job
    result = sourced_result(store, job)
    result = evidence.verify_analysis_sources(store, job['job_id'], result, generation=job['generation'])
    job = {**job, 'command': command}
    result['items'][0].update(
        title='Срок рассмотрения',
        text='Обработка занимает 30 дней.',
        conditions=['Обработка занимает 30 дней.', 'Нужен паспорт.'],
        exceptions=['Кроме нерезидентов.', 'Нужен паспорт.'],
        contradictions=['Другой источник: 60 дней.'])
    before = copy.deepcopy(result)
    text = render(job, result, format)
    for label in ('Условия', 'Исключения'):
        assert f'<b>{label}</b>' not in text
        assert f'### {label}' not in text
    for value in ('Обработка занимает 30 дней.', 'Нужен паспорт.', 'Кроме нерезидентов.'):
        assert text.count(value) == 1
    assert 'Противоречия' in text and 'Другой источник: 60 дней.' in text
    assert result == before


@pytest.mark.parametrize('format', ['html', 'markdown'])
@pytest.mark.parametrize('mode', ['all_messages', 'candidates', 'not_selected'])
def test_scope_explanation_is_separate_from_processed_count(manifest_job, format, mode):
    store, job = manifest_job
    result = sourced_result(store, job)
    result = evidence.verify_analysis_sources(store, job['job_id'], result, generation=job['generation'])
    result['coverage'].update(selection_mode=mode, processed_messages=1, known_skipped=None)
    text = render(job, result, format)
    assert 'Режим: последовательный анализ' not in text
    assert 'Режим: анализ отобранных кандидатов' not in text
    assert 'Обработано сообщений: 1;' in text
    notice = 'Анализ предварительно отобранных сообщений; охват периода неполный'
    assert (notice in text) == (mode == 'candidates')
    assert ('Отбор сообщений не завершён' in text) == (mode == 'not_selected')
    assert 'Не включено в выборку: неизвестно.' in text


@pytest.mark.parametrize('format', ['html', 'markdown'])
@pytest.mark.parametrize('count,opening', [(0, None), (1, 'Исключён 1 пункт'),
    (2, 'Исключены 2 пункта'), (5, 'Исключено 5 пунктов'),
    (11, 'Исключено 11 пунктов'), (21, 'Исключён 21 пункт')])
def test_digest_selection_notice_is_not_a_limitation(manifest_job, format, count, opening):
    store, job = manifest_job
    result = sourced_result(store, job)
    result = evidence.verify_analysis_sources(store, job['job_id'], result, generation=job['generation'])
    result['excluded_digest_only_items'] = count
    text = render(job, result, format)
    if opening:
        expected = opening + ': информация есть только в дайджесте, отдельного поста по теме нет.'
        assert text.count(expected) == 1
    else:
        assert 'информация есть только в дайджесте' not in text
    assert 'Ограничение:' not in text and '## Ограничения' not in text
    assert 'https://t.me/' in text and 'Условия из истории.' in text
    if format == 'html':
        assert '1 источник' in text


def test_verification_counts_excluded_items_without_generic_limitation(manifest_job):
    store, job = manifest_job
    records = records_fixture(store, job, [FACTS[1]])
    result = answer_result(records)
    checked = evidence.verify_analysis_sources(store, job['job_id'], result, generation=job['generation'])
    assert checked['excluded_digest_only_items'] == 1 and not checked['items']
    assert not checked['summary'] and checked['insufficient_information']
    assert not checked['limitations']
