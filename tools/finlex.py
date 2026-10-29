"""Prints an act, or one section of it, as it stood on a given day, from this repository's history.

    python tools/finlex.py 2015-01-01 perustuslaki 10      # 10 § of the Constitution on 1 January 2015
    python tools/finlex.py 2010-01-01 perustuslaki         # the whole Constitution on 1 January 2010
    python tools/finlex.py 2020-01-01 <folder> 3:5         # 3 luku 5 § of an act numbered per chapter

Each commit carries the day its change took effect in an `In-force:` trailer. The text in force on a day is the last
commit on or before it. Versions between an act's original text and its official consolidation are replayed (see README). Standard library only.
"""
import datetime as dt
import re
import subprocess
import sys


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, encoding='utf-8', check=True).stdout


def order(path: str) -> tuple:
    name = path.split('/', 1)[1][:-3]
    if name == 'johdanto':
        return (0, 0, '', 0, '')
    m = re.fullmatch(r'(?:luku-(\d+)([a-z]*)-)?pykala-(\d+)([a-z]*)', name)
    if m:
        return (1, int(m.group(1) or 0), f'{len(m.group(2) or ""):02d}{m.group(2) or ""}', int(m.group(3)),
                f'{len(m.group(4)):02d}{m.group(4)}')
    return (2, 0, '', 0, name)


def file_for(act: str, key: str) -> str:
    """'10' -> pykala-010.md; '3:5' (acts numbered per chapter: 3 luku 5 §) -> luku-03-pykala-005.md."""
    m = re.fullmatch(r'(?:(\d+)([a-z]*):)?(\d+)([a-z]*)', key)
    if m:
        f = f'pykala-{int(m.group(3)):03d}{m.group(4)}.md'
        if m.group(1):
            f = f'luku-{int(m.group(1)):02d}{m.group(2)}-' + f
        return f
    return key if key.endswith('.md') else f'{key}.md'


def commit_on(day: str, act: str) -> str:
    branch = 'main'
    out = git('log', '--format=%H%x09%(trailers:key=In-force,valueonly,separator=)', branch, '--', act)
    for line in out.splitlines():
        h, _, d = line.partition('\t')
        if d.strip() and d.strip() <= day:
            return h
    return ''


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    day, act = sys.argv[1], sys.argv[2]
    dt.date.fromisoformat(day)
    commit = commit_on(day, act)
    if not commit:
        sys.exit(f'{act} is not in the record on {day}.')
    if len(sys.argv) > 3:
        name = file_for(act, sys.argv[3])
        try:
            sys.stdout.write(git('show', f'{commit}:{act}/{name}'))
        except subprocess.CalledProcessError:
            sys.exit(f'{act}/{name} was not in the text on {day}.')
        return
    files = [f for f in git('ls-tree', '-r', '--name-only', commit, f'{act}/').split('\n')
             if f.endswith('.md') and not f.endswith('/README.md')]
    for f in sorted(files, key=order):
        sys.stdout.write(git('show', f'{commit}:{f}') + '\n')


if __name__ == '__main__':
    main()
