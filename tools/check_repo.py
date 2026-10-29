"""Checks the built repository against the official texts (read from the builder's cache, so it is offline).

    python -X utf8 check_repo.py [--repo DIR] [--record]

For EVERY act in acts.csv:
1. FINAL version (the act's folder at the tip of `future`, else `main`): every section file equals the official
   consolidation, after the declared normalisation, file by file; no file missing or extra.
2. FIRST version (the act's base tag): every file equals the original statute as printed in the Statute Book.
3. Its commits, in order, carry the in-force days Finlex lists (`amendedBy`; where it lists none, the day the act's own
   commencement provision names), authored by the act or amending act.
Whole repository: 0 merges, 0 CRs in messages, In-force on every commit, author dates never go backwards, no commit
on `main` dated after the build day. Intermediate versions are replayed; only 1 is a check against an official
consolidation. Blobs are read through `git cat-file --batch`.
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build as B  # noqa: E402
import replay as R  # noqa: E402


def git(repo, *a, inp=None):
    r = subprocess.run(['git', *a], cwd=repo, capture_output=True, input=inp)
    if r.returncode:
        raise SystemExit(f'git {a}: {r.stderr.decode()}')
    return r.stdout


def tree_files(repo, rev):
    """{path: blob sha} for every file at rev."""
    out = {}
    for line in git(repo, 'ls-tree', '-r', '-z', rev).split(b'\0'):
        if line:
            meta, path = line.split(b'\t', 1)
            out[path.decode('utf-8')] = meta.split()[2].decode()
    return out


def read_blobs(repo, shas):
    shas = sorted(set(shas))
    p = subprocess.run(['git', 'cat-file', '--batch'], cwd=repo, input=''.join(s + '\n' for s in shas).encode(),
                       capture_output=True)
    out, buf, i = {}, p.stdout, 0
    for s in shas:
        j = buf.index(b'\n', i)
        size = int(buf[i:j].split()[2])
        out[s] = buf[j + 1:j + 1 + size].decode('utf-8')
        i = j + 1 + size + 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'finland-law-build'))
    ap.add_argument('--record', action='store_true')
    ap.add_argument('--today', default=None, help='the build day (default: corpus.json "built")')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    repo = a.repo
    if a.today is None:
        a.today = json.loads((Path(repo) / 'corpus.json').read_text(encoding='utf-8'))['built']
    problems = []
    acts = list(csv.DictReader((Path(repo) / 'acts.csv').open(encoding='utf-8')))
    refs = git(repo, 'for-each-ref', '--format=%(refname)').decode().split()
    tip = 'future' if 'refs/heads/future' in refs else 'main'
    final_tree = tree_files(repo, tip)
    # global
    log = git(repo, 'log', '--reverse', '--format=%H%x09%an%x09%ad%x09%P%x09%(trailers:key=In-force,valueonly,separator=)',
              '--date=unix', tip).decode('utf-8').splitlines()
    rows = [l.split('\t') for l in log]
    merges = sum(1 for r in rows if len(r[3].split()) > 1)
    if merges:
        problems.append(f'{merges} merges')
    no_if = [r[0] for r in rows if not r[4].strip()]
    if no_if:
        problems.append(f'{len(no_if)} commits without In-force')
    back = sum(1 for x, y in zip(rows, rows[1:]) if int(y[2]) < int(x[2]))
    if back:
        problems.append(f'{back} commits dated before their parent')
    main_days = git(repo, 'log', '--format=%(trailers:key=In-force,valueonly,separator=)', 'main').decode().split()
    if any(d > a.today for d in main_days if d):
        problems.append('a commit on main is in force after the build day')
    crs = git(repo, 'log', '--all', '--format=%B').count(b'\r')
    if crs:
        problems.append(f'{crs} CRs in commit messages')
    # per act
    n_files = 0
    by_folder = {}
    for p, sha in final_tree.items():
        if '/' in p:
            by_folder.setdefault(p.split('/', 1)[0], {})[p.split('/', 1)[1]] = sha
    want_blobs = []
    expected = {}
    first_trees = {}
    no_cons = [0]
    listed = {(x['year'], x['no']): x for x in B.all_acts()}
    for row in acts:
        no, year = row['act'].split('/')
        cfg = {'year': int(year), 'no': no, 'version': ''}
        scan_cfg = listed.get((cfg['year'], no), cfg)
        data = B.load_act(scan_cfg, offline=True)
        events, free = B.replay_all(data, True, a.today)
        final = events[-1][3]
        if B.cons_absent(data):  # never amended, and the consolidation has no text: the first version is the check
            no_cons[0] += 1
        else:
            cons = R.parse_act(data['cons'], notes_ok=True)
            cons['scoped'] = final.get('scoped', False)
            A, C = B.canon(final, free), B.canon(cons, free)
            if A != C:
                problems.append(f'{row["act"]}: replay differs from the consolidation')
        exp = B.files_of(final, B.intro_of(data['base']))
        expected[row['folder']] = exp
        want_blobs += list(by_folder.get(row['folder'], {}).values())
        # first version
        tag = f'{row["folder"]}/{year}-{no}'
        ft = {p.split('/', 1)[1]: s for p, s in tree_files(repo, tag).items() if p.startswith(row['folder'] + '/')}
        first_trees[row['folder']] = (ft, B.files_of(R.parse_act(data['base']), B.intro_of(data['base'])))
        want_blobs += list(ft.values())
        # dates and authors
        flog = git(repo, 'log', '--reverse', '--format=%an%x09%(trailers:key=In-force,valueonly,separator=)', tip, '--',
                   row['folder'] + '/').decode('utf-8').splitlines()
        got = [tuple(x.split('\t')) for x in flog]
        exp_seq = [(f'{no}/{year}', data['base_inforce'])]
        for day, kind, j, _st, _k in events[1:]:
            am = data['ams'][j]
            exp_seq.append((f'{am["no"]}/{am["year"]}', day))
        # commits whose replay changed no file leave no trace in the folder log: compare as subsequences
        it = iter(exp_seq)
        if not all(any(g == e for e in it) for g in got) or (got and got[0] != exp_seq[0]):
            problems.append(f'{row["act"]}: commit authors/days {got[:3]} do not follow amendedBy {exp_seq[:3]}')
    blobs = read_blobs(repo, want_blobs)
    for row in acts:
        f = row['folder']
        have = {n: blobs[s] for n, s in by_folder.get(f, {}).items()}
        exp = expected[f]
        for n in sorted(set(have) | set(exp)):
            if have.get(n) != exp.get(n):
                problems.append(f'{f}/{n}: final file differs from the consolidation replay')
        n_files += len(have)
        ft, fexp = first_trees[f]
        for n in sorted(set(ft) | set(fexp)):
            if blobs.get(ft.get(n, ''), None) != fexp.get(n):
                problems.append(f'{f}/{n}: first version differs from the original statute')
    summary = (f'{len(acts)} acts, {n_files} files at the tip of {tip}: final versions equal the consolidations ({no_cons[0]} never-amended '
               f'acts have no consolidation text to compare), first versions equal the original statutes, commit days follow amendedBy; {len(rows)} commits, {merges} merges, '
               f'{back} backwards dates, {crs} CRs; {len(problems)} problems')
    print(summary)
    for p in problems[:50]:
        print('  ', p)
    if a.record:
        vp = HERE / 'verification.json'
        rec = json.loads(vp.read_text(encoding='utf-8')) if vp.exists() else {}
        rec['check'] = {'summary': summary, 'problems': problems[:200]}
        vp.write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8',
                      newline='\n')
    raise SystemExit(1 if problems else 0)


if __name__ == '__main__':
    main()
