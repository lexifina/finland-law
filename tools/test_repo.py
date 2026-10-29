"""Functional smoke test of the built Finnish law repository: the things a reader does with it work. Standard library only.

    python -X utf8 scripts/finland-law/test_repo.py [--repo ../finland-law] [--record]
    python -X utf8 tools/test_repo.py                   # from inside the repository

It asserts that:
1. `git blame` on a changed provision names an amending act as author of its lines;
2. tools/finlex.py gives the text before and after a known amendment, and each equals the file at that day's tag;
3. `git log --follow` on a file lists its amending acts in the order they took effect;
4. every tag resolves to a commit, and every commit but the documentation commit has an `In-force:` trailer;
5. no commit is authored by Lexifina except the documentation commit (the last one);
6. every git or tool command in the README's table runs and prints something.
With --record the result is written to verification.json next to this script (for REPRODUCE.md).
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAW = re.compile(r'^\d+/\d{4}$')
PUBLISHER = 'Finlex (Oikeusministeriö)'


def default_repo() -> Path:
    if (HERE.parent / '.git').exists():
        return HERE.parent
    return HERE.parents[2] / 'finland-law-build'


class Smoke:
    def __init__(self, repo: Path):
        self.repo = repo
        self.results: list[tuple[str, bool, str]] = []

    def git(self, *args: str) -> str:
        out = subprocess.run(['git', *args], cwd=self.repo, capture_output=True, text=True, encoding='utf-8')
        if out.returncode:
            raise AssertionError(f'git {" ".join(args)}: {out.stderr.strip()}')
        return out.stdout

    def tool(self, *args: str) -> str:
        out = subprocess.run([sys.executable, 'tools/finlex.py', *args], cwd=self.repo, capture_output=True, text=True,
                             encoding='utf-8')
        if out.returncode:
            raise AssertionError(f'finlex {" ".join(args)}: {out.stderr.strip()}')
        return out.stdout

    def check(self, name: str, fn):
        try:
            detail = fn() or ''
            self.results.append((name, True, detail))
            print(f'PASS {name}: {detail}')
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            print(f'FAIL {name}: {exc}')

    # 1
    def blame(self):
        out = self.git('blame', '--line-porcelain', 'main', '--', 'perustuslaki/pykala-010.md')
        authors = {l[7:] for l in out.splitlines() if l.startswith('author ')}
        assert authors, 'no blame output'
        assert all(LAW.match(a) or a == PUBLISHER for a in authors), f'unexpected authors {authors}'
        assert '817/2018' in authors, f'817/2018 not among the authors {authors}'
        assert len(authors) >= 2, f'only {authors}'
        return f'perustuslaki/pykala-010.md lines by {", ".join(sorted(authors))}'

    # 2
    def on_date(self):
        done = []
        for act, prov, path, tag, before, after in [
                ('perustuslaki', '10', 'perustuslaki/pykala-010.md', 'perustuslaki/2018-817', '2018-10-14', '2018-10-15'),
                ('perustuslaki', '53', 'perustuslaki/pykala-053.md', 'perustuslaki/2011-1112', '2012-02-29', '2012-03-01')]:
            a, b = self.tool(before, act, prov), self.tool(after, act, prov)
            assert a and b and a != b, f'{path}: no change between {before} and {after}'
            assert b == self.git('show', f'{tag}:{path}'), f'{path} on {after} is not the file at {tag}'
            assert a == self.git('show', f'{tag}~1:{path}'), f'{path} on {before} is not the file before {tag}'
            done.append(f'{path} {before} != {after}')
        whole = self.tool('2000-03-01', 'perustuslaki')
        assert '# 10 §' in whole, 'whole Constitution on 2000-03-01 lacks 10 §'
        return '; '.join(done)

    # 2b: the same across the corpus, on a deterministic sample of amending acts (every k-th row of amendments.csv)
    def on_date_sample(self):
        import csv
        import datetime as dt
        built = json.loads((self.repo / 'corpus.json').read_text(encoding='utf-8')).get('built', '')
        with open(self.repo / 'amendments.csv', encoding='utf-8', newline='') as f:
            # amendments in force after the build day are on the never-merged `future` branch, which the on-date
            # tool does not read (README): the sample is drawn from main
            rows = [r for r in csv.DictReader(f) if not r['expires'] and (not built or r['in_force'] <= built)]
        if not rows:
            return 'no amending acts'
        step = max(1, len(rows) // 25)
        n = 0
        for r in rows[::step]:
            no, y = r['act'].split('/')
            tag = f'{r["folder"]}/{y}-{no}'
            day = r['in_force']
            # the version of each file this act modified, on its in-force day and the day before; skipped when another
            # commit touches the same file on the same day (the tool gives the day's last version)
            names = [p for p in self.git('diff-tree', '--no-commit-id', '--name-only', '--diff-filter=M', '-r', tag,
                                         '--', r['folder'] + '/').split() if p.endswith('.md') and 'pykala' in p]
            for path in names[:1]:
                same_day = [l for l in self.git('log', '--format=%(trailers:key=In-force,valueonly,separator=)',
                                                'main', '--', path).split() if l == day]
                if len(same_day) > 1:
                    continue
                m = re.search(r'(?:luku-0*(\d+[a-z]*)-)?pykala-0*(\d+[a-z]*)\.md$', path)
                prov = (m.group(1) + ':' if m.group(1) else '') + m.group(2)
                before = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
                a, b = self.tool(before, r['folder'], prov), self.tool(day, r['folder'], prov)
                assert b == self.git('show', f'{tag}:{path}'), f'{path} on {day} is not the file at {tag}'
                prev = self.git('log', '-1', '--format=%H', f'{tag}~1', '--', path).strip()
                assert a == self.git('show', f'{prev}:{path}'), f'{path} on {before} is not its previous version'
                n += 1
        assert n, 'no sample checked'
        return f'{n} sampled amending acts: the tool gives the file at the tag and the version before'

    # 3
    def follow(self):
        checked = []
        for path in ('perustuslaki/pykala-010.md', 'perustuslaki/pykala-053.md', 'perustuslaki/pykala-090.md'):
            out = self.git('log', '--follow', '--format=%an%x09%(trailers:key=In-force,valueonly,separator=)', 'main', '--', path)
            rows = [l.split('\t') for l in out.splitlines() if l]
            assert len(rows) >= 2, f'{path}: {len(rows)} commits'
            days = [d.strip() for _, d in rows]
            assert days == sorted(days, reverse=True), f'{path}: not in date order {days}'
            assert all(LAW.match(a) or a == PUBLISHER for a, _ in rows), f'{path}: authors {[a for a, _ in rows]}'
            assert sum(1 for a, _ in rows if LAW.match(a)) >= 2, f'{path}: fewer than two acts'
            checked.append(f'{path} {len(rows)} commits')
        return '; '.join(checked)

    # 4
    def tags_and_trailers(self):
        tags = self.git('tag').split()
        assert tags, 'no tags'
        for t in tags:
            self.git('rev-parse', '--verify', '-q', f'{t}^{{commit}}')
        rows = self.git('log', '--format=%H%x09%an%x09%(trailers:key=In-force,valueonly,separator=)', 'main').splitlines()
        missing = [r for r in rows if not r.split('\t')[2].strip()]
        assert not missing, f'{len(missing)} commits without In-force, e.g. {missing[:2]}'
        bad = [r for r in rows if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', r.split('\t')[2].strip())]
        assert not bad, f'malformed In-force {bad[:2]}'
        return f'{len(tags)} tags resolve; {len(rows) - 1} law commits and the documentation commit carry In-force'

    # 5
    def lexifina(self):
        rows = self.git('log', '--format=%H%x09%an%x09%ae%x09%cn', 'main').splitlines()
        by_lex = [r for r in rows if r.split('\t')[1] == 'Lexifina']
        assert len(by_lex) == 1 and by_lex[0] == rows[0], f'{len(by_lex)} commits by Lexifina'
        assert all(r.split('\t')[3] == 'Lexifina' for r in rows), 'a committer other than Lexifina'
        authors = {r.split('\t')[1] for r in rows[1:]}
        assert all(LAW.match(a) or a == PUBLISHER for a in authors), f'unexpected authors {authors - {PUBLISHER}}'
        return f'only the documentation commit {rows[0][:10]} is by Lexifina; {len(authors)} distinct authors'

    # 6
    def readme(self):
        text = (self.repo / 'README.md').read_text(encoding='utf-8')
        table = text[text.index('## The questions'):text.index('## The dates')]
        cmds = [c for c in re.findall(r'`([^`]+)`', table) if c.startswith(('git ', 'python '))]
        assert len(cmds) >= 7, f'only {len(cmds)} commands found'
        for c in cmds:
            args = shlex.split(c)
            if args[0] == 'python':
                args[0] = sys.executable
            out = subprocess.run(args, cwd=self.repo, capture_output=True, text=True, encoding='utf-8')
            assert out.returncode == 0, f'{c}: exit {out.returncode} {out.stderr.strip()[:200]}'
            assert out.stdout.strip(), f'{c}: printed nothing'
        return f'{len(cmds)} README commands ran and printed output'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=None)
    ap.add_argument('--record', action='store_true')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    s = Smoke(Path(args.repo) if args.repo else default_repo())
    s.check('blame names an act', s.blame)
    s.check('text before and after an amendment', s.on_date)
    s.check('text before and after, sampled across acts', s.on_date_sample)
    s.check('log --follow in date order', s.follow)
    s.check('tags resolve, In-force on every law commit', s.tags_and_trailers)
    s.check('only the documentation commit is by Lexifina', s.lexifina)
    s.check('README commands run', s.readme)
    ok = all(r[1] for r in s.results)
    print(f'{sum(r[1] for r in s.results)}/{len(s.results)} passed')
    if args.record:
        p = HERE / 'verification.json'
        rec = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        rec['smoke'] = {'passed': sum(r[1] for r in s.results), 'total': len(s.results),
                        'results': [{'test': n, 'ok': o, 'detail': d} for n, o, d in s.results]}
        p.write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
