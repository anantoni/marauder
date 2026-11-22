"""Exhaustive, case-sensitive schema diagnostics. Evaluation only; never generation.

Paths identify list positions (not guessed property-name joins). Missing records carry
their complete value, so no property or enum entry disappears from the census.
Enum entry order is immaterial, as in the benchmark; duplicate entries still count.
"""
from collections import Counter


def differences(generated, reference):
    """Return every changed leaf and every absent subtree, without truncation.

Presence flags distinguish an absent value from a present null or zero. ASCII
case-only annotations are diagnostic, never a scoring normalization. Classification
and source provenance belong to the caller, not this reference-blind comparator.
"""
    out = []

    def add(kind, path, g, r, gp=True, rp=True):
        row = dict(kind=kind, path=path, generated_present=gp, reference_present=rp)
        if gp:
            row['generated'] = g
        if rp:
            row['reference'] = r
        if gp and rp and isinstance(g, str) and isinstance(r, str):
            table = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
            row['ascii_case_only'] = g != r and g.translate(table) == r.translate(table)
        out.append(row)

    def walk(g, r, path, kind):
        if type(g) is not type(r):
            add(kind, path, g, r)
        elif isinstance(g, dict):
            for key in sorted(g.keys() | r.keys()):
                if key not in g or key not in r:
                    add(kind, path + [key], g.get(key), r.get(key), key in g, key in r)
                else:
                    walk(g[key], r[key], path + [key], kind)
        elif isinstance(g, list):
            for i in range(max(len(g), len(r))):
                if i >= len(g) or i >= len(r):
                    add(kind, path + [i], g[i] if i < len(g) else None,
                        r[i] if i < len(r) else None, i < len(g), i < len(r))
                else:
                    walk(g[i], r[i], path + [i], kind)
        elif g != r:
            add(kind, path, g, r)

    for family, g, r in [('types', generated[1], reference[1]),
                         ('enums', generated[0], reference[0])]:
        for name in sorted(g.keys() | r.keys()):
            path = [family, name]
            if name not in g or name not in r:
                kind = ('missing_' if name not in g else 'extra_') + family[:-1]
                add(kind, path, g.get(name), r.get(name), name in g, name in r)
            elif family == 'types':
                walk(g[name], r[name], path, 'type_field')
            else:
                gc, rc = Counter(map(tuple, g[name])), Counter(map(tuple, r[name]))
                for entry in sorted(gc.keys() | rc.keys()):
                    if gc[entry] != rc[entry]:
                        add('enum_entry', path + [list(entry)], gc[entry], rc[entry])
                        table = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
                        folded = lambda c: sum(v for (value, name), v in c.items()
                                               if value == entry[0] and name.translate(table) == entry[1].translate(table))
                        out[-1]['ascii_case_only'] = folded(gc) == folded(rc)
    return out


_FOLD = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
FIELD_KINDS = ('super', 'count', 'name', 'type', 'index', 'dim', 'enum_entry')


def _fold_type(t):
    return {k: (_fold_type(v) if isinstance(v, dict) else v.translate(_FOLD) if isinstance(v, str) and k != 't' else v)
            for k, v in t.items()}


def _fold_mapping(mapping):
    enums, types = mapping
    out_e = {k.translate(_FOLD): [(v, n.translate(_FOLD)) for v, n in ents] for k, ents in enums.items()}
    out_t = {}
    for k, st in types.items():
        out_t[k.translate(_FOLD)] = {
            'super': st['super'].translate(_FOLD) if isinstance(st['super'], str) else st['super'],
            'count': st['count'],
            'props': [dict(p, name=p['name'].translate(_FOLD), type=_fold_type(p['type'])) for p in st['props']]}
    return out_e, out_t


def accuracy(generated, reference, fold=False):
    """Record and field match counts against a reference mapping. Evaluation only.

Records are types and enums; the denominator is the union of both key sets, so missing and
extra records both count as wrong. A type contributes two header fields (super, count) and
four fields per property position (name, type tree, index, array dim); properties are aligned
by list position, as in differences(). An enum contributes one field per entry occurrence
(multiset, order-free). With fold=True every name is ASCII case-folded first.
"""
    if fold:
        generated, reference = _fold_mapping(generated), _fold_mapping(reference)
    ge, gt = generated
    re_, rt = reference
    total, exact = Counter(), Counter()
    rec_total = rec_exact = 0
    for name in gt.keys() | rt.keys():
        rec_total += 1
        g, r = gt.get(name), rt.get(name)
        if g is not None and r is not None and g == r:
            rec_exact += 1
        for key in ('super', 'count'):
            total[key] += 1
            exact[key] += g is not None and r is not None and g[key] == r[key]
        gp, rp = (g or {}).get('props', []), (r or {}).get('props', [])
        for i in range(max(len(gp), len(rp))):
            a = gp[i] if i < len(gp) else None
            b = rp[i] if i < len(rp) else None
            for kind, key in (('name', 'name'), ('type', 'type'), ('index', 'i'), ('dim', 'dim')):
                total[kind] += 1
                exact[kind] += a is not None and b is not None and a[key] == b[key]
    for name in ge.keys() | re_.keys():
        rec_total += 1
        gc, rc = Counter(map(tuple, ge.get(name, []))), Counter(map(tuple, re_.get(name, [])))
        if name in ge and name in re_ and gc == rc:
            rec_exact += 1
        for entry in gc.keys() | rc.keys():
            total['enum_entry'] += max(gc[entry], rc[entry])
            exact['enum_entry'] += min(gc[entry], rc[entry])
    return {'records': [rec_exact, rec_total],
            'fields': [sum(exact.values()), sum(total.values())],
            'by_kind': {k: [exact[k], total[k]] for k in FIELD_KINDS if total[k]}}
