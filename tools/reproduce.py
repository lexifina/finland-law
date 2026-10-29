"""Writes the repository's documentation: README.md, LICENSE, REPRODUCE.md, corpus.json, acts.csv, amendments.csv,
LEFT-OUT.csv, and copies the tools. Called by build.py; deterministic (no clock, no network). verification.json (from
check_repo.py / test_repo.py) is read when it exists. Every non-law file goes through publish_gate before the commit."""
import collections
import json
import re
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'gitlaw_common'))
try:
    from publish_gate import publish_gate
except ImportError:          # an outside reader rebuilding from the published tools: no private rule to apply
    if os.environ.get('GITLAW_PUBLIC_REBUILD') != '1':
        raise SystemExit('publish gate missing: run from lexifina/scripts, or set GITLAW_PUBLIC_REBUILD=1')

    def publish_gate(files): return files

# Published with the record: what a reader needs to rebuild from the retrieved answers and check the result. The
# retrieval scripts stay private (the user's rule of 2026-10-29).
PUBLISHED_TOOLS = ('build.py', 'replay.py', 'reproduce.py', 'check_repo.py', 'check_words.py', 'test_repo.py',
                   'finlex.py', 'acts.json', 'decree_dates.json')

NOTE_REPLAY = ('Finlex serves only the CURRENT consolidation of a statute. The intermediate versions in this '
               'repository are therefore REPLAYED: the original statute is taken from the Statute Book and each '
               'amending act is applied, in in-force order, as the act\'s own enacting clause states (whole sections, '
               'headings, cross headings, subsections, introductory words, points and chapters; replacement, insertion '
               'and repeal; temporary amendments end the day after their last day in force). Only the end state of each '
               'act is checked against the official consolidation; the earlier versions are not independently confirmed.')

MODEL = 'claude-opus-5-5 (Claude Agent SDK)'


def csv_row(xs):
    return ','.join('"' + str(x).replace('"', '""') + '"' for x in xs)


def report_norm(here: Path) -> str:
    src = (here / 'build.py').read_text(encoding='utf-8')
    a = src.index('NORMALISATION = (')
    ns = {}
    exec(src[a:src.index('\n\n\ndef nline')], ns)
    return ns['NORMALISATION']


def write_docs(repo: Path, datas, scan, today, here: Path):
    ver = {}
    vp = here / 'verification.json'
    if vp.exists():
        ver = json.loads(vp.read_text(encoding='utf-8'))
    datas = sorted(datas, key=lambda d: (d['cfg']['year'], int(re.match(r'\d+', d['cfg']['no']).group()), d['cfg']['no']))
    n_acts = len(datas)
    n_amended = sum(1 for d in datas if d['ams'])
    n_nocons = sum(1 for d in datas if d['no_cons'])
    in_force = [e for d in datas for e in d['events'] if e[0] <= today]
    n_am_commits = sum(1 for d in datas for e in d['events'] if e[1] != 'base' and e[0] <= today)
    n_future = sum(1 for d in datas for e in d['events'] if e[0] > today)
    n_expired = sum(1 for d in datas for e in d['events'] if e[1] == 'expire')
    first = min(d['base_inforce'] for d in datas)
    last = max(e[0] for e in in_force)
    left = sorted((v for v in scan.values() if not v['ok']),
                  key=lambda v: (int(v['act'].split('/')[1]), int(re.match(r'\d+', v['act'].split('/')[0]).group())))
    total = len(scan)

    def reason_class(r):
        r = r or ''
        if 'contentAbsent' in r and 'original statute not served' in r:
            return 'no text in the open data (neither the original nor the consolidation)'
        if 'original statute not served' in r or 'no body' in r:
            return 'the original statute has no text in the open data'
        if 'an amending act whose consolidation has no text' in r:
            return 'an amending act listed as in force (its changes belong to the act it amends)'
        if 'consolidation has no text' in r:
            return 'the consolidation has no text, so an amended act cannot be verified'
        if 'no in-force date for the original' in r:
            return 'no in-force day for the original (in force by decree on a day the data does not give)'
        if 'no commencement date' in r:
            return 'an amending act has no in-force day (older amendments, often with no text in the open data)'
        if 'staged commencement' in r:
            return 'an amending act takes effect in stages (per-section dates)'
        if 'end state differs' in r and '[misprint]' in r:
            return 'end state differs from the consolidation by a misprint (at most 3 characters): a source discrepancy'
        if 'end state differs' in r and '[wording]' in r:
            return 'end state differs from the consolidation in wording (same structure)'
        if 'end state differs' in r:
            return 'end state differs from the consolidation in structure (sections or lines on one side only)'
        if 'not served' in r:
            return 'an amending act is not in the open data'
        if 'temporary' in r:
            return 'temporary amendments the replay does not resolve'
        if 'error:' in r:
            return 'source markup the parser does not read'
        return 'an enacting clause or body the replay grammar does not cover'
    classes = collections.Counter(reason_class(v.get('reason')) for v in left)

    corpus = {
        'corpus': 'fi/national/acts',
        'name': f'Finnish law: {n_acts} acts in force (Finlex), replayed and verified',
        'label': 'Finland',
        'language': 'fi',
        'acts': [f'{d["cfg"]["no"]}/{d["cfg"]["year"]}' for d in datas],
        'history_from': first,
        'record_to': today,
        'record_note': (f'{n_acts} of the {total} acts in force that Finlex consolidates: each from its original text, '
                        f'with its amending acts replayed and the end state verified against the official '
                        f'consolidation ({n_nocons} never-amended acts whose consolidation carries no text are their '
                        f'original alone); {total - n_acts} acts the replay cannot verify, or with no text in the open '
                        f'data, are left out (LEFT-OUT.csv). Data retrieved {today}.'),
        'in_force_trailer': 'In-force',
        'built': today,
        'dates': {'commit': 'in-force', 'other_trailers': ['Enacted', 'Published', 'Expires'],
                  'time': '12:00 Europe/Helsinki (UTC+2, UTC+3 in summer time from 1981)'},
        'scope': {
            'trailer': 'Applies',
            'when_absent': 'this corpus records no scope notes (no commit carries Applies:); the absence is not a '
                           'statement that the change reaches every case',
            'default_reach': {
                'rule': 'no genuine retroactivity (Constitution 8 §: nullum crimen; legislative practice otherwise)',
                'meaning': 'an act applies from the day it takes effect; the Constitution forbids retroactive '
                           'criminal liability and penalties (8 §) and no general rule is written beyond that',
                'authority': 'Suomen perustuslaki 731/1999, 8 § (Rikosoikeudellinen laillisuusperiaate); '
                             'transitional rules are in each amending act\'s own commencement provision',
                'decided_here': False}},
        'replayed': True,
        'tool': 'tools/finlex.py',
        'citation': {'file': '([a-z0-9-]+)/(?:luku-[0-9a-z]+-)?pykala-0*([0-9]+[a-z]*)\\.md',
                     'cites': '([^0-9]|^){n} ?§'},
        'checked_on': today,
        'history': 'replayed',
        'verification': (f"Verified against official text: each act's first commit is its original statute text; for "
                         f"the {n_acts - n_nocons} acts with a consolidation text, the last commit equals Finlex's "
                         f'current consolidation (every section, after the declared normalisation). The {n_nocons} '
                         f'never-amended acts whose consolidation carries no text are checked against the original '
                         f'only. Intermediate states are replays of the amending acts and are not independently '
                         f'verified.'),
    }
    (repo / 'corpus.json').write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + '\n', encoding='utf-8',
                                      newline='\n')

    rows = ['act,folder,title,in_force,amendments,sections,consolidated_on']
    for d in datas:
        rows.append(csv_row([f'{d["cfg"]["no"]}/{d["cfg"]["year"]}', d['folder'], d['title'], d['base_inforce'],
                             len(d['ams']), d['n_sections'], d['consolidated_on'] or '']))
    (repo / 'acts.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8', newline='\n')
    rows = ['act,amended_act,folder,title,enacted,published,in_force,expires,provisions_changed,commencement']
    for d in datas:
        for a in d['ams']:
            rows.append(csv_row([f'{a["no"]}/{a["year"]}', f'{d["cfg"]["no"]}/{d["cfg"]["year"]}', d['folder'],
                                 a['title'], a.get('enacted') or '', a.get('published') or '', a['dates'][0],
                                 a.get('expires') or '', ' '.join(a.get('changed', [])), a.get('commencement', '')]))
    (repo / 'amendments.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8', newline='\n')
    rows = ['act,title,amendments,reason_class,reason']
    for v in left:
        rows.append(csv_row([v['act'], v.get('title', ''), v.get('amendments', ''), reason_class(v.get('reason')),
                             v.get('reason', '')]))
    (repo / 'LEFT-OUT.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8', newline='\n')

    (repo / 'LICENSE').write_text(
        'Source of the text: Finlex, the legal database of Finland, owned by the Ministry of Justice\n'
        '(Oikeusministeriö), open data interface https://opendata.finlex.fi (Akoma Ntoso XML).\n\n'
        'Finlex states (https://www.finlex.fi/en/data-protection): "Finlex open data is available under a Creative\n'
        'Commons Attribution 4.0 (CC BY 4.0) licence." It adds: "The data may not be used for purposes that breach or\n'
        'compromise the protection of personal data." and "The data may not be combined in a manner that violates a\n'
        'person\'s privacy." Licence text: https://creativecommons.org/licenses/by/4.0/legalcode\n\n'
        'Attribution for the legal texts in this repository: "Contains data from Finlex (Oikeusministeriö / Ministry of\n'
        'Justice, Finland), https://www.finlex.fi, licensed under CC BY 4.0." The texts of the replayed intermediate\n'
        'versions are derived from that data by the method in REPRODUCE.md; they are not published by Finlex.\n\n'
        'Finlex says the consolidated texts are for information; only the Statute Book (Suomen säädöskokoelma) is\n'
        'authoritative.\n\n'
        'The history (commits and commit messages), the tables and the tools are dedicated to the public domain under\n'
        'CC0 1.0 Universal: https://creativecommons.org/publicdomain/zero/1.0/legalcode\n'
        'Reuse terms for the whole repository have not been separately reviewed: it stays private.\n',
        encoding='utf-8', newline='\n')

    ok_text = ''
    if ver:
        c = ver.get('check', {})
        s = ver.get('smoke', {})
        ok_text = (f'- check_repo.py: {c.get("summary", "not recorded")}\n'
                   f'- check_words.py: {ver.get("words", {}).get("summary", "not recorded")}\n'
                   f'- test_repo.py: {s.get("passed", "?")}/{s.get("total", "?")} passed\n')
    class_table = '\n'.join(f'| {k} | {v} |' for k, v in sorted(classes.items(), key=lambda kv: -kv[1]))
    vaali = next((d for d in datas if d['cfg']['year'] == 1998 and d['cfg']['no'] == '714'), None)
    vaali_line = ('Vaalilaki (714/1998) is built.' if vaali else
                  'Vaalilaki (714/1998) is left out: ' + (scan.get('714/1998', {}).get('reason') or 'not scanned') + '.')
    (repo / 'README.md').write_text(f'''# Finnish law, change by change

{n_acts} Finnish acts in force (lait), as a git repository: the Constitution (Suomen perustuslaki, 731/1999) and every
other act Finlex consolidates whose history the replay can reproduce. Each amending act is one commit, credited to that
act and dated the day it took effect, at noon in Helsinki. Text is Finnish.

**This record is partial.** It holds the {n_acts:,} acts that replay exactly, of {total:,} in force; later rebuilds will
add more and re-date the history, so commit hashes and tags may change.

**The intermediate versions are replayed. Only each act's end state is checked against an official consolidation.**
{NOTE_REPLAY} The method is the same as the US Code repository: replay, then check the result against the official text.

| | |
| --- | --- |
| Acts in force that Finlex consolidates | {total} |
| Acts here | {n_acts} ({n_amended} amended, end state verified against the consolidation; {n_acts - n_amended} never amended, of them {n_nocons} whose consolidation carries no text, so the original is not compared with one) |
| Acts left out | {total - n_acts} ([`LEFT-OUT.csv`](LEFT-OUT.csv)) |
| Amendment commits on `main` | {n_am_commits} (of them {n_expired} ends of temporary amendments) |
| Commits on the never-merged `future` branch | {n_future} |
| First in-force day | {first} |

## The questions, as git commands

| Question | Command |
| --- | --- |
| What did 10 § of the Constitution say on 1 January 2015? | `python tools/finlex.py 2015-01-01 perustuslaki 10` |
| The whole Constitution as it stood in 2010 | `python tools/finlex.py 2010-01-01 perustuslaki` |
| Which act wrote each line of 10 §? | `git blame perustuslaki/pykala-010.md` |
| Every change to 10 §, with the act and the day | `git log --follow perustuslaki/pykala-010.md` |
| Everything one act changed | `git show --stat perustuslaki/2011-1112` |
| Which folder holds an act | [`acts.csv`](acts.csv) (act number, folder, title) |
| The amending acts with all their dates | [`amendments.csv`](amendments.csv) |
| Everything that took effect on 1 January 2020 | `git log --grep="^In-force: 2020-01-01" --format="%an %s"` |
| Every act's history, one tag per act and amendment | `git tag --list "perustuslaki/*"` |

## The dates, and which one we use

A Finnish act has a date it was enacted (`Enacted:`, the signature day), a day it was published in the Statute Book
(`Published:`) and a day it takes effect (`In-force:`). **Commits are dated the day the change took effect**, at noon in
Helsinki (EET, or EEST in summer time from 1981). Each amending act here has a single commencement day, read from
Finlex's `amendedBy` list; acts that take effect in stages are left out. A temporary amendment carries `Expires:`; the
day after it, a second commit by the same act restores the earlier text. Changes that take effect after the build day
are on the `future` branch, never merged. Days before 1970 are clamped to 1970-01-01 in git (seconds apart, in order);
`In-force:` carries the real day.

## What "author" means

The author of a commit is the act that made the change (`817/2018`, address `sd-2018-817@finlex.invalid`), so `git blame`
names the act. The committer is always `Lexifina`.

## How it is checked

`tools/build.py scan` replays every act and stops each one unless its replayed end state equals the official
consolidation for every section, after this normalisation: {report_norm(here)} Only acts that pass are committed.
`tools/check_repo.py` then reads every act's versions back from git and compares the last with the consolidation and the
first with the original statute; `tools/check_words.py` compares the letters and digits of every act's last version
with the consolidation's raw XML text, read by a separate minimal extractor; `tools/test_repo.py` checks that the
commands above work. See [`REPRODUCE.md`](REPRODUCE.md).
{ok_text}
## Files

`<folder>/pykala-NNN.md` is one section (pykälä); the `#` line is its number and heading, then `Osa:` its part, `Luku:`
its chapter and `Väliotsikko:` the cross heading printed before it, when it has them; each subsection (momentti) is a
paragraph block, each line one paragraph or point. Acts that number sections per chapter use
`luku-NN-pykala-NNN.md`. `johdanto.md` is the title and enacting words. A repealed section is removed. A folder is the
act's one-word name (`vaalilaki`) or `sd-<year>-<number>`; the Constitution keeps `perustuslaki`.

## What it does not show

- Versions Finlex does not publish: only the current consolidation exists; the rest is replayed.
- Acts left out ({total - n_acts}), by reason:

| Reason | Acts |
| --- | --- |
{class_table}

- {vaali_line}
- Amendments Finlex does not list in `amendedBy`: the end-state check would fail if one were missing.
- Decrees (asetukset) and acts no longer in force.

## Licence

Finlex open data, CC BY 4.0: see [`LICENSE`](LICENSE) for the exact words and attribution. Tools, history and tables: CC0.

## Source

Finlex open data interface (Oikeusministeriö), https://opendata.finlex.fi, Akoma Ntoso. API quirks were learned from
LawVM (https://github.com/eliask/lawvm, MIT) and legalize-dev (https://github.com/legalize-dev); no code was copied.
''', encoding='utf-8', newline='\n')

    (repo / 'REPRODUCE.md').write_text(f'''# Reproduce

Data retrieved {today}. Everything below is deterministic: the same data gives the same hashes.

## Steps

The sources are Finlex open data answers (Akoma Ntoso XML): for each act in force listed in `tools/acts.json` (Finlex's
list of consolidated acts in force, with each act's consolidation version), its current consolidation
(`statute-consolidated/<year>/<no>/fin@<version>`), its original statute (`statute/<year>/<no>/fin@`) and every amending
act in the consolidation's `amendedBy` list (`statute/<year>/<no>/fin@`), all under
`https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act`. The build reads each answer from a file named by the sha1
of its URL plus `.bin` (an answer of 404 is the bytes `\\x00404`), in the folder `FINLEX_SOURCES`. How the answers were
retrieved is not part of this record.

1. With `tools/acts.json` and the retrieved answers in place, `python -X utf8 tools/build.py scan --offline` replays and
   gates every act and writes `scan.json`.
2. `python -X utf8 tools/build.py build --offline --today {today} --repo <dir>` commits every act that passed, through
   one deterministic `git fast-import` stream, then this documentation commit.
3. `python -X utf8 tools/check_repo.py --record`, `python -X utf8 tools/check_words.py <repo> --record`,
   `python -X utf8 tools/test_repo.py --record`, then build again so this file carries the record (same law commits).

## Counts

- Acts in force listed: {total}; built: {n_acts} ({n_amended} amended); left out: {total - n_acts}.
- Amending acts replayed: {sum(len(d['ams']) for d in datas)}; commits on main: {len(in_force) + 1} (one per act and amending
  act, {n_expired} expiries of temporary amendments, and this documentation commit); on `future`: {n_future}.

## Method

{NOTE_REPLAY}

The enacting clause of each act lists the provisions it replaces, inserts or repeals; the body gives the new text in the
same order, with omission markers for what it leaves alone. The provenance phrases ("sellaisena kuin se on laissa ...")
are skipped. If the grammar does not recognise a clause, if the body does not match what the clause names, or if the end
state differs from the consolidation in any section, the act is left out and listed in LEFT-OUT.csv with the reason.
Normalisation, applied to both sides of the comparison: {report_norm(here)}

## Dates and causes

Commit date: noon Helsinki on the in-force day (EET +0200; EEST +0300 from the last Sunday of March to the last Sunday of
October, `start <= day < end`, from 1981, when Finland introduced summer time). Commits on one day are seconds apart in
in-force order, up to 12:00:59 (on a day with more than 60 commits the rest share 12:00:59; their order is the
parent chain). In-force days come from Finlex's `amendedBy` list (an original's from the consolidation's `inForce`);
where Finlex gives none (older acts), from the act's own commencement sentence when it names one fixed day ("Tämä laki
tulee voimaan 1 päivänä heinäkuuta 1986."), or else from Finlex's own editorial note in the consolidation ("L 387/1964
tuli A:n 388/1964 mukaisesti voimaan 1.9.1964."), and the commit says so and quotes the note. An act that takes
effect "by decree" is dated by its enacting decree's own sentence ("... annettu laki (982/2002) tulee voimaan 27
päivänä joulukuuta 2002."), the decree found through Finlex's treaty reference database (`tools/decree_dates.json`); where none is found the act is left out. Enacted days come from the consolidation's commencement headings. Nothing is
attributed by date alone and no commit is by the publisher.

Never-amended acts whose consolidation carries no text (`contentAbsent`; Finlex lists no amending act for them) are
committed from the original alone, which is then the current text; nothing is compared for their final version. Acts
listed as in force that are themselves amending acts with no consolidation text are left out (their changes belong to
the act they amend).

## Decisions by hand and AI

No model wrote legal text or ran at build time. The code was written with Claude ({MODEL}) and is plain Python.
Hand decisions: the declared normalisation; the grammar's coverage; leaving out every act the replay cannot verify.

## Verification record

{ok_text or '(not yet recorded)'}
## Licence and prior art

See LICENSE. Prior art: LawVM (https://github.com/eliask/lawvm, MIT) and legalize-dev (https://github.com/legalize-dev) were
read for API behaviour; no code copied.
''', encoding='utf-8', newline='\n')

    tools = repo / 'tools'
    tools.mkdir(exist_ok=True)
    for n in PUBLISHED_TOOLS:
        src = here / ('repo/tools/' + n if n == 'finlex.py' else n)
        if src.exists():
            shutil.copyfile(src, tools / n)
    gate(repo)


def gate(repo: Path):
    """Every non-law file of the record (top-level files and tools/) goes through publish_gate; the act folders are
    law and are never gated."""
    paths = [p for p in repo.iterdir() if p.is_file()] + [p for p in sorted((repo / 'tools').rglob('*')) if p.is_file()]
    files = {p.relative_to(repo).as_posix(): p.read_bytes() for p in paths}
    for rel, data in publish_gate(files).items():
        (repo / rel).write_bytes(data)
