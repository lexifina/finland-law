# Reproduce

Data retrieved 2026-10-29. Everything below is deterministic: the same data gives the same hashes.

## Steps

1. `python -X utf8 tools/enumerate_acts.py` lists the acts in force (`statute-consolidated/list?isInForce=true&typeStatute=act`,
   10 per page, both languages; the Finnish entries are kept with their consolidation version) into `acts.json`.
2. `python -X utf8 tools/build.py scan --prefetch` fetches, for each act, the consolidation
   (`.../akn/fi/act/statute-consolidated/<year>/<no>/fin@<version>`), the original (`.../statute/<year>/<no>/fin@`) and
   every amending act in the consolidation's `amendedBy` list (`.../statute/<year>/<no>/fin@`), replays and gates it,
   and writes `scan.json`. `--offline` reruns it from the cache.
3. `python -X utf8 tools/build.py build --offline --today 2026-10-29 --repo <dir>` commits every act that passed, through
   one deterministic `git fast-import` stream, then this documentation commit.
4. `python -X utf8 tools/check_repo.py --record`, `python -X utf8 tools/check_words.py <repo> --record`,
   `python -X utf8 tools/test_repo.py --record`, then build again so this file carries the record (same law commits).

## Fetch rate

4 parallel requests at most, each followed by a 0.25 s pause; HTTP 429/5xx retried with back-off. Every answer is cached on disk, 404s included. 

## Counts

- Acts in force listed: 6239; built: 1556 (443 amended); left out: 4683.
- Amending acts replayed: 1617; commits on main: 3153 (one per act and amending
  act, 3 expiries of temporary amendments, and this documentation commit); on `future`: 24.

## Method

Finlex serves only the CURRENT consolidation of a statute. The intermediate versions in this repository are therefore REPLAYED: the original statute is taken from the Statute Book and each amending act is applied, in in-force order, as the act's own enacting clause states (whole sections, headings, cross headings, subsections, introductory words, points and chapters; replacement, insertion and repeal; temporary amendments end the day after their last day in force). Only the end state of each act is checked against the official consolidation; the earlier versions are not independently confirmed.

The enacting clause of each act lists the provisions it replaces, inserts or repeals; the body gives the new text in the
same order, with omission markers for what it leaves alone. The provenance phrases ("sellaisena kuin se on laissa ...")
are skipped. If the grammar does not recognise a clause, if the body does not match what the clause names, or if the end
state differs from the consolidation in any section, the act is left out and listed in LEFT-OUT.csv with the reason.
Normalisation, applied to both sides of the comparison: (1) a trailing amendment label such as " (4.11.2011/1112)" on a heading is dropped (Finlex adds it to the consolidation); (2) U+2015 and U+2014 (dashes) are read as U+2013; (3) the original's line-break hyphen in "valtio-neuvoston" (731/1999 131 §) is read as "valtioneuvoston", as the consolidation prints it; (4) the chapter line is not compared for sections the original prints outside any chapter (e.g. 130 § and 131 § of 731/1999, which the consolidation files under chapter 13); (5) Finlex's editorial notes in the consolidation (noteAuthorial) are not part of the text and are not compared, nor are the correction notes in some originals (authorialNote, "Merkitty kohta oikaistu ...", which give the wording before a published correction; the corrected wording is the text); (6) Finlex's repeal markers in the consolidation ("3 momentti on kumottu L:lla 29.8.2008/562.", "4 § on kumottu L:lla ...") are editorial notes, not text: the replay removes the provision itself (a repealed subsection keeps its place, so later subsections keep their numbers); (7) a heading's final full stop, which older originals print ("Lain soveltamisala.") and the consolidation drops, is not compared, and a chapter number printed bare in an older original ("1") is read as the consolidation prints it ("1 luku"); (8) spacing before the section sign ("3§" in older originals, "3 §" in the consolidation) and after it ("57 §: n", "57 §:n") is not compared; (9) a hyphen between two numbers ("3-5 §") is read as the en dash the consolidation prints ("3–5 §").

## Dates and causes

Commit date: noon Helsinki on the in-force day (EET +0200; EEST +0300 from the last Sunday of March to the last Sunday of
October, `start <= day < end`, from 1981, when Finland introduced summer time). Commits on one day are seconds apart in
in-force order. In-force days come from Finlex's `amendedBy` list (an original's from the consolidation's `inForce`);
where Finlex gives none (older acts), from the act's own commencement sentence when it names one fixed day ("Tämä laki
tulee voimaan 1 päivänä heinäkuuta 1986."), and the commit says so; acts that take effect "by decree" on a day the
data does not give are left out. Enacted days come from the consolidation's commencement headings. Nothing is
attributed by date alone and no commit is by the publisher.

Never-amended acts whose consolidation carries no text (`contentAbsent`; Finlex lists no amending act for them) are
committed from the original alone, which is then the current text; nothing is compared for their final version. Acts
listed as in force that are themselves amending acts with no consolidation text are left out (their changes belong to
the act they amend).

## Decisions by hand and AI

No model wrote legal text or ran at build time. The code was written with Claude (claude-opus-5-5 (Claude Agent SDK)) and is plain Python.
Hand decisions: the declared normalisation; the grammar's coverage; leaving out every act the replay cannot verify.

## Verification record

- check_repo.py: final version vs consolidation of 2018-10-05: 131 sections, 0 mismatches, word check equal; first version vs original statute: equal; 4 amending acts checked against their own text; 0 problems
- check_words.py: not recorded
- test_repo.py: 6/6 passed

## Licence and prior art

See LICENSE. Prior art: LawVM (https://github.com/eliask/lawvm, MIT) and legalize-dev (https://github.com/legalize-dev) were
read for API behaviour; no code copied.
