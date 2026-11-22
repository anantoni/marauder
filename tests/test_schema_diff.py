"""Synthetic census regressions; no executable or captured schema fixtures."""
import copy
import os
import sys
from usmap_generator.engine.schema_diff import differences


def fixture():
    return ({'E': [(0, 'A'), (4, 'B')]}, {'S': {'super': None, 'count': 2, 'props': [
        {'i': 0, 'dim': 2, 'name': 'Id', 'type': {'t': 'ArrayProperty', 'inner':
         {'t': 'StructProperty', 'struct': 'Vector'}}}]}})


def test_every_changed_field_and_nested_reference():
    ref = fixture()
    gen = copy.deepcopy(ref)
    s = gen[1]['S']
    s.update(super='Base', count=5)
    s['props'][0].update(i=3, dim=1, name='ID')
    s['props'][0]['type']['inner']['struct'] = 'Rotator'
    rows = differences(gen, ref)
    assert len(rows) == 6
    assert {tuple(x['path'][2:]) for x in rows} == {
        ('super',), ('count',), ('props', 0, 'i'), ('props', 0, 'dim'),
        ('props', 0, 'name'), ('props', 0, 'type', 'inner', 'struct')}
    assert sum(x.get('ascii_case_only', False) for x in rows) == 1


def test_absent_records_retain_complete_subtree():
    ref = fixture()
    rows = differences(({}, {}), ref)
    assert len(rows) == 2
    assert {x['kind'] for x in rows} == {'missing_type', 'missing_enum'}
    assert all(not x['generated_present'] and x['reference_present'] for x in rows)
    assert next(x for x in rows if x['kind'] == 'missing_type')['reference'] == ref[1]['S']
    assert {x['kind'] for x in differences(ref, ({}, {}))} == {'extra_type', 'extra_enum'}


def test_absent_property_null_and_zero_are_distinct():
    ref = fixture()
    gen = copy.deepcopy(ref)
    gen[1]['S']['props'] = []
    gen[1]['S'].pop('super')
    gen[1]['S']['count'] = 0
    rows = differences(gen, ref)
    assert len(rows) == 3
    sup = next(x for x in rows if x['path'][-1] == 'super')
    assert sup['reference'] is None and not sup['generated_present']
    assert next(x for x in rows if x['path'][-1] == 'count')['generated'] == 0


def test_enum_order_duplicates_names_and_values():
    ref = fixture()
    gen = copy.deepcopy(ref)
    gen[0]['E'].reverse()
    assert not differences(gen, ref)
    gen[0]['E'].append((0, 'A'))
    assert differences(gen, ref)[0]['generated'] == 2
    gen[0]['E'] = [(1, 'a'), (4, 'B')]
    assert len(differences(gen, ref)) == 2


def test_many_missing_records_are_never_truncated():
    ref = ({}, {str(n): {'super': None, 'count': 0, 'props': []} for n in range(101)})
    assert len(differences(({}, {}), ref)) == 101
    assert differences(ref, ref) == []


def test_enum_case_annotation_cannot_hide_numeric_or_multiplicity_errors():
    rows = differences(({'E': [(0, 'ID')]}, {}), ({'E': [(0, 'Id')]}, {}))
    assert len(rows) == 2 and all(x['ascii_case_only'] for x in rows)
    rows = differences(({'E': [(1, 'ID')]}, {}), ({'E': [(0, 'Id')]}, {}))
    assert not any(x['ascii_case_only'] for x in rows)
    rows = differences(({'E': [(0, 'ID'), (0, 'ID')]}, {}), ({'E': [(0, 'Id')]}, {}))
    assert not any(x['ascii_case_only'] for x in rows)


def test_accuracy_counts_records_and_fields():
    from usmap_generator.engine.schema_diff import accuracy
    ref = fixture()
    assert accuracy(ref, ref) == {'records': [2, 2], 'fields': [8, 8], 'by_kind': {
        'super': [1, 1], 'count': [1, 1], 'name': [1, 1], 'type': [1, 1], 'index': [1, 1],
        'dim': [1, 1], 'enum_entry': [2, 2]}}
    gen = copy.deepcopy(ref)
    gen[1]['S']['props'][0]['name'] = 'ID'
    gen[0]['X'] = [(0, 'Y')]
    a = accuracy(gen, ref)
    assert a['records'] == [1, 3] and a['fields'] == [7, 9]
    assert accuracy(gen, ref, fold=True)['records'] == [2, 3]
    assert accuracy(({}, {}), ref)['fields'] == [0, 8]
