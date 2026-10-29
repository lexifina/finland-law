"""Independent word check: Finlex's own XML against what git holds, for every act in the repository.

The source side is the raw cached Akoma Ntoso XML (cache/<sha1(url)>.bin), read with lxml's itertext() and nothing
from build.py or replay.py (only the API address and the cache naming are shared, to find the files). The git side is
each act's files at a commit, read with `git ls-tree -r` + `git cat-file --batch`. Both sides are reduced to words
(runs of letters and digits, casefolded; whitespace and punctuation dropped).

    python -X utf8 scripts/finland-law/check_words.py [REPO] [--record]

For every act in the repository's acts.csv:
1. FIRST version: the original statute (`statute/<y>/<n>/fin@`, Finnish): docTitle, the enacting formula and every
   text node of the statuteProvisionsWrapper, in document order, against the act's files at its first commit.
2. FINAL version: the same from the act's current consolidation, requested in Finnish with its version
   (`statute-consolidated/<y>/<n>/fin@<version>`; without a language the API returns Swedish first), against the
   act's section files at the last law commit (johdanto.md, the original's title and formula, is checked in 1).
3. EACH amending act: the words of every section of its statuteProvisionsWrapper whose file that commit changes must
   all be in that file (counted; an amending act prints only the parts it changes, so it is a subset). A new law
   that repeals part of the act in its own provisions (not an amending act), prints none of
   the act's text (its title names no amendment or repeal, its formula no "muutetaan" / "kumotaan" / "lisätään"):
   there every file the commit changes may only lose words.

Excluded from the source, as the build documents (README/REPRODUCE: "Normalisation"):
  - docNumber: it is the commit's author and trailer, not file text;
  - the consolidation's trailing amendment labels on headings, "(4.11.2011/1112)", and its editorial notes
    (noteAuthorial), and the correction notes in the originals (authorialNote, "Merkitty kohta oikaistu ..."):
    Finlex editorial additions;
  - the body's `conclusions` (preliminary works, signatures) and every hcontainer outside the
    statuteProvisionsWrapper (the amending acts' own commencement rules: commit trailers);
  - Finlex's repeal markers in the consolidation ("3 momentti on kumottu L:lla 29.8.2008/562."): editorial notes;
  - the present wording a consolidation prints after a coming amendment ("Aiempi sanamuoto kuuluu:"): the newest
    version of each provision is compared, as the repository's last commit holds every amendment;
  - part and chapter number and heading (the word "luku", which older originals omit, is not compared): the repo repeats them on "Osa:" / "Luku:" lines in every section file, so
    they are compared separately (every distinct Osa:/Luku: line must be a part or chapter heading of the source).
The repo's "Väliotsikko:" line (a cross heading) is read where the source prints it, before the section.
On the git side, for the FINAL comparison only, the original's line-break hyphen "valtio-neuvoston" (731/1999 131 §,
kept as the Statute Book prints it) is read as the consolidation's "valtioneuvoston" (build.py NORMALISATION (3)).
Nothing else is excused.
"""
import csv
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get('FINLEX_SOURCES') or HERE / 'cache')
API = 'https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act'
WORD = re.compile(r'[^\W_]+')
LABEL = re.compile(r'\(\d{1,2}\.\d{1,2}\.\d{4}/\d+\)')
SKIP_TAGS = ('docNumber',)


JOIN = re.compile(r'(?<=[^\W\d_])[-‐‑­]\s*(?=[^\W\d_])')


def words(s: str) -> list:
    """Words, with a hyphen inside a word joined on both sides (line-break hyphenation left in one source:
    "rautatieyrityk-sellä"; declared in the build's normalisation (11))."""
    return [w.casefold() for w in WORD.findall(JOIN.sub('', s.replace('―', '–')))]


def local(e) -> str:
    return etree.QName(e).localname if isinstance(e.tag, str) else ''


def akn(url: str, lang='fin'):
    f = CACHE / (hashlib.sha1(url.encode()).hexdigest() + '.bin')
    if not f.exists():
        raise SystemExit(f'not in the cache: {url}')
    root = etree.parse(str(f)).getroot()
    docs = [e for e in root.iter() if local(e) == 'akomaNtoso']
    for d in docs:
        lg = next((e for e in d.iter() if local(e) == 'FRBRlanguage'), None)
        if lg is not None and lg.get('language') == lang:
            return d
    raise SystemExit(f'no {lang} document in {url}')


MARKER = re.compile(r'(?:\d+ ?[a-z]? (?:§|mom[a-z]*\.?|kohta|luku|alakohta)|Väliotsikko|Otsikko) on kumottu \w*:lla '
                    r'\d+\.\d+\.\d{4}/\d+(?:, joka tulee voimaan \d+\.\d+\.\d{4}\. Aiempi sanamuoto kuuluu:|\.?)')


def is_marker(x) -> bool:
    """A repeal marker ("3 momentti on kumottu L:lla 29.8.2008/562."): Finlex's editorial note, not text."""
    return local(x) in ('p', 'paragraph', 'subsection', 'content', 'crossHeading') and \
        bool(MARKER.fullmatch(' '.join(''.join(x.itertext()).split())))


def marker_only(x) -> bool:
    """A section or chapter whose only content is a repeal marker."""
    rest = [c for c in x if local(c) not in ('num', 'heading')]
    return local(x) in ('section', 'chapter') and len(rest) == 1 and is_marker(rest[0])


def head_words(s: str) -> tuple:
    return tuple(w for w in words(s) if w != 'luku')  # "1 luku" in the consolidation, "1" in older originals


def text_of(e) -> str:
    """itertext without editorial notes."""
    out = []

    def walk(x):
        if (local(x) == 'hcontainer' and x.get('name') == 'noteAuthorial') or local(x) == 'authorialNote' or is_marker(x):
            if x.tail:
                out.append(x.tail)
            return
        if x.text:
            out.append(x.text)
        for c in x:
            walk(c)
        if x.tail:
            out.append(x.tail)
    if e.text:
        out.append(e.text)
    for c in e:
        walk(c)
    return ' '.join(out)


def wrapper(doc):
    act = next(x for x in doc if isinstance(x.tag, str))
    return act, [e for e in act.iter() if local(e) == 'hcontainer' and e.get('name') == 'statuteProvisionsWrapper']


def drop_superseded(doc):
    """Where a consolidation prints a provision's coming version (eId ending v<8 digits>) and, after "Aiempi
    sanamuoto kuuluu:", its present wording under the same eId, keep only the newest: the repository's last commit
    (the tip of `future`) holds every amendment."""
    for parent in list(doc.iter()):
        seen = {}
        for c in list(parent):
            eid = c.get('eId') if isinstance(c.tag, str) else None
            if not eid:
                continue
            m = re.search('v([0-9]{8})$', eid)
            seen.setdefault(re.sub('v[0-9]{8}$', '', eid), []).append((int(m.group(1)) if m else 0, c))
        for items in seen.values():
            top = max(v for v, _c in items)
            for v, c in items:
                if v != top:
                    parent.remove(c)


def source_words(doc, with_title=True):
    """(words in document order, set of part/chapter heading word tuples)."""
    act, wraps = wrapper(doc)
    ws = []
    if with_title:
        title = next(e for e in act.iter() if local(e) == 'docTitle')
        ws += words(''.join(title.itertext()))
        for f in act.iter():
            if local(f) == 'formula':
                ws += words(''.join(f.itertext()))
    heads = set()

    def walk(parent):
        nonlocal ws
        for c in parent:
            t = local(c)
            if marker_only(c) or is_marker(c):
                continue
            if t in ('chapter', 'part'):
                head = [x for x in c if local(x) in ('num', 'heading')]
                heads.add(head_words(LABEL.sub(' ', ' '.join(''.join(h.itertext()) for h in head))))
                walk(c)
            elif t in ('num', 'heading') and local(parent) in ('chapter', 'part'):
                pass
            elif t == 'hcontainer' and c.get('name') == 'noteAuthorial':
                pass
            elif t:
                ws += words(LABEL.sub(' ', text_of(c)))
    for w in wraps:
        walk(w)
    # an original that prints its last section ("11 § Voimaantulo") as a heading only, with the text in the
    # entryIntoForce container: the repository files that text under the section, as the consolidation does
    secs = [s for w in wraps for s in w.iter() if local(s) == 'section']
    body = next((e for e in act.iter() if local(e) == 'body'), None)
    eif = [h for h in body if local(h) == 'hcontainer' and h.get('name') == 'entryIntoForce'] if body is not None else []
    if secs and len(eif) == 1 and not [c for c in secs[-1] if local(c) not in ('num', 'heading')] and \
            any(local(c) == 'heading' for c in secs[-1]):
        ws += words(text_of(eif[0]))
    return ws, heads


def sec_file_key(num: str):
    m = re.match(r'\s*(\d+)\s*([a-z]?)', num)
    return f'pykala-{int(m.group(1)):03d}{m.group(2)}.md' if m else None


def section_texts(doc) -> list:
    _act, wraps = wrapper(doc)
    out = []
    for w in wraps:
        for s in w.iter():
            if local(s) == 'section':
                num = next((x for x in s if local(x) == 'num'), None)
                k = sec_file_key(''.join(num.itertext())) if num is not None else None
                if k:
                    out.append((k, words(LABEL.sub(' ', text_of(s)))))
    return out


def order_key(n: str):
    if n == 'johdanto.md':
        return (-1, '', -1, '')
    m = re.fullmatch(r'(?:luku-(\d+)([a-z]*)-)?pykala-(\d+)([a-z]*)\.md', n)
    return (int(m.group(1) or 0), m.group(2) or '', int(m.group(3)), m.group(4))


class Git:
    def __init__(self, repo):
        self.repo = repo
        self.cat = subprocess.Popen(['git', '-C', str(repo), 'cat-file', '--batch'], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE)

    def run(self, *a):
        return subprocess.run(['git', '-C', str(self.repo), *a], capture_output=True, text=True, encoding='utf-8',
                              check=True).stdout

    def blob(self, sha) -> str:
        self.cat.stdin.write(sha.encode() + b'\n')
        self.cat.stdin.flush()
        head = self.cat.stdout.readline().split()
        size = int(head[2])
        data = self.cat.stdout.read(size + 1)[:-1]
        return data.decode('utf-8')

    def files(self, commit, folder) -> dict:
        out = {}
        for line in self.run('ls-tree', '-r', commit, folder + '/').splitlines():
            meta, path = line.split('\t', 1)
            out[path.split('/', 1)[1]] = self.blob(meta.split()[2])
        return out


def repo_words(fs, final=False):
    ws, heads = [], set()
    for n in sorted(fs, key=order_key):
        if final and n == 'johdanto.md':
            continue
        lines = fs[n].split('\n')
        cross = [l for l in lines if l.startswith('Väliotsikko: ')]
        for l in cross:
            ws += words(l[len('Väliotsikko: '):])
        body = []
        for line in lines:
            if line.startswith(('Luku: ', 'Osa: ')):
                heads.add(head_words(line.split(': ', 1)[1]))
            elif line.startswith('Väliotsikko: '):
                pass
            else:
                body.append(line.replace('valtio-neuvoston', 'valtioneuvoston') if final else line)
        # one file's text at once, as the source side reads one section at once: a word the source breaks with a
        # hyphen across two elements ("päätök-" | "seen", 205/1996 12 §) is two lines here and is joined on both sides
        ws += words('\n'.join(body))
    return ws, heads


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    record = '--record' in sys.argv
    repo = Path(args[0] if args else HERE.parents[2] / 'finland-law-build')
    sys.stdout.reconfigure(encoding='utf-8')
    g = Git(repo)
    versions = {f'{a["no"]}/{a["year"]}': a.get('version', '')
                for a in json.loads((HERE / 'acts.json').read_text(encoding='utf-8'))}
    with open(repo / 'acts.csv', encoding='utf-8', newline='') as f:
        acts = list(csv.DictReader(f))
    log = {}
    tip = 'future' if g.run('branch', '--list', 'future').strip() else 'main'  # the last commit holds every amendment
    for line in g.run('log', '--format=%H%x09%an%x09%s', tip).splitlines():
        h, au, subj = line.split('\t', 2)
        log[h] = (au, subj)
    problems, n_words, n_amend, n_absent, repealers = [], 0, 0, 0, []

    def compare(label, src, ours):
        nonlocal n_words
        n_words += len(src)
        if src == ours:
            return True
        sm = difflib.SequenceMatcher(None, src, ours, autojunk=False)
        diffs = []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op != 'equal':
                diffs.append(f'{op}: …{" ".join(src[max(0, i1 - 4):i1])} [{" ".join(src[i1:i2])}] '
                             f'{" ".join(src[i2:i2 + 4])}… / git [{" ".join(ours[j1:j2])}]')
        problems.append(f'{label}: source {len(src)} words, git {len(ours)}; ' + ' | '.join(diffs[:3]))
        return False

    for row in acts:
        act, folder = row['act'], row['folder']
        no, year = act.split('/')
        commits = [l for l in g.run('log', '--reverse', '--format=%H', tip, '--', folder + '/').split() if l]
        commits = [c for c in commits if log[c][0] != 'Lexifina']
        if not commits:
            problems.append(f'{act}: no commits')
            continue
        # 1. first version
        sw, sh = source_words(akn(f'{API}/statute/{year}/{no}/fin@'))
        fw, fh = repo_words(g.files(commits[0], folder))
        compare(f'{act} first version', sw, fw)
        if fh - sh:
            problems.append(f'{act} first version: Osa/Luku lines not in the source: {sorted(fh - sh)[:3]}')
        # 3. amending acts
        for c in commits[1:]:
            au, subj = log[c]
            if au == act or subj.endswith('expired)'):
                continue
            ano, ayear = au.split('/')
            changed = {p.split('/', 1)[1] for p in g.run('diff-tree', '--no-commit-id', '--name-only', '-r', c,
                                                         '--', folder + '/').split() if p}
            fs = g.files(c, folder)
            n_amend += 1
            adoc = akn(f'{API}/statute/{ayear}/{ano}/fin@')
            formula = ' '.join(''.join(f.itertext()) for f in adoc.iter() if local(f) == 'formula')
            atitle = ' '.join(''.join(t.itertext()) for t in adoc.iter() if local(t) == 'docTitle')
            if not re.search(r'muuttamisesta|kumoamisesta|lisäämisestä|muutoksista', atitle) and \
                    not re.search(r'\b(?:muutetaan|kumotaan|lisätään)\b', formula):
                # not an amending act of this act but a new law that repeals part of it in its own provisions
                # ("Tällä lailla kumotaan: ... 9) ... (1551/1995) 9 §:n 1 momentti"): its sections are its own and are
                # not this act's text, so the commit may only remove words from the files it changes
                repealers.append(f'{act} by {au}')
                before = g.files(c + '~1', folder)
                for n in sorted(changed):
                    extra = Counter(words(fs.get(n, ''))) - Counter(words(before.get(n, '')))
                    if extra:
                        problems.append(f'{act} repealed in part by {au}: {n}: {sum(extra.values())} words added: '
                                        f'{extra.most_common(6)}')
                continue
            for k, ws in section_texts(adoc):
                names = [n for n in changed if (n == k or n.endswith('-' + k)) and n in fs]  # (a repealed file: gone)
                if not names or ws[1:4] == ['tällä', 'lailla', 'kumotaan'] or ws[1:3] == ['täten', 'kumotaan'] or \
                        ws[1:5] == ['tämä', 'laki', 'tulee', 'voimaan']:
                    continue  # the act's own repeal sentence or commencement section ("2 § Tämä laki tulee voimaan")
                best = min((sum((Counter(ws) - Counter(words(fs.get(n, '')))).values()), n) for n in names)
                if best[0]:
                    miss = Counter(ws) - Counter(words(fs.get(best[1], '')))
                    problems.append(f'{act} amended by {au}: {best[1]}: {best[0]} of {len(ws)} words not in the file: '
                                    f'{miss.most_common(6)}')
                n_words += len(ws)
        # 2. final version
        cons = akn(f'{API}/statute-consolidated/{year}/{no}/fin@{versions.get(act, "")}')
        if any(local(e) == 'hcontainer' and e.get('name') == 'contentAbsent' for e in cons.iter()):
            if len(commits) != 1:
                problems.append(f'{act}: the consolidation has no text but the act has {len(commits)} commits')
            n_absent += 1
            continue
        drop_superseded(cons)
        sw, sh = source_words(cons, with_title=False)
        fw, fh = repo_words(g.files(commits[-1], folder), final=True)
        compare(f'{act} final version', sw, fw)
        if fh - sh:
            problems.append(f'{act} final version: Osa/Luku lines not in the source: {sorted(fh - sh)[:3]}')
    for p in problems:
        print(p)
    summary = (f'{len(acts)} acts, {n_amend} amending commits, {n_words} source words checked ({n_absent} never-amended '
               f'acts whose consolidation has no text: first version only; {len(repealers)} commits by new laws repealing part of '
               f'an act: removals only); {len(problems)} problems')
    print(summary)
    if record:
        p = HERE / 'verification.json'
        rec = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        rec['words'] = {'acts': len(acts), 'amending_commits': n_amend, 'source_words': n_words,
                        'no_consolidation_text': n_absent, 'problems': len(problems), 'examples': problems[:20],
                        'summary': summary}
        p.write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8',
                     newline='\n')
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
