"""Lists every IN-FORCE consolidated act (laki, typeStatute=act) in Finnish, with its current consolidation version from the Finlex open-data list endpoint.

    python -X utf8 enumerate_acts.py [--offline]

(the filter langAndVersion=fin@ matches only UNVERSIONED consolidations, i.e. acts never amended, so the list is read
for both languages and the Finnish entries kept). Writes acts.json: [{"year": 1999, "no": "731", "version": "20180817"}, ...] in the API's order (year, number). One request at a time
(page size is capped at 10 by the API); pages are cached through build.fetch.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as B  # noqa: E402

LIST = (B.API + '/statute-consolidated/list?format=json&limit=10&page={page}&isInForce=true'
        '&typeStatute=act&sortBy=number')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--offline', action='store_true')
    args = ap.parse_args()
    out, seen, page = [], {}, 1
    while True:
        body = B.fetch(LIST.format(page=page), args.offline)
        rows = json.loads(body)
        for r in rows:
            m = re.search(r'/statute-consolidated/(\d{4})/([0-9a-z-]+)/fin@(\w*)$', r['akn_uri'])
            if not m:
                continue  # the Swedish version
            k = (int(m.group(1)), m.group(2))
            if k not in seen:
                seen[k] = {'year': k[0], 'no': k[1], 'version': m.group(3)}
                out.append(seen[k])
            elif m.group(3) > seen[k]['version']:  # listed twice: keep the latest consolidation version
                seen[k]['version'] = m.group(3)
        if page % 50 == 0:
            print(page, len(out), flush=True)
        if len(rows) < 10:
            break
        page += 1
    (B.HERE / 'acts.json').write_text(json.dumps(out, indent=0, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    print('acts', len(out), 'pages', page)


if __name__ == '__main__':
    main()
