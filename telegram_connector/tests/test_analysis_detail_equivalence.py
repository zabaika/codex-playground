"""Input-only detail equivalence preserves subject, role, values and polarity."""
import copy

import pytest

from telegram_connector import analysis_evidence as evidence
from telegram_connector.tests.test_analysis_single_text import owned_record


def known_details():
    record = dict(owned_record('Выпущена FreeVideo с MiniMax H3.',
        ['Заявленный минимум составляет 8 ГБ VRAM и 16 ГБ RAM.']), schema_version=3)
    record['entities'] = ['FreeVideo', 'MiniMax H3']
    return {'a': record, 'b': copy.deepcopy(record)}


def test_detail_equivalence_is_an_input_hint_without_representative():
    known = known_details()
    before = copy.deepcopy(known)
    assert evidence.equivalent_detail_groups(known) == [
        {'refs': [{'record_id': key, 'index': 0} for key in known]}]
    assert known == before


@pytest.mark.parametrize('change', ['subject', 'quantity', 'polarity', 'record_role', 'event_date'])
def test_detail_equivalence_rejects_different_subject_values_roles_and_dates(change):
    known = known_details()
    other = known['b']
    if change == 'subject':
        other['entities'] = ['OtherVideo', 'Other model']
    elif change == 'quantity':
        other['details'] = ['Заявленный минимум составляет 12 ГБ VRAM и 16 ГБ RAM.']
    elif change == 'polarity':
        other['details'] = ['Заявленный максимум составляет 8 ГБ VRAM и 16 ГБ RAM.']
    elif change == 'record_role':
        other['record_type'] = 'question'
    else:
        known['a']['claim'] += ' Релиз 1 октября.'
        other['claim'] += ' Релиз 9 октября.'
    assert evidence.equivalent_detail_groups(known) == []


@pytest.mark.parametrize('entities,other_entities', [
    (['EUR'], ['EUR']), (['Uno World', 'RTX 4090'], ['Dos World', 'RTX 4090'])])
def test_currency_or_contextual_hardware_does_not_equate_different_primary_subjects(entities, other_entities):
    known = known_details()
    known['a'].update(claim='Выпущена Uno World.', entities=entities)
    known['b'].update(claim='Выпущена Dos World.', entities=other_entities)
    assert evidence.equivalent_detail_groups(known) == []
