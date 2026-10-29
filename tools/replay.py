"""Finlex (Akoma Ntoso) parsing and deterministic replay of amending acts onto a base statute.

No AI, no fuzzy edits. The amending act's enacting clause (the "muutetaan ... / lisätään ... / kumotaan ..." sentence)
names every provision it changes; the body gives the new text in the same order, with omission markers for the parts
left alone. Replay understands:
  - whole sections ("23 §", "14–17 §"), headings ("53 §:n otsikko"), cross headings ("12 §:n edellä oleva
    väliotsikko"), subsections ("1 §:n 3 momentti"), a subsection's introductory words ("2 momentin
    johdantokappale"), points ("5 §:n 7 kohta", "9 §:n 1 momentin 1–3 kohta"), the Finnish wording of a provision
    ("2 §:n 3 momentin suomenkielinen sanamuoto"), whole chapters ("3 luku", "3 luvun otsikko");
  - insertion of sections ("lakiin uusi 48 a §"), subsections ("9 §:ään uusi 4 momentti"), points ("79 §:n 1
    momenttiin uusi 8 kohta"), chapters ("uusi 5 a luku");
  - repeal of sections, subsections, points and chapters;
  - acts whose sections are numbered per chapter ("3 luvun 5 §").
The "sellaisena kuin se on laissa ..." provenance phrases are skipped. Anything the grammar does not recognise raises
ReplayError; nothing is guessed. The end state is then compared with the official consolidation (build.py).
Standard library only.
"""
import copy
import re

N = '{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}'
WS = re.compile(r'\s+')


class ReplayError(Exception):
    pass


# Finlex's consolidation keeps a marker where a provision was repealed: "3 momentti on kumottu L:lla 29.8.2008/562.",
# "4 § on kumottu L:lla 10.8.2018/596.", "Väliotsikko on kumottu L:lla ...". It is an editorial note, not law text:
# such elements are dropped when parsing (the replay removes the provision itself).
PLACEHOLDER = re.compile(r'^(?:[0-9]+ ?[a-z]? (?:§|mom[a-z]*\.?|kohta|luku|alakohta)|Väliotsikko|Otsikko) on kumottu '
                         r'[A-Za-zÄÖäö]*:lla \d{1,2}\.\d{1,2}\.\d{4}/\d+'
                         r'(?:, joka tulee voimaan \d{1,2}\.\d{1,2}\.\d{4}\. Aiempi sanamuoto kuuluu:|\.?)$')


def is_placeholder(e) -> bool:
    return bool(PLACEHOLDER.match(txt(e)))


def tag(e) -> str:
    return e.tag.replace(N, '')


def _text(e, out):
    if e.text:
        out.append(e.text)
    for c in e:
        if c.tag != N + 'authorialNote':  # Finlex's correction note ("Merkitty kohta oikaistu ..."): not text
            _text(c, out)
        if c.tail:
            out.append(c.tail)


def txt(e) -> str:
    out = []
    _text(e, out)
    return WS.sub(' ', ''.join(out)).strip()


def key(num: str) -> str:
    """'35 §' -> '35', '2 a §' -> '2a'."""
    m = re.fullmatch(r'(\d+)\s*([a-z]?)\s*§\.?', num.strip())
    if not m:
        raise ReplayError(f'bad section number {num!r}')
    return m.group(1) + m.group(2)


def chap_key(num: str) -> str:
    """'3 luku' -> '3', '5 a luku' -> '5a'."""
    m = re.match(r'(\d+)\s*([a-z]?)\b', num.strip())
    return (m.group(1) + m.group(2)) if m else num.strip()


def point_key(num: str) -> str:
    return re.sub(r'[\s).]', '', num)


def sort_key(k: str):
    m = re.fullmatch(r'(?:(\d+)([a-z]?):)?(\d+)([a-z]?)', k)
    if not m:
        return (0, '', 0, k)
    return (int(m.group(1) or 0), m.group(2) or '', int(m.group(3)), m.group(4))


# ---- structure ---------------------------------------------------------------------------------------------------
TEXT_SKIP = ('num', 'heading')


OMIT = '\x00omission'
SUBPOINT = re.compile(r'([a-zåäö])\) ')


def split_point(lines):
    """A point's lines -> (its own lines, [[sub-point letter, lines]]). A sub-point starts with "a) "."""
    head, subs = [], []
    for i, x in enumerate(lines):
        m = SUBPOINT.match(x) if i else None
        if m:
            subs.append([m.group(1), [x]])
        elif x == OMIT:
            subs.append([None, [x]]) if subs else head.append(x)
        elif subs:
            subs[-1][1].append(x)
        else:
            head.append(x)
    return head, subs


def strip_tail_omit(lines):
    """An omission printed as a point's last child, when the point is named whole: it elides what follows the point."""
    lines = list(lines)
    while lines and lines[-1] == OMIT:
        lines.pop()
    return lines


def join_point(head, subs):
    return list(head) + [x for _k, ls in subs for x in ls]


def table_lines(t) -> list:
    """A table, one line per row, cells joined by ' | '."""
    out = []
    for tr in t.iter(N + 'tr'):
        cells = [txt(c) for c in tr if tag(c) in ('td', 'th')]
        if any(cells):
            out.append(' | '.join(cells))
    return out


def block_lines(e) -> list:
    """Lines of a point (kohta) or nested block: its own text, then nested points, one line each."""
    num = e.find(N + 'num')
    head = (txt(num) + ' ') if num is not None else ''
    own, nested, post = [], [], []
    for c in e:
        t = tag(c)
        if t == 'content':
            own += [txt(p) for p in c if tag(p) == 'p' and txt(p)]
            for p in c:
                if tag(p) in ('paragraph', 'list', 'blockList', 'point'):
                    nested += block_lines(p)
                elif tag(p) == 'table':
                    nested += table_lines(p)
                elif tag(p) != 'p':
                    raise ReplayError(f'unknown content child {tag(p)}')
        elif t == 'intro':
            own += [txt(p) for p in c if txt(p)] or ([txt(c)] if txt(c) else [])
        elif t in ('paragraph', 'point', 'subparagraph', 'list'):
            nested += block_lines(c)
        elif t == 'wrapUp':
            post += [txt(p) for p in c if txt(p)] or ([txt(c)] if txt(c) else [])
        elif t in TEXT_SKIP:
            pass
        elif t == 'hcontainer' and c.get('name') == 'omission':
            nested.append(OMIT)  # only sub-point operations accept a point with omissions
        elif t == 'hcontainer' and c.get('name') == 'noteAuthorial':
            pass  # Finlex's editorial note inside a point (consolidations): not text
        else:
            raise ReplayError(f'unknown child {t}')
    if own:
        own[0] = head + own[0]
    elif head.strip():
        own = [head.strip()]
    return own + nested + post


def parse_sub(e) -> dict:
    """A subsection (momentti): {'pre': lines, 'points': [[key, lines]], 'post': lines, 'omits': bool}."""
    d = {'pre': [], 'points': [], 'post': [], 'omits': False}
    for c in e:
        t = tag(c)
        if t == 'content':
            for p in c:
                if tag(p) == 'p' and is_placeholder(p):
                    d['gone'] = True
                elif tag(p) == 'p':
                    if txt(p):
                        (d['post'] if d['points'] else d['pre']).append(txt(p))
                elif tag(p) == 'table':
                    (d['post'] if d['points'] else d['pre']).extend(table_lines(p))
                elif tag(p) in ('paragraph', 'point', 'list', 'blockList'):
                    if is_placeholder(p):
                        continue
                    n = p.find(N + 'num')
                    d['points'].append([point_key(txt(n)) if n is not None else '', block_lines(p)])
                else:
                    raise ReplayError(f'unknown content child {tag(p)}')
        elif t == 'intro':
            d['pre'] += [txt(p) for p in c if txt(p)] or ([txt(c)] if txt(c) else [])
        elif t in ('paragraph', 'point'):
            if is_placeholder(c):
                continue
            n = c.find(N + 'num')
            d['points'].append([point_key(txt(n)) if n is not None else '', block_lines(c)])
        elif t == 'wrapUp':
            d['post'] += [txt(p) for p in c if txt(p)] or ([txt(c)] if txt(c) else [])
        elif t == 'hcontainer' and c.get('name') == 'omission':
            d['omits'] = True
        elif t == 'hcontainer' and c.get('name') == 'noteAuthorial':
            pass  # Finlex's editorial note inside a subsection (consolidations): not text
        elif t in TEXT_SKIP:
            pass
        else:
            raise ReplayError(f'unknown subsection child {t}')
    nested = []
    for pk, ls in d['points']:
        if nested and re.fullmatch(r'[a-zåäö]', pk) and re.fullmatch(r'\d+[a-z]?', nested[-1][0]):
            nested[-1][1] = nested[-1][1] + ls  # "2) ...: | b) ...": a sub-point printed beside its point
        else:
            nested.append([pk, ls])
    d['points'] = nested
    kids = [c for c in e if tag(c) not in TEXT_SKIP]
    # an omission printed as the subsection's last child (after its text): the source's way of eliding the
    # momentti that FOLLOW it, when the clause names this momentti whole
    omissions = [c for c in kids if tag(c) == 'hcontainer' and c.get('name') == 'omission']
    d['omit_tail'] = len(omissions) == 1 and kids[-1] is omissions[0]
    return d


def gone_sub() -> dict:
    """A repealed subsection keeps its place: later ones keep their numbers (as Finlex numbers them)."""
    return {'pre': [], 'points': [], 'post': [], 'omits': False, 'gone': True}


def sub_lines(s: dict) -> list:
    out = list(s['pre'])
    for _k, ls in s['points']:
        out += ls
    return out + list(s['post'])


def parse_section(sec, notes_ok=False) -> dict:
    if sec.find(N + 'num') is None:
        raise ReplayError('a section without a number (source markup)')
    d = {'num': txt(sec.find(N + 'num')), 'heading': '', 'cross': ''}
    h = sec.find(N + 'heading')
    if h is not None:
        d['heading'] = txt(h)
    seq = []
    for c in sec:
        t = tag(c)
        if t == 'subsection':
            sub = parse_sub(c)
            if sub.get('gone') and not sub_lines(sub):
                sub = gone_sub()
            else:
                sub.pop('gone', None)  # a marker beside real text (a repealed point): the subsection stays
            seq.append(sub)
        elif t == 'content' and is_placeholder(c):
            d['placeholder'] = True
        elif t == 'content':  # a section printed without subsection markup
            pre = []
            for p in c:
                pre += table_lines(p) if tag(p) == 'table' else ([txt(p)] if txt(p) else [])
            seq.append({'pre': pre, 'points': [], 'post': [], 'omits': False})
        elif t == 'hcontainer' and c.get('name') == 'omission':
            seq.append(None)
        elif t == 'hcontainer' and c.get('name') == 'noteAuthorial' and notes_ok:
            pass
        elif t in TEXT_SKIP:
            pass
        else:
            raise ReplayError(f'unknown section child {t} {c.get("name") or ""} in {d["num"]}')
    merged = []
    for sub in seq:
        prev = merged[-1] if merged else None
        if (sub is not None and prev is not None and not prev.get('gone') and prev['points'] and not prev['post']
                and sub['pre'] and not sub['points'] and re.match(r'[a-zåäö]', sub['pre'][0])):
            # "Joka ... 1) ...; 27) ..., | on tuomittava ...": the closing words of a list printed as a subsection of
            # their own (they continue the sentence) belong to the momentti of the list
            prev['post'] = list(sub['pre']) + list(sub['post'])
            prev['omits'] = prev['omits'] or sub['omits']
            continue
        pm = re.match(r'(\d+ ?[a-z]?)\) ', sub['pre'][0]) if (sub is not None and sub['pre'] and not sub['points']
                                                             and not sub['post'] and len(sub['pre']) == 1) else None
        if (pm and prev is not None and not prev.get('gone') and not prev['post'] and not sub['omits']
                and (prev['points'] or (prev['pre'] and prev['pre'][-1].endswith(':')))):
            # "...: | 1) ... | 2) ...": points printed as subsections of their own belong to the momentti they list
            prev['points'].append([point_key(pm.group(1)), list(sub['pre'])])
            continue
        merged.append(sub)
    seq = merged
    while seq and seq[-1] is not None and seq[-1].get('gone'):
        seq.pop()  # a repealed last subsection leaves nothing behind
    d['seq'] = seq
    d['subs'] = [s for s in seq if s is not None]
    d['omits'] = any(s is None or s['omits'] for s in seq)
    return d


EID_VERSION = re.compile(r'v(\d{8})$')


def latest_versions_only(root):
    """A consolidation that already holds an amendment not yet in force prints the new provision (eId ...v20260378)
    and, after a note "... tulee voimaan 1.1.2027. Aiempi sanamuoto kuuluu:", the earlier wording under the same eId
    without that version. The replay's end state includes every amendment, so only the latest version of each
    provision is compared: among siblings with the same eId (version suffix removed), the highest version is kept."""
    for parent in list(root.iter()):
        groups = {}
        for c in parent:
            eid = c.get('eId') if isinstance(c.tag, str) else None
            if eid:
                m = EID_VERSION.search(eid)
                groups.setdefault(EID_VERSION.sub('', eid), []).append((int(m.group(1)) if m else 0, c))
        for _base, items in groups.items():
            if len(items) > 1:
                keep = max(v for v, _c in items)
                for v, c in items:
                    if v != keep:
                        parent.remove(c)


def parse_act(root, notes_ok=False) -> dict:
    """{'title', 'enacting_blocks', 'chapters': [{'num','heading','part','sections':[...]}]}"""
    if notes_ok:
        latest_versions_only(root)
    act = {'title': '', 'chapters': [], 'enacting_blocks': []}
    dt = root.find('.//' + N + 'docTitle')
    act['title'] = txt(dt) if dt is not None else ''
    pre = root.find('.//' + N + 'preamble')
    if pre is not None:
        act['enacting_blocks'] = [(b.get('name'), txt(b)) for b in pre.iter(N + 'block')]
    body = root.find('.//' + N + 'body')
    if body is None:
        raise ReplayError('no body (no text in the open data)')
    wraps = [h for h in body if h.get('name') == 'statuteProvisionsWrapper']
    if not wraps:
        if any(h.get('name') == 'statuteTextWrapper' for h in body):
            act['text_only'] = True  # an act whose text is no sections (e.g. it amends a commencement provision)
            return act
        raise ReplayError('no statuteProvisionsWrapper')
    state = {'cur': None, 'cross': '', 'part': ''}

    def add_section(s, ch):
        sec = parse_section(s, notes_ok)
        if sec.get('placeholder'):
            if sec['subs'] or sec['heading']:
                raise ReplayError(f'repeal marker in a section with text: {sec["num"]}')
            return  # a cross heading printed before it heads the next section
        sec['cross'], state['cross'] = state['cross'], ''
        ch['sections'].append(sec)

    def chapter(c, part):
        n, h = c.find(N + 'num'), c.find(N + 'heading')
        ch = {'num': txt(n) if n is not None else '', 'heading': txt(h) if h is not None else '', 'part': part,
              'sections': []}
        gone = False
        for s in c:
            t = tag(s)
            if t == 'section':
                add_section(s, ch)
            elif t == 'crossHeading':
                state['cross'] = '' if is_placeholder(s) else txt(s)
            elif t == 'hcontainer' and s.get('name') == 'noteAuthorial' and notes_ok:
                pass
            elif t == 'content' and is_placeholder(s):
                gone = True
            elif t not in ('num', 'heading'):
                raise ReplayError('unknown chapter child ' + t)
        if gone:
            if ch['sections']:
                raise ReplayError('repeal marker in a chapter with sections')
            state['cur'] = None
            return
        act['chapters'].append(ch)
        state['cur'] = None

    def loose(c, part):
        if state['cur'] is None:
            state['cur'] = {'num': '', 'heading': '', 'part': part, 'sections': []}
            act['chapters'].append(state['cur'])
        add_section(c, state['cur'])

    def walk(parent, part):
        for c in parent:
            t = tag(c)
            if t == 'chapter':
                chapter(c, part)
            elif t == 'section':
                loose(c, part)
            elif t == 'crossHeading':
                state['cross'] = '' if is_placeholder(c) else txt(c)
            elif t == 'level' and c.find('.//' + N + 'docTitle') is not None and parent is wraps[0] and \
                    not any(tag(x) == 'section' for x in c.iter()):
                act['new_title'] = txt(c.find('.//' + N + 'docTitle'))  # an amending act's new name of the act
            elif t == 'part':
                n, h = c.find(N + 'num'), c.find(N + 'heading')
                label = ' '.join(x for x in [txt(n) if n is not None else '', txt(h) if h is not None else ''] if x)
                state['cur'] = None
                walk(c, label)
                state['cur'] = None
            elif t in ('num', 'heading'):
                pass
            elif t == 'hcontainer' and c.get('name') == 'noteAuthorial' and notes_ok:
                pass
            else:
                raise ReplayError('unknown body child ' + t + ' ' + (c.get('name') or ''))
    walk(wraps[0], '')
    for extra in wraps[1:]:
        walk(extra, '')
    # "11 § Voimaantulo" printed as a heading only, its text in the entryIntoForce container after the provisions
    # (the consolidation prints the text in the section)
    last = act['chapters'][-1]['sections'][-1] if act['chapters'] and act['chapters'][-1]['sections'] else None
    eif = [h for h in body if h.get('name') == 'entryIntoForce']
    if last is not None and not last['subs'] and last['heading'] and len(eif) == 1:
        paras = [txt(p) for p in eif[0].iter(N + 'p') if txt(p)]
        if paras:
            last['subs'] = [{'pre': [x], 'points': [], 'post': [], 'omits': False} for x in paras]
            last['seq'] = list(last['subs'])
    nums = [key(s['num']) for ch in act['chapters'] for s in ch['sections']]
    act['scoped'] = len(nums) != len(set(nums))
    return act


def skey(act, ch, s) -> str:
    return (chap_key(ch['num']) + ':' + key(s['num'])) if act.get('scoped') else key(s['num'])


def sections(act):
    for ch in act['chapters']:
        for s in ch['sections']:
            yield skey(act, ch, s), ch, s


# ---- enacting clause grammar -------------------------------------------------------------------------------------
TOK = re.compile(r'\d+/\d{4}|\d+/\d{2}(?!\d)|\d+(?: ?[a-zåäö](?![a-zåäö]))?|§|:[a-zåäö]+|[a-zåäöA-ZÅÄÖ]+|,|–')
REF = re.compile(r'\d+/(?:\d{4}|\d{2})$')
NUMT = re.compile(r'\d+(?: ?[a-zåäö])?$')
DATE_PHRASE = re.compile(r'\d+ päivänä [a-zäö]+ \d{4}')
MOVES = re.compile(r',? jolloin(?:[^,]*?\w+ksi)(?:\s+ja\s+[^,]*?\w+ksi)*')
LAW_WORDS = {'laissa', 'laeissa', 'lailla', 'laeilla', 'asetuksessa', 'asetuksissa', 'asetuksella'}
STOP_WORDS = {'liite', 'liitteen', 'liitteet', 'liitteeseen', 'liitteitä', 'liitteessä', 'liitettä', 'liitteiden'}


def nk(tok):
    return tok.replace(' ', '')


def expand(a, b):
    ma, mb = re.fullmatch(r'(\d+)([a-zåäö]?)', a), re.fullmatch(r'(\d+)([a-zåäö]?)', b)
    if (ma.group(1) == mb.group(1) and mb.group(2) and ma.group(2) < mb.group(2)
            and mb.group(2) in 'abcdefghijklmnopqrstuvwxyz'):  # "8 a–8 c" -> 8b, 8c
        return [ma.group(1) + chr(c) for c in range(ord(ma.group(2) or 'a') + (1 if ma.group(2) else 0),
                                                      ord(mb.group(2)) + 1)]
    if ma.group(2) or mb.group(2) or int(mb.group(1)) < int(ma.group(1)):
        raise ReplayError(f'range {a}–{b} not understood')
    return [str(i) for i in range(int(ma.group(1)) + 1, int(mb.group(1)) + 1)]


def clause_tokens(text):
    t = text.replace('\xa0', ' ')
    t = re.sub('[―—–‒]', '–', t)
    t = re.sub(r'(?<=\d)-(?=\d)', '–', t)
    t = DATE_PHRASE.sub(' ', t)
    t = MOVES.sub('', t)
    t = re.sub(r'\(\s*(\d+/\d{2,4})\s*\)', ' ', t)
    return TOK.findall(t)


def parse_clause(text, mode, scoped):
    """Ops from one enacting block. mode: 'sub' (muutetaan), 'ins' (lisätään), 'rep' (kumotaan)."""
    toks = clause_tokens(text)
    ops = []
    nums, secs, mom, chap, dash = [], None, None, None, False
    since_secs = 0
    i = 0
    pref = {'sub': '', 'ins': 'ins_', 'rep': 'rep_'}[mode]

    def emit(kind, *a):
        nonlocal since_secs
        ops.append((pref + kind,) + a)
        since_secs += 1

    def sk(n):
        return (chap + ':' + n) if (scoped and chap) else n

    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ''
        tl = t.lower()
        if REF.match(t):
            pass  # an act reference outside a provenance phrase (e.g. in the act's name): ignored
        elif NUMT.match(t):
            if dash:
                nums += expand(nk(nums[-1]), nk(t))
                dash = False
            else:
                nums.append(nk(t))
        elif t == '–':
            if not nums:
                raise ReplayError('dash without a number')
            dash = True
        elif t == '§':
            S, nums = nums, []
            if not S:
                raise ReplayError('§ without a number')
            if nxt.startswith(':'):
                secs, mom, since_secs = [sk(s) for s in S], None, 0
                i += 1
            else:
                for s in S:
                    emit('sec', sk(s))
                secs, mom = None, None
        elif tl in ('luvun', 'lukuun', 'luvussa'):
            C, nums = nums, []
            if not C and chap and tl in ('luvun', 'lukuun'):
                C = [chap]  # "9 luvun 2 §:n 1 momentti ja luvun ... otsikko": the same chapter
            if len(C) != 1:
                raise ReplayError('chapter reference not understood')
            chap = C[0]
            if nxt == 'otsikko':
                emit('chapter_heading', chap)
                i += 1
            elif nxt in ('väliotsikko',):
                raise ReplayError('chapter cross heading')
            secs = None
        elif tl in ('luku', 'luvut'):
            for c in nums:
                emit('chapter', c)
            nums = []
        elif tl == 'otsikko':
            if not secs or nums:
                raise ReplayError('otsikko without a section')
            for s in secs:
                emit('heading', s)
        elif tl in ('väliotsikko', 'väliotsikot'):
            near = [x.lower() for x in toks[max(0, i - 3):i]]
            if (not secs and not nums and ops and ops[-1][0] == pref + 'sec'
                    and ('edelle' in near or 'edellä' in near)):
                if pref == 'rep_':  # "kumotaan 6 § sekä sen edellä oleva väliotsikko": both go
                    ops.append(('rep_cross', ops[-1][1]))
                # "uusi 8 a § sekä sen edelle uusi väliotsikko": the cross heading comes with the whole section
            elif not secs or nums:
                raise ReplayError('väliotsikko without a section')
            else:
                for s in secs:
                    emit('cross', s)
        elif tl in ('momentti', 'momentit', 'momentiksi'):
            if not secs or not nums or len(secs) != 1:
                raise ReplayError('momentti without a section')
            for m in nums:
                emit('mom', secs[0], int(re.match(r'\d+', m).group()))
            nums, mom = [], None
        elif tl in ('momentin', 'momenttiin', 'momentista'):
            if not secs or not nums or len(secs) != 1:
                raise ReplayError('momentin without a section')
            mom, nums = [int(re.match(r'\d+', m).group()) for m in nums], []
            nn = toks[i + 2] if i + 2 < len(toks) else ''
            if tl == 'momentin' and (nxt in ('ja', 'sekä', 'suomenkielinen', '', 'seuraavasti')
                                     or (nxt == ',' and not nn.startswith('sellais'))):
                for m in mom:
                    emit('mom', secs[0], m)
                mom = None
        elif tl in ('kohta', 'kohdat'):
            if not secs or not nums or len(secs) != 1:
                raise ReplayError('kohta without a section')
            if mom and len(mom) != 1:
                raise ReplayError('kohta of several momentti')
            m = mom[0] if mom else 1
            for k in nums:
                emit('kohta', secs[0], m, k)
            nums = []
        elif tl in ('kohdan', 'kohtaan') and nums and len(nums) == 1 and secs and len(secs) == 1 and \
                not (mom and len(mom) != 1):
            pk, nums, m = nums[0], [], (mom[0] if mom else 1)
            nx = [x.lower() for x in toks[i + 1:i + 4]]
            if nx[:1] == ['tilalle']:  # "kumotun 4 kohdan tilalle uusi 4 kohta": the new point follows
                i += 1
            elif nx[:1] == ['johdantokappale']:
                emit('kohta_intro', secs[0], m, pk)
                i += 1
            elif nx[:1] == ['suomenkielinen']:  # the Finnish wording of the point: the point as the body prints it
                emit('kohta', secs[0], m, pk)
                i += 2 if nx[1:2] == ['sanamuoto'] else 1
            else:
                j, letters = i + 1, []
                while j < len(toks) and (re.fullmatch(r'[a-zåäö]', toks[j]) or toks[j] in (',', 'ja', 'sekä', 'uusi',
                                                                                        'uudet', '–')):
                    if re.fullmatch(r'[a-zåäö]', toks[j]):
                        if letters and toks[j - 1] == '–':
                            letters += [chr(c) for c in range(ord(letters[-1]) + 1, ord(toks[j]) + 1)]
                        else:
                            letters.append(toks[j])
                    j += 1
                if not letters or j >= len(toks) or toks[j].lower() not in ('alakohta', 'alakohdat'):
                    raise ReplayError('sub-point (alakohta / kohdan ...) not supported')
                for lt in letters:
                    emit('alakohta', secs[0], m, pk, lt)
                i = j
        elif tl in ('kohdan', 'kohtaan', 'alakohta', 'alakohdan', 'alakohdat', 'alakohtaan'):
            raise ReplayError('sub-point (alakohta / kohdan ...) not supported')
        elif tl == 'johdantokappale':
            if not secs or nums or len(secs) != 1:
                raise ReplayError('johdantokappale without a section')
            if mom and len(mom) != 1:
                raise ReplayError('johdantokappale of several momentti')
            emit('intro', secs[0], mom[0] if mom else 1)
            # mom stays: "2 momentin johdantokappale ja 1 ja 3 kohta" are points of the same momentti
        elif tl == 'suomenkielinen':
            if nxt == 'sanamuoto':
                i += 1
            if secs and since_secs == 0 and not nums and mom is None:
                for s in secs:
                    emit('sec', s)
        elif tl == 'ruotsinkielinen' and nxt == 'otsikko' and not nums and secs is None:
            i += 1  # "luvun ruotsinkielinen otsikko": the Swedish title only; nothing changes in the Finnish text
        elif tl == 'ruotsinkielinen':
            # "2 §:n ruotsinkielinen sanamuoto", "8 §:n 2 momentin ruotsinkielinen sanamuoto", "74 §:n otsikon
            # ruotsinkielinen sanamuoto": only the Swedish text changes; the Finnish text stays as it is
            if nums or not secs or len(secs) != 1:
                raise ReplayError('Swedish-wording-only change')
            emit('swe', secs[0])
            if nxt in ('sanamuoto', 'asu'):
                i += 1
            secs, mom = None, None
        elif tl.startswith('voimaantulosäännö'):
            # "lain voimaantulosäännöksen 2 momentti", "lain voimaantulosäännös": the act's commencement provision
            if nums:
                raise ReplayError('numbers before a commencement provision')
            if nxt and NUMT.match(nxt):
                secs, mom, since_secs = ['voimaantulo'], None, 0
            else:
                emit('sec', 'voimaantulo')
        elif tl in ('sellaisena', 'sellaisina'):
            # The phrase ends at its act reference ("laissa 526/1988", "laissa (281/83)"), or, when it names the
            # act only as "mainitussa laissa" / "annetussa laissa" without a number, at that word.
            plural = tl == 'sellaisina'
            j = i + 1
            stop = next((k for k in range(j, len(toks)) if toks[k].lower() == 'seuraavasti'), len(toks))
            ends = [k for k in range(j, stop) if REF.match(toks[k])
                    or (toks[k].lower() in LAW_WORDS and not (k + 1 < len(toks) and REF.match(toks[k + 1])))]
            if not ends:
                raise ReplayError('provenance phrase without an act reference')
            if plural and nxt == 'kuin' and i + 2 < len(toks) and toks[i + 2].lower() == 'niistä':
                end = ends[-1]  # "sellaisina kuin niistä ovat 3 § laissa .. ja 5 § laissa ..": lists provisions
            else:
                # "laissa 1/2000 ja 2/2001", "laissa (1/00) ja mainitussa laissa": further ends with no provision
                # number between them belong to the same phrase
                end = ends[0]
                for e2 in ends[1:]:
                    between = toks[end + 1:e2]
                    if any(NUMT.match(x) or x == '§' for x in between):
                        break
                    end = e2
            i = end
        elif tl == 'nimike' and not nums and secs is None:
            emit('title')  # the act's name ("lain nimike")
        elif tl == 'seuraavasti':
            break
        elif tl in STOP_WORDS:
            raise ReplayError('annex (liite) not supported')
        elif t in (',', 'ja', 'sekä', 'uusi', 'uudet', 'uudeksi'):
            pass
        else:
            # other words (the act's name, "lakiin", "edellä oleva", ...). Numbers met before such a word belong to the
            # act's name ("vuosina 1992–1993 annetun lain", "vuoden 1987 satovahinkojen"), not to a provision
            if nums and not dash and re.fullmatch(r'[a-zåäöA-ZÅÄÖ]+', t):
                nums = []
        i += 1
    if nums:
        raise ReplayError(f'numbers left over in the clause: {nums}')
    return ops


def clause_ops(blocks, scoped):
    ops, temporary = [], False
    for name, text in blocks:
        t = text.replace('\xa0', ' ').strip()
        verb = re.match(r'(muutetaan|lisätään|kumotaan)(\s+väliaikaisesti)?\b', t)
        if name == 'substitutions-originals' or name == 'insertions-originals' or name == 'repeals-originals':
            continue
        if name in ('substitutions', 'insertions', 'repeals', 'repeal'):
            mode = {'substitutions': 'sub', 'insertions': 'ins', 'repeals': 'rep', 'repeal': 'rep'}[name]
            if verb:
                temporary = temporary or bool(verb.group(2))
                t = t[verb.end():]
            ops += parse_clause(t, mode, scoped)
        else:
            raise ReplayError(f'unknown enacting block {name!r}: {text[:80]}')
    if not ops:
        raise ReplayError('no operations in the enacting clause')
    return ops, temporary


# ---- applying ----------------------------------------------------------------------------------------------------
def trim_gone(sec):
    while sec['subs'] and sec['subs'][-1].get('gone'):
        sec['subs'].pop()


def find_sec(act, k):
    for kk, ch, s in sections(act):
        if kk == k:
            return ch, s
    return None, None


def find_chapter(act, c):
    for ch in act['chapters']:
        if ch['num'] and chap_key(ch['num']) == c:
            return ch
    return None


def new_section(b):
    return {'num': b['num'], 'heading': b['heading'], 'cross': b.get('cross', ''),
            'subs': [copy.deepcopy(s) for s in b['subs']]}


def point_index(sub, k):
    for i, (pk, _ls) in enumerate(sub['points']):
        if pk == k:
            return i
    return None


def insert_section(act, k, sec, chapter_hint=None):
    """Insert sec in number order: into the hinted chapter, else after the section with the greatest smaller key."""
    target = None
    if chapter_hint is not None:
        target = find_chapter(act, chapter_hint)
    if target is None:
        best = None
        for kk, ch, s in sections(act):
            if sort_key(kk) < sort_key(k) and (best is None or sort_key(kk) > sort_key(best[0])):
                best = (kk, ch)
        if best is None:
            target = act['chapters'][0] if act['chapters'] else None
        else:
            target = best[1]
    if target is None:
        target = {'num': '', 'heading': '', 'part': '', 'sections': []}
        act['chapters'].append(target)
    pos = len(target['sections'])
    for i, s in enumerate(target['sections']):
        if sort_key(key(s['num'])) > sort_key(key(sec['num'])):
            pos = i
            break
    target['sections'].insert(pos, sec)


def repeal_list_item(amend, target):
    """An act that repeals provisions of many acts: "1 § Tällä lailla kumotaan [seuraavat lainkohdat]: 1) ... lain
    (495/1989) 8 luvun 6 §; 2) ...". The point that names the target act (by its number) is its repeal clause; no other
    section may name the target."""
    if target is None:
        return None
    no, year = target
    refs = (f'({no}/{year})', f'({no}/{str(year)[2:]})', f'( {no}/{year} )')
    secs = [s for ch in amend['chapters'] for s in ch['sections']]
    hits, mentions = [], 0
    for s2 in secs:
        for sub in s2['subs']:
            lines = sub_lines(sub)
            mentions += sum(1 for x in lines if any(r in x for r in refs))
            if sub['pre'] and re.match(r'(?:Täten|Tällä lailla)\s+kumotaan\b[^:]*:$', sub['pre'][-1]):
                # "Tällä lailla kumotaan: 1) ... lain (495/1989) 8 luvun 6 §; 2) ..."
                hits += [' '.join(ls) for _k, ls in sub['points'] if any(r in ' '.join(ls) for r in refs)]
            for x in lines:
                m = re.match(r'(?:Täten|Tällä lailla)\s+kumotaan\s+(.*)$', x)
                # "Tällä lailla kumotaan ... annetun työturvallisuuslain (738/2002) 68 §:n 3 momentti." (one act only)
                if m and not x.endswith(':') and any(r in x for r in refs) and len(re.findall(r'\(\s*\d+/\d+\s*\)', x)) == 1:
                    hits.append(m.group(1))
    if len(hits) != 1 or mentions != 1:
        return None
    clause = re.sub(r'^\d+ ?[a-z]?\)\s*', '', hits[0]).rstrip(';. ')
    if re.search(r'annettu laki\s*\(', clause):
        return None  # the whole act repealed: not a provision-level change
    return clause


def repeal_sentence(amend):
    """The text after 'kumotaan' when the act's only provisions are one repeal sentence and a commencement sentence."""
    secs = [s for ch in amend['chapters'] for s in ch['sections']]
    if len(secs) not in (1, 2):
        return None
    first = [x for sub in secs[0]['subs'] for x in sub_lines(sub)]
    if len(first) != 1:
        return None
    m = re.match(r'(?:Täten|Tällä lailla)\s+kumotaan\s+(.*)$', first[0])
    if not m:
        return None
    if len(secs) == 2:
        rest = ' '.join(x for sub in secs[1]['subs'] for x in sub_lines(sub))
        if not rest.startswith('Tämä laki tulee voimaan'):
            return None
    return m.group(1)


def apply_act(act: dict, amend: dict, target=None):
    """Replay one parsed amending act onto act (in place). Returns (changed keys, temporary flag). target: the
    amended act's (number, year), for acts that repeal provisions of many acts in one list."""
    scoped = act.get('scoped', False)
    repeal_only = repeal_sentence(amend) or repeal_list_item(amend, target)
    if repeal_only is not None and not any(n in ('substitutions', 'insertions', 'repeals', 'repeal')
                                           for n, _t in amend['enacting_blocks']):
        # "Täten kumotaan ... 32 §:n 3 momentti." in 1 § of an act that only repeals; the rest is commencement
        ops, temporary = parse_clause(repeal_only, 'rep', scoped), False
        if not ops:
            raise ReplayError('no operations in the repeal sentence')
        amend = dict(amend, chapters=[])
    else:
        ops, temporary = clause_ops(amend['enacting_blocks'], scoped)
    body = {}
    for ch in amend['chapters']:
        for s in ch['sections']:
            k = (chap_key(ch['num']) + ':' + key(s['num'])) if scoped else key(s['num'])
            if k in body:
                raise ReplayError(f'section {k} twice in the amending act')
            body[k] = (ch, s)
    bchap = {chap_key(ch['num']): ch for ch in amend['chapters'] if ch['num']}
    changed, used = [], set()

    def mark(k):
        if k not in changed:
            changed.append(k)

    # chapters first
    for op in ops:
        kind = op[0]
        if kind in ('chapter', 'ins_chapter'):
            c = op[1]
            bc = bchap.get(c)
            if bc is None:
                raise ReplayError(f'chapter {c} not in the amending act body')
            if any(s['omits'] for s in bc['sections']):
                raise ReplayError(f'chapter {c} body has omissions')
            new = {'num': bc['num'], 'heading': bc['heading'], 'part': '',
                   'sections': [new_section(s) for s in bc['sections']]}
            old = find_chapter(act, c)
            if kind == 'chapter':
                if old is None:
                    raise ReplayError(f'chapter {c} not in act')
                for s in old['sections']:
                    mark(skey(act, old, s))
                new['part'] = old['part']
                act['chapters'][act['chapters'].index(old)] = new
            else:
                if old is not None:
                    raise ReplayError(f'chapter {c} already exists')
                pos = len(act['chapters'])
                for i, ch in enumerate(act['chapters']):
                    if ch['num'] and sort_key(chap_key(ch['num'])) > sort_key(c):
                        pos = i
                        break
                if pos > 0:
                    new['part'] = act['chapters'][pos - 1]['part']
                act['chapters'].insert(pos, new)
            for s in bc['sections']:
                k = (c + ':' + key(s['num'])) if scoped else key(s['num'])
                used.add(k)
                mark(k)
        elif kind == 'chapter_heading':
            c = op[1]
            old, bc = find_chapter(act, c), bchap.get(c)
            if old is None or bc is None:
                raise ReplayError(f'chapter {c} heading: chapter missing')
            old['heading'] = bc['heading']
            changed.append('luku ' + c) if ('luku ' + c) not in changed else None
        elif kind == 'rep_chapter':
            old = find_chapter(act, op[1])
            if old is None:
                raise ReplayError(f'chapter {op[1]} not in act')
            for s in old['sections']:
                mark(skey(act, old, s))
            act['chapters'].remove(old)
        elif kind == 'rep_chapter_heading':
            raise ReplayError('repeal of a chapter heading')
    # section-level, grouped per section
    per = {}
    for op in ops:
        if op[0] in ('chapter', 'ins_chapter', 'chapter_heading', 'rep_chapter'):
            continue
        if op[0] == 'title':
            if not amend.get('new_title'):
                raise ReplayError('the clause names the act\'s name, the body gives none')
            act['title'] = amend['new_title']
            mark('nimike')
            continue
        if op[0] in ('ins_title', 'rep_title'):
            raise ReplayError('insertion or repeal of the act\'s name')
        per.setdefault(op[1], []).append(op)
    for k in sorted(per, key=sort_key):
        sops = per[k]
        kinds = {o[0] for o in sops}
        if k == 'voimaantulo':
            if any(re.fullmatch(r'voimaantulo.*', s2['heading'].lower()) for _kk, _c, s2 in sections(act)):
                raise ReplayError('a change to a commencement provision the text files as a section')
            mark('voimaantulo')
            continue  # the commencement provision is not in the files (it is the commits' trailers)
        if kinds == {'swe'}:
            if k in body:
                used.add(k)  # the Finnish body reprints the provision unchanged
            continue
        kinds -= {'swe'}
        sops = [o for o in sops if o[0] != 'swe']
        b = body.get(k, (None, None))[1]
        if 'ins_sec' in kinds:
            if b is None:
                raise ReplayError(f'new {k} § not in the body')
            if find_sec(act, k)[1] is not None:
                raise ReplayError(f'new {k} § already exists')
            if b['omits']:
                raise ReplayError(f'new {k} § has omissions')
            hint = k.split(':')[0] if scoped else None
            if hint is None:
                bch = body[k][0]
                if bch['num'] and find_chapter(act, chap_key(bch['num'])) is not None:
                    hint = chap_key(bch['num'])
            insert_section(act, k, new_section(b), hint)
            used.add(k)
            mark(k)
            if len(kinds) > 1:
                raise ReplayError(f'{k} §: insertion mixed with other operations')
            continue
        if 'rep_sec' in kinds:
            if kinds - {'rep_sec', 'rep_cross'}:
                raise ReplayError(f'{k} §: repeal mixed with other operations')
            ch, old = find_sec(act, k)
            if old is None:
                raise ReplayError(f'repealed {k} § not in act')
            i_old = ch['sections'].index(old)
            if 'rep_cross' not in kinds and old.get('cross') and i_old + 1 < len(ch['sections']) and \
                    not ch['sections'][i_old + 1].get('cross'):
                ch['sections'][i_old + 1]['cross'] = old['cross']  # the cross heading now heads the next section
            ch['sections'].remove(old)
            mark(k)
            continue
        ch, old = find_sec(act, k)
        if old is None:
            raise ReplayError(f'{k} § not in act')
        if 'sec' in kinds:
            if b is None:
                raise ReplayError(f'{k} § not in the body')
            if b['omits']:
                raise ReplayError(f'{k} § named whole but its body has omissions')
            if len(kinds) > 1 and kinds - {'sec', 'heading', 'cross'}:
                raise ReplayError(f'{k} §: whole replacement mixed with parts')
            old['heading'], old['subs'] = b['heading'], [copy.deepcopy(s) for s in b['subs']]
            if b.get('cross'):
                old['cross'] = b['cross']
            used.add(k)
            mark(k)
            continue
        if 'heading' in kinds or 'ins_heading' in kinds:
            if b is None:
                raise ReplayError(f'{k} § heading not in the body')
            old['heading'] = b['heading']
        if 'cross' in kinds or 'ins_cross' in kinds:
            if b is None or not b.get('cross'):
                raise ReplayError(f'{k} § cross heading not in the body')
            old['cross'] = b['cross']
        if 'rep_heading' in kinds:
            old['heading'] = ''
        if 'rep_cross' in kinds:
            old['cross'] = ''
        mops = [o for o in sops if o[0] in ('mom', 'intro', 'kohta', 'ins_mom', 'ins_kohta', 'kohta_intro',
                                            'alakohta', 'ins_alakohta')]
        if mops:
            if b is None:
                raise ReplayError(f'{k} § not in the body')
            units = b['subs']
            # the body prints the momentti in their NEW numbering: an inserted momentti i (new numbering) pushes
            # every old momentti from i on down one ("jolloin muutettu 4 momentti siirtyy 5 momentiksi")
            inserted = sorted({o[2] for o in mops if o[0] == 'ins_mom'})

            def newpos(m):
                for i in inserted:
                    if i <= m:
                        m += 1
                return m
            positions = sorted({newpos(o[2]) for o in mops if o[0] != 'ins_mom'} | set(inserted))
            if len(units) != len(positions):
                raise ReplayError(f'{k} §: body has {len(units)} subsections, clause names momentti {positions}')
            unit = dict(zip(positions, units))
            # substitutions (old numbering)
            for o in mops:
                kind, m = o[0], o[2]
                if kind in ('kohta_intro', 'alakohta', 'ins_alakohta'):
                    pk = o[3]
                    s, u = old['subs'][m - 1] if m <= len(old['subs']) else None, unit[newpos(m)]
                    i_old, i_new = (point_index(s, pk) if s else None), point_index(u, pk)
                    if i_old is None or i_new is None:
                        raise ReplayError(f'{k} § momentti {m} kohta {pk} missing')
                    oh, osubs = split_point(s['points'][i_old][1])
                    nh, nsubs = split_point(u['points'][i_new][1])
                    if kind == 'kohta_intro':
                        if OMIT in nh or not nh:
                            raise ReplayError(f'{k} § kohta {pk}: no introductory words in the body')
                        oh = list(nh)
                    else:
                        lt = o[4]
                        new = [x for x in nsubs if x[0] == lt]
                        if len(new) != 1 or OMIT in new[0][1]:
                            raise ReplayError(f'{k} § kohta {pk} alakohta {lt} not in the body')
                        have = [i for i, x in enumerate(osubs) if x[0] == lt]
                        if kind == 'alakohta':
                            if len(have) != 1:
                                raise ReplayError(f'{k} § kohta {pk} has no alakohta {lt}')
                            osubs[have[0]] = new[0]
                        else:
                            if have:
                                raise ReplayError(f'{k} § kohta {pk}: alakohta {lt} already exists')
                            pos = next((i for i, x in enumerate(osubs) if x[0] and x[0] > lt), len(osubs))
                            osubs.insert(pos, new[0])
                    s['points'][i_old][1] = join_point(oh, osubs)
                    continue
                if kind not in ('mom', 'intro', 'kohta'):
                    continue
                if m > len(old['subs']):
                    raise ReplayError(f'{k} § has no momentti {m}')
                u, s = unit[newpos(m)], old['subs'][m - 1]
                if kind == 'mom':
                    if u['omits'] and not u.get('omit_tail'):
                        raise ReplayError(f'{k} § momentti {m} named whole but its body has omissions')
                    u = dict(u, omits=False, omit_tail=False,
                             points=[[pk_, strip_tail_omit(ls)] for pk_, ls in u['points']])
                    if any(x[0] in ('kohta', 'intro') and x[2] == m for x in mops):
                        raise ReplayError(f'{k} § momentti {m}: whole and parts')
                    old['subs'][m - 1] = copy.deepcopy(u)
                elif kind == 'intro':
                    if not u['pre']:
                        raise ReplayError(f'{k} § momentti {m}: no introductory words in the body')
                    s['pre'] = list(u['pre'])
                else:
                    pk = o[3]
                    i_old, i_new = point_index(s, pk), point_index(u, pk)
                    if i_old is None or i_new is None:
                        raise ReplayError(f'{k} § momentti {m} kohta {pk} missing')
                    new_pt = [u['points'][i_new][0], strip_tail_omit(u['points'][i_new][1])]
                    if OMIT in new_pt[1]:
                        raise ReplayError(f'{k} § kohta {pk} named whole but its body has omissions')
                    s['points'][i_old] = new_pt
            for o in [o for o in sops if o[0] == 'rep_alakohta']:
                s = old['subs'][o[2] - 1] if o[2] <= len(old['subs']) else None
                i = point_index(s, o[3]) if s else None
                if i is None:
                    raise ReplayError(f'{k} § kohta {o[3]} missing')
                oh, osubs = split_point(s['points'][i][1])
                if sum(1 for x in osubs if x[0] == o[4]) != 1:
                    raise ReplayError(f'{k} § kohta {o[3]} has no alakohta {o[4]}')
                s['points'][i][1] = join_point(oh, [x for x in osubs if x[0] != o[4]])
            # repeals (old numbering), points and subsections from the end
            for o in sorted([o for o in sops if o[0] == 'rep_kohta'], key=lambda o: (o[2], sort_key(o[3])),
                            reverse=True):
                s = old['subs'][o[2] - 1] if o[2] <= len(old['subs']) else None
                i = point_index(s, o[3]) if s else None
                if i is None:
                    raise ReplayError(f'repealed {k} § {o[2]} mom {o[3]} kohta missing')
                del s['points'][i]
            for o in sorted([o for o in sops if o[0] == 'rep_mom'], key=lambda o: o[2], reverse=True):
                if o[2] > len(old['subs']) or old['subs'][o[2] - 1].get('gone'):
                    raise ReplayError(f'repealed {k} § momentti {o[2]} missing')
                old['subs'][o[2] - 1] = gone_sub()
            trim_gone(old)
            # insertions (new numbering)
            for o in sorted([o for o in mops if o[0] == 'ins_mom'], key=lambda o: o[2]):
                m = o[2]
                if m > len(old['subs']) + 1:
                    raise ReplayError(f'{k} §: cannot insert momentti {m}')
                if unit[m]['omits']:
                    raise ReplayError(f'{k} § new momentti {m} has omissions')
                if m <= len(old['subs']) and old['subs'][m - 1].get('gone'):
                    old['subs'][m - 1] = copy.deepcopy(unit[m])  # a new momentti in a repealed one's place
                else:
                    old['subs'].insert(m - 1, copy.deepcopy(unit[m]))
            for o in [o for o in mops if o[0] == 'ins_kohta']:
                m, pk = o[2], o[3]
                if m > len(old['subs']):
                    raise ReplayError(f'{k} § has no momentti {m} for a new kohta')
                m = newpos(m)
                if m > len(old['subs']):
                    raise ReplayError(f'{k} § has no momentti {m} for a new kohta')
                s, u = old['subs'][m - 1], unit[m]
                i_new = point_index(u, pk)
                if i_new is None or point_index(s, pk) is not None:
                    raise ReplayError(f'{k} § new kohta {pk} not insertable')
                pos = len(s['points'])
                for i, (q, _l) in enumerate(s['points']):
                    if sort_key(q) > sort_key(pk) if re.fullmatch(r'\d+[a-z]?', q) and re.fullmatch(r'\d+[a-z]?', pk) else False:
                        pos = i
                        break
                s['points'].insert(pos, copy.deepcopy(u['points'][i_new]))
        else:
            rest = [o for o in sops if o[0] in ('rep_mom', 'rep_kohta')]
            for o in [o for o in sops if o[0] == 'rep_alakohta']:
                s = old['subs'][o[2] - 1] if o[2] <= len(old['subs']) else None
                i = point_index(s, o[3]) if s else None
                if i is None:
                    raise ReplayError(f'{k} § kohta {o[3]} missing')
                oh, osubs = split_point(s['points'][i][1])
                if sum(1 for x in osubs if x[0] == o[4]) != 1:
                    raise ReplayError(f'{k} § kohta {o[3]} has no alakohta {o[4]}')
                s['points'][i][1] = join_point(oh, [x for x in osubs if x[0] != o[4]])
            for o in sorted([o for o in rest if o[0] == 'rep_kohta'], key=lambda o: (o[2], sort_key(o[3])), reverse=True):
                s = old['subs'][o[2] - 1] if o[2] <= len(old['subs']) else None
                i = point_index(s, o[3]) if s else None
                if i is None:
                    raise ReplayError(f'repealed {k} § kohta missing')
                del s['points'][i]
            for o in sorted([o for o in rest if o[0] == 'rep_mom'], key=lambda o: o[2], reverse=True):
                if o[2] > len(old['subs']) or old['subs'][o[2] - 1].get('gone'):
                    raise ReplayError(f'repealed {k} § momentti {o[2]} missing')
                old['subs'][o[2] - 1] = gone_sub()
            trim_gone(old)
        unknown = kinds - {'kohta_intro', 'alakohta', 'ins_alakohta', 'rep_alakohta',
                           'heading', 'ins_heading', 'cross', 'ins_cross', 'rep_heading', 'rep_cross', 'mom', 'intro', 'kohta',
                           'ins_mom', 'ins_kohta', 'rep_mom', 'rep_kohta'}
        if unknown:
            raise ReplayError(f'{k} §: unsupported operations {sorted(unknown)}')
        if b is not None:
            used.add(k)
        mark(k)
    unused = set(body) - used
    if unused:
        raise ReplayError(f'body sections not covered by the enacting clause: {sorted(unused, key=sort_key)}')
    for kk, _ch, sec in sections(act):  # an omission marker must never reach the text
        if any(OMIT in sub_lines(u) for u in sec['subs']):
            raise ReplayError(f'{kk} §: an omitted part of a point would enter the text')
    return sorted(changed, key=lambda x: sort_key(x) if not x.startswith('luku') else (0, '', 0, x)), temporary


def sections_keys(act):
    return [k for k, _ch, _s in sections(act)]
