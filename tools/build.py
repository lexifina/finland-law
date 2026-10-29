"""Builds the Finnish git-for-law repository from Finlex open data (Akoma Ntoso API).

    python -X utf8 build.py scan  [--offline] [--workers 4] [--only Y/N ...]   # replay + gate every act -> scan.json
    python -X utf8 build.py build --repo DIR --today YYYY-MM-DD [--offline]   # commit every act that passed the gate

The acts: every IN-FORCE consolidated act (laki) Finlex lists (acts.json, from enumerate_acts.py). For each act the
current consolidation (`statute-consolidated/<y>/<n>/fin@<version>`), the original statute (`statute/<y>/<n>/fin@`) and
every amending act in the consolidation's `amendedBy` list are fetched. Finlex serves only the CURRENT consolidation,
so the intermediate versions are REPLAYED (replay.py; no AI, no fuzzy edits). The gate: the replayed end state must
equal the consolidation after the declared normalisation (NORMALISATION below). An act that fails is left out and
listed in scan.json / the repository's LEFT-OUT.csv with the reason.

One commit per amending act per in-force day, authored by the act; one tag per act and amendment. Deterministic
fast-import. Standard library only.

Prior art consulted for API behaviour (no code copied): LawVM, https://github.com/eliask/lawvm (MIT), whose reference
country is Finland; legalize-dev, https://github.com/legalize-dev .
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import xml.etree.ElementTree as ET
from multiprocessing.dummy import Pool  # threads, results in input order
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay as R  # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get('FINLEX_SOURCES') or HERE / 'cache')
API = 'https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act'
SITE = 'https://www.finlex.fi'
N = R.N
FIN = '{http://data.finlex.fi/schema/finlex}'
PUBLISHER = 'Finlex (Oikeusministeriö)'
COMMITTER = ('Lexifina', 'finland-law@lexifina.com')

# The constitution keeps its folder name from the first build.
NAMED = {(1999, '731'): ('perustuslaki', 'Perustuslaki')}

# ---- normalisation (declared; applied to BOTH sides of the gate and of check_repo) ----------------------------------
LABEL = re.compile(r' \(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)$')
FIXES = [('―', '–'),          # base prints "― ―" for an elided name, the consolidation "– –"
         ('—', '–'),          # amending acts print "1—4 momentissa", the consolidation "1–4"
         ('valtio-neuvoston', 'valtioneuvoston')]  # 731/1999 131 §: a line-break hyphen in the original
NORMALISATION = ('(1) a trailing amendment label such as " (4.11.2011/1112)" on a heading is dropped (Finlex adds it to '
                 'the consolidation); (2) U+2015 and U+2014 (dashes) are read as U+2013; (3) the original\'s line-break hyphen in '
                 '"valtio-neuvoston" (731/1999 131 §) is read as "valtioneuvoston", as the consolidation prints it; '
                 '(4) the chapter line is not compared for sections the original prints outside any chapter '
                 '(e.g. 130 § and 131 § of 731/1999, which the consolidation files under chapter 13); (5) Finlex\'s '
                 'editorial notes in the consolidation (noteAuthorial) are not part of the text and are not compared, '
                 'nor are the correction notes in some originals (authorialNote, "Merkitty kohta oikaistu ...", '
                 'which give the wording before a published correction; the corrected wording is the text); '
                 '(6) Finlex\'s repeal markers in the consolidation ("3 momentti on kumottu L:lla 29.8.2008/562.", '
                 '"4 § on kumottu L:lla ...") are editorial notes, not text: the replay removes the provision itself '
                 '(a repealed subsection keeps its place, so later subsections keep their numbers); (7) a heading\'s '
                 'final full stop, which older originals print ("Lain soveltamisala.") and the consolidation drops, '
                 'is not compared, and a chapter number printed bare in an older original ("1") is read as the '
                 'consolidation prints it ("1 luku"); (8) spacing before the section sign ("3§" in older originals, '
                 '"3 §" in the consolidation) and after it ("57 §: n", "57 §:n") is not compared; (9) a hyphen between '
                 'two numbers ("3-5 §") is read as the en dash the consolidation prints ("3–5 §"); (10) Finlex\'s '
                 'amendment labels inside a line ("oloista: (10.8.2018/678)") are dropped, as on headings; (11) a '
                 'hyphen inside a word, with or without a space after it, is not compared: line-break hyphenation '
                 'left in one source ("rautatieyrityk-sellä", "toimeksi- antosopimus", "ar-vioidusta"); (12) how a '
                 'section\'s lines are divided into subsections is not compared (some texts print two momentti as '
                 'one subsection with two paragraphs, or one momentti as two); the lines themselves and their order '
                 'are.')


def nline(s: str) -> str:
    for a, b in FIXES:
        s = s.replace(a, b)
    s = SECTION_SIGN.sub(r'\1 §', s)
    s = SIGN_COLON.sub('§:', s)
    s = INLINE_LABEL.sub('', s)
    s = WORD_HYPHEN.sub('', s)
    return NUM_RANGE.sub(r'\1–', s)


SECTION_SIGN = re.compile(r'(\d[a-z]?)\s*§')  # "3§" in older originals, "3 §" in the consolidation (spacing only)
SIGN_COLON = re.compile(r'§\s*:\s*')  # "57 §: n" / "57 §:n"
INLINE_LABEL = re.compile(r' ?\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)')  # Finlex's amendment label inside a line
WORD_HYPHEN = re.compile(r'(?<=[^\W\d_])[-\u2010\u2011\u00ad]\s*(?=[^\W\d_])')  # "yrityk-sellä", "toimeksi- anto"
NUM_RANGE = re.compile(r'(\d)\s?-\s?(?=\d)')  # "3-5 §" in older originals, "3–5 §" in the consolidation


def nheading(s: str) -> str:
    return nline(LABEL.sub('', s)).rstrip().removesuffix('.').rstrip()


def nchapter(num: str) -> str:
    num = num.strip().removesuffix('.')
    return num + ' luku' if re.fullmatch(r'[0-9]+ ?[a-z]?', num) else num


def canon(act: dict, chapter_free=()) -> dict:
    out = {}
    for k, ch, s in R.sections(act):
        label = '' if (not ch['num'].strip() or k in chapter_free) else (nchapter(ch['num']) + ' ' + nheading(ch['heading']))
        part = '' if k in chapter_free else nline(ch.get('part', ''))
        out[k] = (part, label, nline(s.get('cross', '')), nheading(s['heading']),
                  [nline(x) for sub in s['subs'] for x in R.sub_lines(sub)])
    return out


# ---- the retrieved answers --------------------------------------------------------------------------------------------------------
_lock = threading.Lock()
STATS = {'fetched': 0, 'cached': 0, 'started': time.time()}
# How the answers were retrieved is not part of the published record: this build reads the retrieved answers only.
# A retrieval module may install itself here (an object with retrieve(url, path) and heartbeat(line, force)).
_net = None


def cache_path(url):
    return CACHE / (hashlib.sha1(url.encode()).hexdigest() + '.bin')


def fetch(url: str, offline: bool = False):
    """Body bytes, or None for a retrieved 404 (stored as the bytes 0x00 '404'). Read from the retrieved answers; without
    --offline, an answer not yet retrieved is asked for by the installed retrieval module, if any."""
    f = cache_path(url)
    if f.exists():
        d = f.read_bytes()
        STATS['cached'] += 1
        return None if d == b'\x00404' else d
    if offline or _net is None:
        raise SystemExit(f'--offline: {url} is not in the cache')
    return _net.retrieve(url, f)


def parse_bytes(b: bytes, lang='fin'):
    root = ET.fromstring(b)
    if root.tag == N + 'akomaNtoso':
        return root
    docs = root.findall('.//' + N + 'akomaNtoso')
    for d in docs:
        lg = d.find('.//' + N + 'FRBRlanguage')
        if lg is not None and lg.get('language') == lang:
            return d
    return docs[0] if docs else None


def retrieved() -> str:
    p = CACHE / 'retrieved.txt'
    return p.read_text(encoding='utf-8').strip() if p.exists() else dt.date.today().isoformat()


def amended_by(cons) -> list:
    out = []
    ab = cons.find('.//' + FIN + 'amendedBy')
    if ab is None:
        return out
    for ref in ab.findall(FIN + 'statuteReference'):
        href = ref.find(FIN + 'ref').get('href')
        m = re.fullmatch(r'/akn/fi/act/statute/(\d{4})/([0-9a-z-]+)', href)
        if not m:
            raise R.ReplayError('amendedBy reference not understood: ' + href)
        dates = sorted({d.get('date') for d in ref.iter(FIN + 'dateEntryIntoForce') if d.get('date')})
        note = ref.find(FIN + 'noteEditorial')
        out.append({'year': int(m.group(1)), 'no': m.group(2), 'dates': dates,
                    'note': R.txt(note) if note is not None else ''})
    return out


def frbr(root, name):
    for e in root.iter(N + 'FRBRdate'):
        if e.get('name') == name:
            return e.get('date')
    return None


MONTHS = ['tammikuuta', 'helmikuuta', 'maaliskuuta', 'huhtikuuta', 'toukokuuta', 'kesäkuuta', 'heinäkuuta',
          'elokuuta', 'syyskuuta', 'lokakuuta', 'marraskuuta', 'joulukuuta']


def fi_date(d, mon, y):
    return f'{y}-{MONTHS.index(mon) + 1:02d}-{int(d):02d}'


def expiry_of(commencement: str):
    """'... ja on voimassa 31 päivään joulukuuta 2021' -> '2021-12-31' (the last day in force), else None."""
    m = re.search(r'on voimassa (?:\d+ päivästä \w+ \d{4} )?(\d+) päivään (\w+) (\d{4})', commencement)
    if m and m.group(2) in MONTHS:
        return fi_date(m.group(1), m.group(2), m.group(3))
    m = re.search(r'on voimassa (?:\d+\.\d+\.\d{4} )?(\d+)\.(\d+)\.(\d{4}) (?:asti|saakka)', commencement)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    return None


COMMENCE = re.compile(r'Tämä laki tulee voimaan (\d{1,2}) päivänä ([a-zä]+) (\d{4})\s*\.')


def commencement_day(root):
    """The day an act's own commencement provision names, when it names exactly one fixed day: the entryIntoForce
    container, else the body's text, must say "Tämä laki tulee voimaan <d> päivänä <month> <yyyy>." (one such
    sentence; any "voimaan" by decree / a day set later gives None)."""
    eif = [h for h in root.iter(N + 'hcontainer') if h.get('name') == 'entryIntoForce']
    if eif:
        t = R.txt(eif[0])
    else:
        body = root.find('.//' + N + 'body')
        t = R.txt(body) if body is not None else ''
    hits = COMMENCE.findall(t)
    if len(hits) != 1 or len(re.findall(r'\bvoimaan\b', t)) != 1 or hits[0][1] not in MONTHS:
        return None
    return fi_date(*hits[0])


_dp = Path(__file__).resolve().parent / 'decree_dates.json'  # enacting decrees' own words (Finlex treaty reference database)
DECREES = json.loads(_dp.read_text(encoding='utf-8')) if _dp.exists() else {}
NOTE_DATE = re.compile(r'\bL\s+(\d+[a-z]?/\d{4})\s+tuli\s+(?:\S+\s+){0,3}?(?:A:n|L:n|TPA:n|VNA:n|asetuksen)\s*'
                       r'(?:\d+/\d{4}\s+)?mukaisesti\s+voimaan\s+(\d{1,2})\.(\d{1,2})\.(\d{4})')


def note_dates(cons) -> dict:
    """Finlex's editorial notes in a consolidation that date an act brought into force by a decree or another act:
    "L 387/1964 tuli A:n 388/1964 mukaisesti voimaan 1.9.1964." -> {'387/1964': ('1964-09-01', the note)}. An act
    named by two notes with different days is left out."""
    out, bad = {}, set()
    for h in cons.iter(N + 'hcontainer'):
        if h.get('name') != 'noteAuthorial':
            continue
        t = R.WS.sub(' ', ''.join(h.itertext())).strip()
        for m in NOTE_DATE.finditer(t):
            day = f'{m.group(4)}-{int(m.group(3)):02d}-{int(m.group(2)):02d}'
            if m.group(1) in out and out[m.group(1)][0] != day:
                bad.add(m.group(1))
            out[m.group(1)] = (day, m.group(0) + '.')
    return {k: v for k, v in out.items() if k not in bad}


def cons_url(y, n, version):
    return f'{API}/statute-consolidated/{y}/{n}/fin@{version}'


def load_act(cfg: dict, offline: bool) -> dict:
    """The consolidation, the original and the list of amending acts (amending act bodies are fetched lazily)."""
    y, n = cfg['year'], cfg['no']
    cb = fetch(cons_url(y, n, cfg.get('version', '')), offline)
    if cb is None:
        raise R.ReplayError('consolidation not served')
    cons = parse_bytes(cb)
    bb = fetch(f'{API}/statute/{y}/{n}/fin@', offline)
    if bb is None:
        if cons.find('.//' + N + 'hcontainer[@name="contentAbsent"]') is not None:
            raise R.ReplayError('original statute not served; the consolidation has no text either (contentAbsent)')
        raise R.ReplayError('original statute not served')
    base = parse_bytes(bb)
    ams = amended_by(cons)
    enacted = {}
    for h in cons.iter(N + 'hcontainer'):
        if h.get('name') == 'entryIntoForce':
            hd = h.find(N + 'heading')
            m = re.match(r'(\d+)\.(\d+)\.(\d{4})/(\d+)', R.txt(hd)) if hd is not None else None
            if m:
                enacted[(int(m.group(3)), m.group(4))] = f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    notes = note_dates(cons)
    for a in ams:
        a['enacted'] = enacted.get((a['year'], a['no']))
        if not a['dates']:
            # Finlex lists no in-force day (older amendments): read it from the act's own commencement provision when
            # that names one fixed day ("Tämä laki tulee voimaan 1 päivänä heinäkuuta 1986."), else leave it undated
            b = fetch(f'{API}/statute/{a["year"]}/{a["no"]}/fin@', offline)
            day = commencement_day(parse_bytes(b)) if b is not None else None
            if day:
                a['dates'], a['dated_from'] = [day], 'commencement'
            elif f'{a["no"]}/{a["year"]}' in notes:
                a['dates'], a['dated_from'] = [notes[f'{a["no"]}/{a["year"]}'][0]], 'note'
                a['date_note'] = notes[f'{a["no"]}/{a["year"]}'][1]
    # an amendment with no in-force date sorts by its year (so a 1978 amendment is met, and fails, before 1993's)
    ams.sort(key=lambda a: (min(a['dates']) if a['dates'] else f'{a["year"]}-00', a['year'],
                            int(re.match(r'\d+', a['no']).group())))
    # the act's own in-force day is under <proprietary>; the inForce elements inside amendedBy are the amending acts'
    inf = cons.find('.//' + N + 'proprietary/' + FIN + 'inForce/' + FIN + 'dateEntryIntoForce')
    base_inforce, base_dated_from = (inf.get('date') if inf is not None else None), 'finlex'
    if not base_inforce:
        base_inforce, base_dated_from = commencement_day(base), 'commencement'
    base_note = None
    if not base_inforce and f'{n}/{y}' in notes:
        (base_inforce, base_note), base_dated_from = notes[f'{n}/{y}'], 'note'
    if not base_inforce and f'{n}/{y}' in DECREES:  # "by decree": the day the enacting decree names
        dd = DECREES[f'{n}/{y}']
        base_inforce, base_dated_from = dd['day'], 'decree'
        base_note = f'{dd["decree"]}: "{dd["sentence"]}" (treaty reference {dd["treaty"]})'
    return {'base_dated_from': base_dated_from, 'base_note': base_note, 'cfg': cfg, 'cons': cons, 'base': base, 'ams': ams,
            'consolidated_on': frbr(cons, 'dateConsolidated'), 'base_published': frbr(base, 'datePublished'),
            'base_enacted': frbr(base, 'dateIssued'), 'base_inforce': base_inforce}


def load_amendment(a, offline):
    if 'act' in a:
        return
    b = fetch(f'{API}/statute/{a["year"]}/{a["no"]}/fin@', offline)
    if b is None:
        raise R.ReplayError(f'amending act {a["no"]}/{a["year"]} not served')
    root = parse_bytes(b)
    a['root'] = root
    a['act'] = R.parse_act(root)
    a['published'] = frbr(root, 'datePublished')
    a['title'] = a['act']['title']
    eif = [h for h in root.iter(N + 'hcontainer') if h.get('name') == 'entryIntoForce']
    a['commencement'] = R.txt(eif[0]) if eif else ''


def replay_all(data: dict, offline: bool, today: str):
    """Replays every amending act in in-force order. Returns events: [(day, kind, amendment index, act state)].
    kind: 'base', 'amend', 'expire'. Raises ReplayError."""
    if not data['base_inforce']:
        raise R.ReplayError('no in-force date for the original (Finlex gives none and its commencement provision names '
                            'no fixed day, e.g. "asetuksella säädettävänä ajankohtana")')
    act = R.parse_act(data['base'])
    free = {k for k, ch, s in R.sections(act) if not ch['num'].strip()}
    events = [(data['base_inforce'], 'base', -1, copy.deepcopy(act), [])]
    pending = []  # temporary amendments: (revert day, saved sections, keys, index)
    for j, a in enumerate(data['ams']):
        if not a['dates']:
            raise R.ReplayError(f'{a["no"]}/{a["year"]}: no commencement date')
        if len(a['dates']) != 1:
            raise R.ReplayError(f'{a["no"]}/{a["year"]}: staged commencement {a["dates"]}')
        day = a['dates'][0]
        if day < data['base_inforce']:
            raise R.ReplayError(f'{a["no"]}/{a["year"]}: in force ({day}) before the original ({data["base_inforce"]})')
        for p in sorted(pending):
            if p[0] <= day:
                restore(act, p, events)
                pending.remove(p)
        load_amendment(a, offline)
        before = copy.deepcopy(act)
        try:
            changed, temporary = R.apply_act(act, a['act'], target=(data['cfg']['no'], data['cfg']['year']))
        except R.ReplayError as e:
            raise R.ReplayError(f'{a["no"]}/{a["year"]}: {e}') from None
        a['changed'] = changed
        if temporary or re.search(r'on voimassa .*?(asti|saakka|päivään)', a['commencement']):
            exp = expiry_of(a['commencement'])
            if not exp:
                raise R.ReplayError(f'{a["no"]}/{a["year"]}: temporary, expiry not understood')
            for p in pending:
                if set(p[2]) & set(changed):
                    raise R.ReplayError(f'{a["no"]}/{a["year"]}: overlaps an earlier temporary amendment')
            revert = (dt.date.fromisoformat(exp) + dt.timedelta(days=1)).isoformat()
            a['expires'] = exp
            if revert <= today:
                pending.append((revert, before, changed, j))
        for p in pending:
            if p[3] != j and set(p[2]) & set(changed):
                raise R.ReplayError(f'{a["no"]}/{a["year"]}: amends a provision under a temporary amendment')
        events.append((day, 'amend', j, copy.deepcopy(act), changed))
    for p in sorted(pending):
        restore(act, p, events)
    return events, free


def restore(act, p, events):
    """A temporary amendment's provisions return to their earlier text the day after it expires."""
    revert, before, keys, j = p
    for k in keys:
        if k.startswith('luku '):
            raise R.ReplayError('temporary chapter change')
        ch_b, s_b = R.find_sec(before, k)
        ch_a, s_a = R.find_sec(act, k)
        if s_b is None or s_a is None:
            raise R.ReplayError('temporary insertion or repeal')
        ch_a['sections'][ch_a['sections'].index(s_a)] = copy.deepcopy(s_b)
    events.append((revert, 'expire', j, copy.deepcopy(act), keys))


def cons_absent(data: dict) -> bool:
    """Finlex's consolidation carries no text (contentAbsent). For an act Finlex lists no amending act for, the original
    is then the current text, and nothing is compared; an amended act is left out."""
    if data['cons'].find('.//' + N + 'hcontainer[@name="contentAbsent"]') is None:
        return False
    if data['ams']:
        raise R.ReplayError('the consolidation has no text (contentAbsent), so the replay cannot be verified')
    blocks = {n for n, _t in R.parse_act(data['base'])['enacting_blocks']}
    title = R.txt(data['base'].find('.//' + N + 'docTitle'))
    if blocks & {'substitutions', 'insertions', 'repeals', 'repeal'} or \
            re.search(r'(muuttamisesta|kumoamisesta)\.?$', title):
        raise R.ReplayError('an amending act whose consolidation has no text: its changes belong to the act it amends')
    return True


def gate(data: dict, final: dict, free) -> list:
    if cons_absent(data):
        return []
    cons = R.parse_act(data['cons'], notes_ok=True)
    cons['scoped'] = final.get('scoped', False)
    A, B = canon(final, free), canon(cons, free)
    bad = sorted(set(A) ^ set(B), key=R.sort_key)
    for k in A:
        if k in B and A[k] != B[k]:
            bad.append(k)
    return bad


def diff_class(data, final, free, bad) -> str:
    """Why the end state differs, from the worst section: 'misprint' (the same lines, at most 3 characters apart in
    all: a misprint or a correction on one side, a source discrepancy), 'wording' (the same lines, larger
    differences: an unrecorded correction, or a change the replay did not see), 'structure' (lines or sections only on
    one side: the replay's placement or grammar, or a change the open data does not carry)."""
    import difflib
    cons = R.parse_act(data['cons'], notes_ok=True)
    cons['scoped'] = final.get('scoped', False)
    A, C = canon(final, free), canon(cons, free)
    worst = 'misprint'
    for k in bad:
        a, c = A.get(k), C.get(k)
        if a is None or c is None or len(a[4]) != len(c[4]) or a[:2] != c[:2]:
            return 'structure'
        x, y = '\n'.join([a[2], a[3], *a[4]]), '\n'.join([c[2], c[3], *c[4]])
        n = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in
                difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes() if op != 'equal')
        if n > 3:
            worst = 'wording'
    return worst


def diff_detail(data, final, free, k):
    cons = R.parse_act(data['cons'], notes_ok=True)
    cons['scoped'] = final.get('scoped', False)
    A, B = canon(final, free), canon(cons, free)
    return {'replayed': A.get(k), 'consolidation': B.get(k)}


# ---- scan ------------------------------------------------------------------------------------------------------------
def scan_one(cfg, offline, today, detail=False, prefetch=False):
    ident = f'{cfg["no"]}/{cfg["year"]}'
    res = {'act': ident, 'ok': False}
    data = None
    try:
        data = load_act(cfg, offline)
        res['amendments'] = len(data['ams'])
        res['title'] = R.txt(data['cons'].find('.//' + N + 'docTitle'))
        events, free = replay_all(data, offline, today)
        bad = gate(data, events[-1][3], free)
        if bad:
            res['reason'] = (f'end state differs from the consolidation in {len(bad)} sections: ' + ', '.join(bad[:8])
                             + f' [{diff_class(data, events[-1][3], free, bad)}]')
            if detail:
                res['detail'] = diff_detail(data, events[-1][3], free, bad[0])
        else:
            res['ok'] = True
            res['sections'] = len(canon(events[-1][3]))
            res['consolidated_on'] = data['consolidated_on']
    except R.ReplayError as e:
        res['reason'] = str(e)
        if prefetch and data is not None:  # read every amending act now, so a later --offline rescan has them all
            for a in data['ams']:
                try:
                    fetch(f'{API}/statute/{a["year"]}/{a["no"]}/fin@', offline)
                except Exception:  # noqa: BLE001
                    pass
    except SystemExit as e:
        res['reason'] = 'not cached: ' + str(e)[:100]
    except Exception as e:  # noqa: BLE001
        res['reason'] = 'error: ' + type(e).__name__ + ': ' + str(e)[:200]
        if detail:
            res['trace'] = traceback.format_exc()
    return res


def heartbeat(line, force=False):
    if _net is not None:
        _net.heartbeat(line, force)


def all_acts():
    acts = json.loads((HERE / 'acts.json').read_text(encoding='utf-8'))
    acts.sort(key=lambda a: (a['year'], int(re.match(r'\d+', a['no']).group()), a['no']))
    return acts


def cmd_scan(args):
    acts = all_acts()
    if args.only:
        sel = []
        for x in args.only:
            m = re.fullmatch(r'([0-9a-z-]+)/(\d{4})(?:@(\w*))?', x)
            hit = [a for a in acts if a['year'] == int(m.group(2)) and a['no'] == m.group(1)]
            sel += hit or [{'year': int(m.group(2)), 'no': m.group(1), 'version': m.group(3) or ''}]
        acts = sel
    out_p = HERE / (args.out or 'scan.json')
    prev = json.loads(out_p.read_text(encoding='utf-8')) if (out_p.exists() and not args.only and args.resume) else {}
    todo = [a for a in acts if f'{a["no"]}/{a["year"]}' not in prev]
    results = dict(prev)
    done = [0]

    def work(a):
        r = scan_one(a, args.offline, args.today, detail=bool(args.only), prefetch=args.prefetch)
        with _lock:
            results[r['act']] = r
            done[0] += 1
            if done[0] % 100 == 0:
                ok = sum(1 for x in results.values() if x['ok'])
                print(f'{done[0]}/{len(todo)} ok {ok} fetched {STATS["fetched"]} '
                      f'{time.time() - STATS["started"]:.0f}s', flush=True)
                if not args.only:
                    if not args.offline:
                            heartbeat(f'scan {done[0]}/{len(todo)} acts, {ok} pass the gate, {STATS["fetched"]} fetched '
                                  f'({time.strftime("%H:%M")})')
                    out_p.write_text(json.dumps(results, ensure_ascii=False, sort_keys=True, indent=0) + '\n',
                                     encoding='utf-8', newline='\n')
        return r
    with Pool(args.workers) as ex:
        ex.map(work, todo, chunksize=1)
    if args.only:
        for r in results.values():
            print(json.dumps(r, ensure_ascii=False, indent=1)[:6000])
        return
    out_p.write_text(json.dumps(results, ensure_ascii=False, sort_keys=True, indent=0) + '\n', encoding='utf-8',
                     newline='\n')
    ok = sum(1 for x in results.values() if x['ok'])
    print('acts', len(results), 'ok', ok)
    if not args.offline:
        heartbeat(f'scan done: {len(results)} acts, {ok} pass the gate ({time.strftime("%H:%M")})', force=True)


# ---- rendering -------------------------------------------------------------------------------------------------------
def fname(k: str) -> str:
    c = None
    if ':' in k:
        c, k = k.split(':')
    m = re.fullmatch(r'(\d+)([a-z]*)', k)
    f = f'pykala-{int(m.group(1)):03d}{m.group(2)}.md'
    if c is not None:
        mc = re.fullmatch(r'(\d+)([a-z]*)', c)
        f = f'luku-{int(mc.group(1)):02d}{mc.group(2)}-' + f
    return f


def render(chapter: dict, sec: dict) -> str:
    out = [f'# {sec["num"]} {sec["heading"]}'.rstrip()]
    if chapter.get('part'):
        out.append(f'Osa: {chapter["part"]}')
    if chapter['num'].strip():
        out.append(f'Luku: {chapter["num"]} {chapter["heading"]}'.rstrip())
    if sec.get('cross'):
        out.append(f'Väliotsikko: {sec["cross"]}')
    for sub in sec['subs']:
        if sub.get('gone'):
            continue  # a repealed subsection: nothing printed (it only keeps the later ones' numbers)
        out.append('')
        out += R.sub_lines(sub)
    return '\n'.join(out) + '\n'


def files_of(act: dict, intro: str) -> dict:
    head, _, rest = intro.partition('\n')
    if act.get('title') and act['title'] != head:  # the act was renamed by an amending act ("nimike")
        intro = act['title'] + '\n' + rest
    d = {'johdanto.md': intro}
    for k, ch, s in R.sections(act):
        d[fname(k)] = render(ch, s)
    return d


def intro_of(base_root) -> str:
    title = R.txt(base_root.find('.//' + N + 'docTitle'))
    pre = base_root.find('.//' + N + 'preamble')
    lines = [title]
    if pre is not None:
        lines += [R.txt(p) for p in pre.iter(N + 'p') if R.txt(p)]
    return '\n'.join(lines) + '\n'


def slug(title: str) -> str:
    s = unicodedata.normalize('NFKD', title.lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def folders(passed):
    """Folder per act: the one-word name of the act ('vaalilaki'), else sd-<year>-<no>; collisions get -<year>-<no>."""
    want = {}
    for cfg, title in passed:
        k = (cfg['year'], cfg['no'])
        if k in NAMED:
            want[k] = NAMED[k][0]
        elif re.fullmatch(r'[A-Za-zÅÄÖåäö]+', title.strip()):
            want[k] = slug(title)
        else:
            want[k] = f'sd-{cfg["year"]}-{cfg["no"]}'
    count = {}
    for v in want.values():
        count[v] = count.get(v, 0) + 1
    return {k: (v if count[v] == 1 or v.startswith('sd-') else f'{v}-{k[0]}-{k[1]}') for k, v in want.items()}


# ---- dates -----------------------------------------------------------------------------------------------------------
def last_sunday(y, m):
    d = dt.date(y, m, 31)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def utc_offset(day: dt.date) -> str:
    """Finland: EET +0200, EEST +0300 from the last Sunday of March to the last Sunday of October (start <= day < end)."""
    if day.year < 1981:  # Finland kept no summer time before 1981
        return '+0200'
    summer = last_sunday(day.year, 3) <= day < last_sunday(day.year, 10)
    return '+0300' if summer else '+0200'


def stamp(day: str, seq: int = 0) -> str:
    """Git date: noon Helsinki on the day, plus seq seconds to keep the order of commits on one day.
    Days before 1970 are clamped to 1970-01-01 (seq seconds apart); In-force carries the real day."""
    d = dt.date.fromisoformat(day)
    if d.year < 1970:
        base = dt.datetime(1970, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
        return str(int(base.timestamp()) + seq) + ' +0000'
    off = utc_offset(d)
    tz = dt.timezone(dt.timedelta(hours=int(off[1:3])))
    base = dt.datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=tz)
    return str(int(base.timestamp()) + seq) + ' ' + off


# ---- messages --------------------------------------------------------------------------------------------------------
def secs_text(keys):
    out = []
    for k in keys:
        if k == 'nimike':
            out.append('nimike')
        elif k == 'voimaantulo':
            out.append('voimaantulosäännös')
        elif k.startswith('luku '):
            out.append(k.replace('luku ', '') + ' luku')
        elif ':' in k:
            c, s = k.split(':')
            out.append(f'{c} luku {s} §')
        else:
            out.append(f'{k} §')
    return out


def amend_message(data, a, prev_keys, new_keys, changed, folder):
    cfg = data['cfg']
    added = [k for k in changed if k not in prev_keys and k in new_keys]
    removed = [k for k in changed if k in prev_keys and k not in new_keys]
    mod = [k for k in changed if k in prev_keys and k in new_keys]
    ident = f'{a["no"]}/{a["year"]}'
    st = secs_text(changed)
    first = ', '.join(st[:6]) + (f' (+{len(st) - 6})' if len(st) > 6 else '')
    lines = [f'{data["short"]}: {first} ({ident})', '', a['title'], '',
             'The replayed intermediate version: Finlex serves only the current consolidation, so this version is '
             'replayed from the amending act; only the end state is checked against an official consolidation.']
    if a.get('expires'):
        lines += ['', f'A temporary amendment: in force until {a["expires"]}.']
    if a.get('dated_from') == 'commencement':
        lines += ['', "The in-force day is read from the act's own commencement provision: Finlex's amendedBy list "
                      'gives none for it.']
    if a.get('dated_from') == 'note':
        lines += ['', "The in-force day is read from Finlex's editorial note in the consolidation (the act took effect "
                      f'by a decree or another act): "{a["date_note"]}"']
    lines.append('')
    if mod:
        lines.append('Muutettu: ' + ', '.join(secs_text(mod)))
    if added:
        lines.append('Lisätty: ' + ', '.join(secs_text(added)))
    if removed:
        lines.append('Kumottu: ' + ', '.join(secs_text(removed)))
    if mod or added or removed:
        lines.append('')  # "Lisätty" is no git trailer key (ä), so these lines stay out of the trailer block
    lines += [f'Evidence: replay of {ident} verified against the consolidation of {data["consolidated_on"]}',
              'Kind: Laki', f'Enacted: {a["enacted"]}' if a.get('enacted') else None,
              f'Published: {a["published"]} ({ident})' if a.get('published') else None,
              f'In-force: {a["dates"][0]}',
              f'Expires: {a["expires"]}' if a.get('expires') else None,
              'Commencement: ' + a['commencement'] if a.get('commencement') else None,
              f'Finlex-note: {a["note"]}' if a['note'] else None]
    lines += [f'Affecting: {cfg["no"]}/{cfg["year"]} {x}' for x in secs_text(changed)]
    lines += [f'Source: {SITE}/fi/lainsaadanto/saadoskokoelma/{a["year"]}/{a["no"]}',
              f'Consolidated: {SITE}/fi/lainsaadanto/{cfg["year"]}/{cfg["no"]}']
    return '\n'.join(x for x in lines if x is not None) + '\n'


def expire_message(data, a, keys, revert):
    cfg = data['cfg']
    ident = f'{a["no"]}/{a["year"]}'
    lines = [f'{data["short"]}: {", ".join(secs_text(keys))} ({ident} expired)', '',
             f'The temporary amendment {ident} was in force until {a["expires"]}; from {revert} these provisions read '
             'as they did before it, as its own commencement provision states.', '',
             f'Evidence: replay of {ident} verified against the consolidation of {data["consolidated_on"]}',
             'Kind: Laki (voimassaolo päättyi)', f'In-force: {revert}', 'Commencement: ' + a['commencement']]
    lines += [f'Affecting: {cfg["no"]}/{cfg["year"]} {x}' for x in secs_text(keys)]
    lines += [f'Source: {SITE}/fi/lainsaadanto/saadoskokoelma/{a["year"]}/{a["no"]}']
    return '\n'.join(lines) + '\n'


def base_message(data, title):
    cfg = data['cfg']
    ident = f'{cfg["no"]}/{cfg["year"]}'
    named = (cfg['year'], cfg['no']) in NAMED  # "Perustuslaki: Suomen perustuslaki (731/1999)"; else the title once
    lines = [f'{data["short"]}: {title} ({ident})' if named else f'{data["short"]} ({ident})', '',
             *([] if named else [title, '']),
             'Alkuperäinen säädös sellaisena kuin se on säädetty ja julkaistu (Finlexin säädöskokoelma).',
             'The statute as enacted: the original text from the Statute Book, before any amendment.', '']
    if not data['ams'] and data['no_cons']:
        lines[-1:] = ['', 'Never amended: Finlex lists no amending act. Its consolidation carries no text, so this '
                          'original is not compared with one.', '']
    elif not data['ams']:
        lines[-1:] = ['', f'Never amended: this text equals the consolidation of {data["consolidated_on"]}.', '']
    if data.get('base_dated_from') == 'commencement':
        lines[-1:] = ['', "The in-force day is read from the act's own commencement provision: Finlex's consolidation "
                          'gives none for it.', '']
    if data.get('base_dated_from') == 'decree':
        lines[-1:] = ['', 'The act took effect by decree; the in-force day is the one its enacting decree names, '
                          f'{data["base_note"]}.', '']
    if data.get('base_dated_from') == 'note':
        lines[-1:] = ['', "The in-force day is read from Finlex's editorial note in the consolidation (the act took "
                          f'effect by a decree): "{data["base_note"]}"', '']
    lines += ['Evidence: original statute text from the Statute Book' +
              (' (the base of the replay)' if data['ams'] else
               ' (never amended; the consolidation has no text)' if data['no_cons'] else
               ', identical to the current consolidation'),
              'Kind: Laki',
              f'Enacted: {data["base_enacted"]}' if data['base_enacted'] else None,
              f'Published: {data["base_published"]} ({ident})' if data['base_published'] else None,
              f'In-force: {data["base_inforce"]}',
              f'Source: {SITE}/fi/lainsaadanto/saadoskokoelma/{cfg["year"]}/{cfg["no"]}',
              f'Consolidated: {SITE}/fi/lainsaadanto/{cfg["year"]}/{cfg["no"]}']
    return '\n'.join(x for x in lines if x is not None) + '\n'


# ---- git -------------------------------------------------------------------------------------------------------------
def git(repo, *args, inp=None):
    r = subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false', *args], cwd=repo,
                       input=inp, capture_output=True)
    if r.returncode:
        raise SystemExit(f'git {" ".join(args)}: {r.stderr.decode("utf-8", "replace")}')
    return r.stdout.decode('utf-8')


def rmtree(p):
    def fix(func, path, exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    shutil.rmtree(p, onexc=fix)


class FastImport:
    def __init__(self, repo):
        self.p = subprocess.Popen(['git', 'fast-import', '--quiet', '--done'], cwd=repo, stdin=subprocess.PIPE)
        self.mark = 0
        self.last = {}

    def w(self, b: bytes):
        self.p.stdin.write(b)

    def commit(self, ref, author, email, when, msg, changes, committer_when=None):
        """changes: [(path, text or None)]. Returns the mark."""
        self.mark += 1
        m = msg.encode('utf-8')
        out = [f'commit {ref}\nmark :{self.mark}\n'.encode(),
               f'author {author} <{email}> {when}\n'.encode('utf-8'),
               f'committer {COMMITTER[0]} <{COMMITTER[1]}> {committer_when or when}\n'.encode('utf-8'),
               f'data {len(m)}\n'.encode(), m, b'\n']
        if ref in self.last:
            out.append(f'from :{self.last[ref]}\n'.encode())
        for path, text in changes:
            if text is None:
                out.append(f'D {path}\n'.encode('utf-8'))
            else:
                b = text.encode('utf-8')
                out.append(f'M 100644 inline {path}\ndata {len(b)}\n'.encode('utf-8') + b + b'\n')
        out.append(b'\n')
        self.w(b''.join(out))
        self.last[ref] = self.mark
        return self.mark

    def branch_from(self, ref, mark):
        self.w(f'reset {ref}\nfrom :{mark}\n\n'.encode())
        self.last[ref] = mark

    def tag(self, name, mark):
        self.w(f'reset refs/tags/{name}\nfrom :{mark}\n\n'.encode('utf-8'))

    def close(self):
        self.w(b'done\n')
        self.p.stdin.close()
        if self.p.wait():
            raise SystemExit('fast-import failed')


def prepare(cfg, offline, today):
    data = load_act(cfg, offline)
    events, free = replay_all(data, offline, today)
    bad = gate(data, events[-1][3], free)
    if bad:
        raise R.ReplayError(f'gate failed: {bad[:5]}')
    data['events'] = events
    data['free'] = free
    data['title'] = R.txt(data['cons'].find('.//' + N + 'docTitle'))
    data['intro'] = intro_of(data['base'])
    data['n_sections'] = len(R.sections_keys(events[-1][3]))
    data['no_cons'] = cons_absent(data)
    # drop the heavy XML trees no longer needed
    data['cons'] = data['base'] = None
    for a in data['ams']:
        a.pop('root', None)
        a.pop('act', None)
    return data


def cmd_build(args):
    today = args.today
    scan = json.loads((HERE / (args.scan or 'scan.json')).read_text(encoding='utf-8'))
    acts = all_acts()
    passed = [a for a in acts if scan.get(f'{a["no"]}/{a["year"]}', {}).get('ok')]
    if args.limit:
        passed = passed[:args.limit]
    print('acts passing the gate:', len(passed), flush=True)
    with Pool(args.workers) as ex:
        datas = ex.map(lambda c: prepare(c, args.offline, today), passed, chunksize=1)
    fold = folders([(d['cfg'], d['title']) for d in datas])
    for d in datas:
        k = (d['cfg']['year'], d['cfg']['no'])
        d['folder'] = fold[k]
        d['short'] = NAMED[k][1] if k in NAMED else (d['title'] if len(d['title']) <= 60 else d['title'][:57] + '...')
    # events across acts, in in-force order
    ev = []
    for i, d in enumerate(datas):
        for e_i, (day, kind, j, state, keys) in enumerate(d['events']):
            a = d['ams'][j] if j >= 0 else None
            order = 0 if kind == 'base' else 1
            ident = (d['cfg']['year'], int(re.match(r'\d+', d['cfg']['no']).group())) if a is None else \
                (a['year'], int(re.match(r'\d+', a['no']).group()))
            ev.append((day, order, ident, d['cfg']['year'], int(re.match(r'\d+', d['cfg']['no']).group()),
                       kind, i, e_i))
    ev.sort()
    repo = Path(args.repo)
    if repo.exists():
        rmtree(repo)
    repo.mkdir(parents=True)
    git(repo, 'init', '-q', '-b', 'main')
    fi = FastImport(repo)
    cur = {}  # folder -> {name: text}
    seq_day, seq, clamp = None, 0, 0
    main_mark = None
    future_started = False
    tags = set()
    for day, _o, _id, _y, _n, kind, i, e_i in ev:
        d = datas[i]
        _day, _kind, j, state, keys = d['events'][e_i]
        files = files_of(state, d['intro'])
        old = cur.get(d['folder'], {})
        changes = [(f'{d["folder"]}/{n}', None) for n in sorted(set(old) - set(files))]
        changes += [(f'{d["folder"]}/{n}', files[n]) for n in sorted(files) if old.get(n) != files[n]]
        cur[d['folder']] = files
        if day < '1970-01-01':
            when = stamp(day, clamp)
            clamp += 1
        else:
            if day != seq_day:
                seq_day, seq = day, 0
            when = stamp(day, min(seq, 59))  # stays within 12:00; later commits of a busy day share 12:00:59
            seq += 1
        cfg = d['cfg']
        if kind == 'base':
            y, no = cfg['year'], cfg['no']
            msg = base_message(d, d['title'])
            tag = f'{d["folder"]}/{y}-{no}'
        else:
            a = d['ams'][j]
            y, no = a['year'], a['no']
            prev_state = d['events'][e_i - 1][3]
            if kind == 'amend':
                msg = amend_message(d, a, set(R.sections_keys(prev_state)), set(R.sections_keys(state)), keys,
                                    d['folder'])
                tag = f'{d["folder"]}/{y}-{no}'
            else:
                msg = expire_message(d, a, keys, day)
                tag = f'{d["folder"]}/{y}-{no}-expired'
        ref = 'refs/heads/main'
        if day > today:
            if not future_started:
                fi.branch_from('refs/heads/future', main_mark)
                future_started = True
            ref = 'refs/heads/future'
        elif future_started:
            raise SystemExit('a past event after a future one')
        mark = fi.commit(ref, f'{no}/{y}', f'sd-{y}-{no}@finlex.invalid', when, msg, changes)
        if ref.endswith('main'):
            main_mark = mark
        if tag in tags:
            raise SystemExit('duplicate tag ' + tag)
        tags.add(tag)
        fi.tag(tag, mark)
    fi.close()
    print('law commits:', len(ev), 'acts:', len(datas), flush=True)
    report = {f'{d["cfg"]["no"]}/{d["cfg"]["year"]}': {
        'folder': d['folder'], 'amendments': len(d['ams']), 'sections': len(canon(d['events'][-1][3])),
        'consolidated_on': d['consolidated_on']} for d in datas}
    (HERE / 'gate.json').write_text(json.dumps({'normalisation': NORMALISATION, 'acts': report}, indent=1,
                                               ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8',
                                    newline='\n')
    git(repo, 'checkout', '-q', '-f', 'main')
    (repo / '.gitattributes').write_text('* text eol=lf\n', encoding='utf-8', newline='\n')
    import importlib
    mod = importlib.import_module('reproduce')
    mod.write_docs(repo, datas, scan, today, HERE)
    git(repo, 'add', '-A')
    when = stamp(today, 0)
    env_msg = ('Documentation, tools and REPRODUCE.md\n\nNo law text changes. The tools, README, LICENSE, corpus.json, the '
               'tables of acts, amending acts and acts left out, and REPRODUCE.md.\n\nKind: documentation\n'
               'In-force: ' + today + '\n')
    tree = git(repo, 'write-tree').strip()
    parent = git(repo, 'rev-parse', 'HEAD').strip()
    env = dict(os.environ, GIT_AUTHOR_NAME=COMMITTER[0], GIT_AUTHOR_EMAIL=COMMITTER[1], GIT_AUTHOR_DATE=when,
               GIT_COMMITTER_NAME=COMMITTER[0], GIT_COMMITTER_EMAIL=COMMITTER[1], GIT_COMMITTER_DATE=when)
    r = subprocess.run(['git', 'commit-tree', tree, '-p', parent], cwd=repo, input=env_msg.encode('utf-8'),
                       capture_output=True, env=env)
    if r.returncode:
        raise SystemExit(r.stderr.decode())
    c = r.stdout.decode().strip()
    git(repo, 'update-ref', 'refs/heads/main', c)
    git(repo, 'reset', '-q', '--hard', 'main')
    print('main', c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['scan', 'build'])
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'finland-law-build'))
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--today', default='2026-10-28')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--only', nargs='*')
    ap.add_argument('--out')
    ap.add_argument('--scan')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--prefetch', action='store_true')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    if args.cmd == 'scan':
        cmd_scan(args)
    else:
        cmd_build(args)


if __name__ == '__main__':
    main()
