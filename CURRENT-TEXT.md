# Current-text acts

Most acts in this record are replayed: their original text from the Statute Book, with each amending act applied, and
the end state checked against Finlex's current consolidation (README.md, REPRODUCE.md).

For the acts the replay cannot verify, the record holds their **current text only**: the Finnish consolidation that
Finlex serves today (`statute-consolidated/<year>/<number>/fin@<version>` in the open data), one commit per act with
`Kind: baseline`, credited to Finlex. Their earlier versions are not in the record. Each is dated by the day its newest
wording took effect: the in-force day Finlex gives for the latest amending act it lists (an act it lists no amending
act for is its original, dated by its own in-force day); `In-force-basis:` says which. The files have the same layout as
the replayed acts' (`johdanto.md`, `pykala-NNN.md`, `luku-NN-pykala-NNN.md`), rendered by the same parser
(tools/replay.py `parse_act`, tools/build.py `files_of`); Finlex's amendment labels such as "(29.4.1994/318)" are left
out, as everywhere in this record.

Check: every word of the consolidation (letters and digits, casefolded; Finlex's editorial notes and repeal markers
excluded, as tools/check_words.py reads them) is in the act's files, in the same order, and nothing else is; this is
read back from git after the commits are written. The code is tools/current_text.py.

Not here: acts whose consolidation carries no text in the open data (most acts from before 1990), amending acts listed
as in force, and consolidations that already print an amendment not yet in force (they join once that day has passed).
LEFT-OUT.csv lists every act in force that the record does not hold, with the reason.

Appended so far: 542 acts, the last batch on 2026-11-06.
