# Finnish law, change by change

1556 Finnish acts in force (lait), as a git repository: the Constitution (Suomen perustuslaki, 731/1999) and every
other act Finlex consolidates whose history the replay can reproduce. Each amending act is one commit, credited to that
act and dated the day it took effect, at noon in Helsinki. Text is Finnish.

**The intermediate versions are replayed. Only each act's end state is checked against an official consolidation.**
Finlex serves only the CURRENT consolidation of a statute. The intermediate versions in this repository are therefore REPLAYED: the original statute is taken from the Statute Book and each amending act is applied, in in-force order, as the act's own enacting clause states (whole sections, headings, cross headings, subsections, introductory words, points and chapters; replacement, insertion and repeal; temporary amendments end the day after their last day in force). Only the end state of each act is checked against the official consolidation; the earlier versions are not independently confirmed. The method is the same as the US Code repository: replay, then check the result against the official text.

| | |
| --- | --- |
| Acts in force that Finlex consolidates | 6239 |
| Acts here | 1556 (443 amended, end state verified against the consolidation; 1113 never amended, of them 721 whose consolidation carries no text, so the original is not compared with one) |
| Acts left out | 4683 ([`LEFT-OUT.csv`](LEFT-OUT.csv)) |
| Amendment commits on `main` | 1603 (of them 3 ends of temporary amendments) |
| Commits on the never-merged `future` branch | 24 |
| First in-force day | 1954-01-01 |

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
consolidation for every section, after this normalisation: (1) a trailing amendment label such as " (4.11.2011/1112)" on a heading is dropped (Finlex adds it to the consolidation); (2) U+2015 and U+2014 (dashes) are read as U+2013; (3) the original's line-break hyphen in "valtio-neuvoston" (731/1999 131 §) is read as "valtioneuvoston", as the consolidation prints it; (4) the chapter line is not compared for sections the original prints outside any chapter (e.g. 130 § and 131 § of 731/1999, which the consolidation files under chapter 13); (5) Finlex's editorial notes in the consolidation (noteAuthorial) are not part of the text and are not compared, nor are the correction notes in some originals (authorialNote, "Merkitty kohta oikaistu ...", which give the wording before a published correction; the corrected wording is the text); (6) Finlex's repeal markers in the consolidation ("3 momentti on kumottu L:lla 29.8.2008/562.", "4 § on kumottu L:lla ...") are editorial notes, not text: the replay removes the provision itself (a repealed subsection keeps its place, so later subsections keep their numbers); (7) a heading's final full stop, which older originals print ("Lain soveltamisala.") and the consolidation drops, is not compared, and a chapter number printed bare in an older original ("1") is read as the consolidation prints it ("1 luku"); (8) spacing before the section sign ("3§" in older originals, "3 §" in the consolidation) and after it ("57 §: n", "57 §:n") is not compared; (9) a hyphen between two numbers ("3-5 §") is read as the en dash the consolidation prints ("3–5 §"). Only acts that pass are committed.
`tools/check_repo.py` then reads every act's versions back from git and compares the last with the consolidation and the
first with the original statute; `tools/check_words.py` compares the letters and digits of every act's last version
with the consolidation's raw XML text, read by a separate minimal extractor; `tools/test_repo.py` checks that the
commands above work. See [`REPRODUCE.md`](REPRODUCE.md).
- check_repo.py: final version vs consolidation of 2018-10-05: 131 sections, 0 mismatches, word check equal; first version vs original statute: equal; 4 amending acts checked against their own text; 0 problems
- check_words.py: not recorded
- test_repo.py: 6/6 passed

## Files

`<folder>/pykala-NNN.md` is one section (pykälä); the `#` line is its number and heading, then `Osa:` its part, `Luku:`
its chapter and `Väliotsikko:` the cross heading printed before it, when it has them; each subsection (momentti) is a
paragraph block, each line one paragraph or point. Acts that number sections per chapter use
`luku-NN-pykala-NNN.md`. `johdanto.md` is the title and enacting words. A repealed section is removed. A folder is the
act's one-word name (`vaalilaki`) or `sd-<year>-<number>`; the Constitution keeps `perustuslaki`.

## What it does not show

- Versions Finlex does not publish: only the current consolidation exists; the rest is replayed.
- Acts left out (4683), by reason:

| Reason | Acts |
| --- | --- |
| no text in the open data (neither the original nor the consolidation) | 3231 |
| an enacting clause or body the replay grammar does not cover | 405 |
| an amending act listed as in force (its changes belong to the act it amends) | 386 |
| replay ran but the end state differs from the consolidation | 239 |
| no in-force day for the original (in force by decree on a day the data does not give) | 211 |
| an amending act has no in-force day (older amendments, often with no text in the open data) | 76 |
| the consolidation has no text, so an amended act cannot be verified | 58 |
| the original statute has no text in the open data | 49 |
| temporary amendments the replay does not resolve | 27 |
| an amending act is not in the open data | 1 |

- Vaalilaki (714/1998) is left out: 247/2002: 9 §: body has 2 subsections, clause names momentti [1, 3, 4].
- Amendments Finlex does not list in `amendedBy`: the end-state check would fail if one were missing.
- Decrees (asetukset) and acts no longer in force.

## Licence

Finlex open data, CC BY 4.0: see [`LICENSE`](LICENSE) for the exact words and attribution. Tools, history and tables: CC0.

## Source

Finlex open data interface (Oikeusministeriö), https://opendata.finlex.fi, Akoma Ntoso. API quirks were learned from
LawVM (https://github.com/eliask/lawvm, MIT) and legalize-dev (https://github.com/legalize-dev); no code was copied.
