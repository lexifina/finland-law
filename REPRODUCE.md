# Reproduce

Data retrieved 2026-10-29. Everything below is deterministic: the same data gives the same hashes.

## Steps

The sources are Finlex open data answers (Akoma Ntoso XML): for each act in force listed in `tools/acts.json` (Finlex's
list of consolidated acts in force, with each act's consolidation version), its current consolidation
(`statute-consolidated/<year>/<no>/fin@<version>`), its original statute (`statute/<year>/<no>/fin@`) and every amending
act in the consolidation's `amendedBy` list (`statute/<year>/<no>/fin@`), all under
`https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act`. The build reads each answer from a file named by the sha1
of its URL plus `.bin` (an answer of 404 is the bytes `\x00404`), in the folder `FINLEX_SOURCES`. How the answers were
retrieved is not part of this record.

1. With `tools/acts.json` and the retrieved answers in place, `python -X utf8 tools/build.py scan --offline` replays and
   gates every act and writes `scan.json`.
2. `python -X utf8 tools/build.py build --offline --today 2026-10-29 --repo <dir>` commits every act that passed, through
   one deterministic `git fast-import` stream, then this documentation commit.
3. `python -X utf8 tools/check_repo.py --record`, `python -X utf8 tools/check_words.py <repo> --record`,
   `python -X utf8 tools/test_repo.py --record`, then build again so this file carries the record (same law commits).

## Counts

- Acts in force listed: 6239; built: 1757 (535 amended); left out: 4482.
- Amending acts replayed: 2167; commits on main: 3895 (one per act and amending
  act, 4 expiries of temporary amendments, and this documentation commit); on `future`: 34.

## Method

Finlex serves only the CURRENT consolidation of a statute. The intermediate versions in this repository are therefore REPLAYED: the original statute is taken from the Statute Book and each amending act is applied, in in-force order, as the act's own enacting clause states (whole sections, headings, cross headings, subsections, introductory words, points and chapters; replacement, insertion and repeal; temporary amendments end the day after their last day in force). Only the end state of each act is checked against the official consolidation; the earlier versions are not independently confirmed.

The enacting clause of each act lists the provisions it replaces, inserts or repeals; the body gives the new text in the
same order, with omission markers for what it leaves alone. The provenance phrases ("sellaisena kuin se on laissa ...")
are skipped. If the grammar does not recognise a clause, if the body does not match what the clause names, or if the end
state differs from the consolidation in any section, the act is left out and listed in LEFT-OUT.csv with the reason.
Normalisation, applied to both sides of the comparison: (1) a trailing amendment label such as " (4.11.2011/1112)" on a heading is dropped (Finlex adds it to the consolidation); (2) U+2015 and U+2014 (dashes) are read as U+2013; (3) the original's line-break hyphen in "valtio-neuvoston" (731/1999 131 §) is read as "valtioneuvoston", as the consolidation prints it; (4) the chapter line is not compared for sections the original prints outside any chapter (e.g. 130 § and 131 § of 731/1999, which the consolidation files under chapter 13); (5) Finlex's editorial notes in the consolidation (noteAuthorial) are not part of the text and are not compared, nor are the correction notes in some originals (authorialNote, "Merkitty kohta oikaistu ...", which give the wording before a published correction; the corrected wording is the text); (6) Finlex's repeal markers in the consolidation ("3 momentti on kumottu L:lla 29.8.2008/562.", "4 § on kumottu L:lla ...") are editorial notes, not text: the replay removes the provision itself (a repealed subsection keeps its place, so later subsections keep their numbers); (7) a heading's final full stop, which older originals print ("Lain soveltamisala.") and the consolidation drops, is not compared, and a chapter number printed bare in an older original ("1") is read as the consolidation prints it ("1 luku"); (8) spacing before the section sign ("3§" in older originals, "3 §" in the consolidation) and after it ("57 §: n", "57 §:n") is not compared; (9) a hyphen between two numbers ("3-5 §") is read as the en dash the consolidation prints ("3–5 §"); (10) Finlex's amendment labels inside a line ("oloista: (10.8.2018/678)") are dropped, as on headings; (11) a hyphen inside a word, with or without a space after it, is not compared: line-break hyphenation left in one source ("rautatieyrityk-sellä", "toimeksi- antosopimus", "ar-vioidusta"); (12) how a section's lines are divided into subsections is not compared (some texts print two momentti as one subsection with two paragraphs, or one momentti as two); the lines themselves and their order are.

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

No model wrote legal text or ran at build time. The code was written with Claude (claude-opus-5-5 (Claude Agent SDK)) and is plain Python.
Hand decisions: the declared normalisation; the grammar's coverage; leaving out every act the replay cannot verify.

## Verification record

- check_repo.py: 1757 acts, 19996 files at the tip of future: final versions equal the consolidations (796 never-amended acts have no consolidation text to compare), first versions equal the original statutes, commit days follow amendedBy; 3928 commits, 0 merges, 0 backwards dates, 0 CRs; 0 problems
- check_words.py: 1757 acts, 2167 amending commits, 2678226 source words checked (796 never-amended acts whose consolidation has no text: first version only; 1 commits by new laws repealing part of an act: removals only); 10 problems
- test_repo.py: 7/7 passed

## Licence and prior art

See LICENSE. Prior art: LawVM (https://github.com/eliask/lawvm, MIT) and legalize-dev (https://github.com/legalize-dev) were
read for API behaviour; no code copied.
