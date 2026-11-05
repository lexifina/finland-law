"""Current-text parts for finland-law: the acts in force that the replay cannot verify (LEFT-OUT.csv), published as
their current Finlex consolidation, one commit each (Kind: baseline, credited to the publisher).

Each act: the Finnish consolidation `statute-consolidated/<y>/<n>/fin@<version>` (build.cons_url), rendered with the
replay builder's own parser and file layout (replay.parse_act(notes_ok=True), build.files_of), so a current-text act
reads exactly like a replayed one at its tip. Left out (with the reason): no text (contentAbsent), amending acts
listed as in force, a consolidation that already prints an amendment not yet in force, a parse the builder refuses.

Dating (corpus rule): the text took effect on the latest in-force day among the amending acts Finlex lists (the
newest wording); an act Finlex lists no amending act for is its original, dated by its own in-force day; with neither,
the retrieval day (Text-as-of, In-force-basis: source gives no date). Never before the act's own in-force day.

Gate (independent): check_words.source_words(consolidation) against check_words.repo_words(files, final=True), both
reduced to casefolded letters-and-digit words; any difference leaves the act out.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILDER = HERE.parent
sys.path.insert(0, str(BUILDER))
import build as B  # noqa: E402
import replay as R  # noqa: E402
import check_words as W  # noqa: E402

N = R.N
AMENDING = re.compile(r'(muuttamisesta|kumoamisesta|voimaantulosta|voimaanpanosta|muuttamisesta ja '
                      r'väliaikaisesta muuttamisesta)\.?$')


def act_id(a: dict) -> str:
    return f'{a["no"]}/{a["year"]}'


def cons_doc_lxml(url: str):
    """The Finnish akomaNtoso of a cached answer, as lxml (check_words' independent reader)."""
    import hashlib
    from lxml import etree
    f = B.CACHE / (hashlib.sha1(url.encode()).hexdigest() + '.bin')
    root = etree.parse(str(f)).getroot()
    docs = [e for e in root.iter() if W.local(e) == 'akomaNtoso']
    for d in docs:
        lg = next((e for e in d.iter() if W.local(e) == 'FRBRlanguage'), None)
        if lg is not None and lg.get('language') == 'fin':
            return d
    raise ValueError('no Finnish document')


def prepare(a: dict, today: str, retrieved: str) -> dict:
    """{'ok': True, ...} with files and message fields, or {'ok': False, 'reason': ...}. Offline (cache only)."""
    ident = act_id(a)
    url = B.cons_url(a['year'], a['no'], a.get('version', ''))
    try:
        cb = B.fetch(url, offline=True)
    except SystemExit:
        return {'ok': False, 'act': ident, 'reason': 'not retrieved', 'url': url}
    if cb is None:
        return {'ok': False, 'act': ident, 'reason': 'consolidation not served'}
    cons = B.parse_bytes(cb)
    if cons is None:
        return {'ok': False, 'act': ident, 'reason': 'no Finnish document'}
    if cons.find('.//' + N + 'hcontainer[@name="contentAbsent"]') is not None:
        return {'ok': False, 'act': ident, 'reason': 'no text in the open data (contentAbsent)'}
    title = R.txt(cons.find('.//' + N + 'docTitle'))
    if AMENDING.search(title):
        return {'ok': False, 'act': ident, 'reason': 'an amending act listed as in force'}
    ams = B.amended_by(cons)
    days = sorted({d for x in ams for d in x['dates']})
    if any(d > today for d in days):
        return {'ok': False, 'act': ident, 'reason': 'the consolidation prints an amendment not yet in force'}
    inf = cons.find('.//' + N + 'proprietary/' + B.FIN + 'inForce/' + B.FIN + 'dateEntryIntoForce')
    own = inf.get('date') if inf is not None else None
    if own and own > today:
        return {'ok': False, 'act': ident, 'reason': 'not in force yet'}
    try:
        act = R.parse_act(cons, notes_ok=True)
    except Exception as e:  # noqa: BLE001  (ReplayError and malformed markup alike)
        return {'ok': False, 'act': ident, 'reason': f'parse: {str(e)[:160]}'}
    if act.get('text_only') or not any(ch['sections'] for ch in act['chapters']):
        return {'ok': False, 'act': ident, 'reason': 'no sections'}
    try:
        files = B.files_of(act, B.intro_of(cons))
        # Finlex's amendment labels " (29.4.1994/318)" are editorial additions (build NORMALISATION (1)): the
        # replayed files never carry them, so neither do these
        files = {n: B.INLINE_LABEL.sub('', t) for n, t in files.items()}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'act': ident, 'reason': f'render: {str(e)[:160]}'}
    # the gate: the source's words against the files' words (independent reader, lxml)
    try:
        doc = cons_doc_lxml(url)
        W.drop_superseded(doc)
        src, heads = W.source_words(doc, with_title=False)
        mine, mheads = W.repo_words(files, final=True)
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'act': ident, 'reason': f'gate read: {str(e)[:160]}'}
    if src != mine:
        import difflib
        sm = difflib.SequenceMatcher(None, src, mine, autojunk=False)
        op = next(o for o in sm.get_opcodes() if o[0] != 'equal')
        return {'ok': False, 'act': ident, 'reason': f'gate: words differ ({op[0]} '
                f'{" ".join(src[op[1]:op[2]][:6])!r} / {" ".join(mine[op[3]:op[4]][:6])!r})'}
    if not mheads <= heads | {()}:
        return {'ok': False, 'act': ident, 'reason': f'gate: chapter heading not in the source {sorted(mheads - heads)[:2]}'}
    dated = [x for x in ams if x['dates']]
    if dated:
        last = max(dated, key=lambda x: (max(x['dates']), x['year'], int(re.match(r'\d+', x['no']).group())))
        day, basis = max(last['dates']), (f'the in-force day Finlex gives for the latest amending act it lists, '
                                          f'{last["no"]}/{last["year"]}')
    elif not ams and own:
        last, day, basis = None, own, "the act's own in-force day (Finlex lists no amending act)"
    else:
        last, day, basis = None, retrieved, 'source gives no date'
    if own and day < own:
        day = own
    return {'ok': True, 'act': ident, 'cfg': a, 'title': title, 'files': files, 'in_force': day, 'basis': basis,
            'text_as_of': retrieved if basis == 'source gives no date' else None,
            'last': last, 'amendments': len(ams), 'undated': len(ams) - len(dated), 'own_in_force': own,
            'consolidated_on': B.frbr(cons, 'dateConsolidated'), 'issued': B.frbr(cons, 'dateIssued'),
            'url': url, 'words': len(src)}


def message(p: dict, folder: str) -> str:
    cfg = p['cfg']
    ident = p['act']
    short = p['title'] if len(p['title']) <= 60 else p['title'][:57] + '...'
    lines = [f'{short} ({ident})', '', p['title'], '',
             'The act as Finlex consolidates it today ('
             + (f'consolidation of {p["consolidated_on"]}, ' if (p.get('consolidated_on') or '9') <= p['retrieved'] else '')
             + f'retrieved {p["retrieved"]}). The replay of its amending acts cannot be verified for this act, so the record holds '
             'only this current text: its earlier versions are not in the record, and it is dated by the day its '
             'newest wording took effect.', '']
    if p['last']:
        lines += [f'Latest amending act listed: {p["last"]["no"]}/{p["last"]["year"]}'
                  + (f' ({p["last"]["note"]})' if p['last'].get('note') else '')
                  + f'; {p["amendments"]} amending acts listed in all'
                  + (f', {p["undated"]} of them with no in-force day.' if p['undated'] else '.'), '']
    lines += ['Evidence: current consolidation text from Finlex open data; every word of it is in the files, in order',
              'Kind: baseline',
              f'Enacted: {p["issued"]}' if p.get('issued') else None,
              f'In-force: {p["in_force"]}',
              f'Text-as-of: {p["text_as_of"]}' if p.get('text_as_of') else None,
              f'In-force-basis: {p["basis"]}',
              f'Source: {B.SITE}/fi/lainsaadanto/{cfg["year"]}/{cfg["no"]}',
              f'Text-source: {p["url"]}']
    return '\n'.join(x for x in lines if x is not None) + '\n'


def left_out_rows(path: Path) -> list[dict]:
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


if __name__ == '__main__':  # offline dry count: python -X utf8 fi_current.py <published acts.csv> [today]
    import collections
    import json
    pub = {r['act'] for r in csv.DictReader(open(sys.argv[1], encoding='utf-8'))}
    today = sys.argv[2] if len(sys.argv) > 2 else dt.date.today().isoformat()
    acts = [a for a in B.all_acts() if act_id(a) not in pub]
    res = [prepare(a, today, B.retrieved()) for a in acts]
    c = collections.Counter('OK' if r['ok'] else re.sub(r'\(.*', '', r['reason'])[:70] for r in res)
    for k, v in c.most_common(40):
        print(v, k)
    json.dump([{k: v for k, v in r.items() if k not in ('files', 'cfg', 'last')} for r in res],
              open(HERE / 'dry.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
