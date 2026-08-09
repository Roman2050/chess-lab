# Third-Party Notices

Chess Lab is licensed under `GPL-3.0-or-later`. The components and data listed
below remain subject to their own copyright and license terms.

## python-chess

- Locked distributions: `python-chess` 1.999 (meta-package) and `chess` 1.11.2
- Copyright: the python-chess authors and contributors
- License: GNU General Public License version 3 or later
  (`GPL-3.0-or-later`)
- Source release: <https://github.com/niklasf/python-chess/releases/tag/v1.11.2>
- Exact source commit:
  <https://github.com/niklasf/python-chess/tree/3516d7c6c0879af724c2855fac5a304a4ef40949>
- License text:
  <https://github.com/niklasf/python-chess/blob/3516d7c6c0879af724c2855fac5a304a4ef40949/LICENSE.txt>

The direct `python-chess` dependency resolves through its meta-package to the
`chess` implementation recorded in `uv.lock`. The complete GPL version 3 text is
also included in this distribution as `LICENSE`.

## Stockfish

- Version: Stockfish 18
- Release tag: `sf_18`
- Exact source commit: `cb3d4ee9b47d0c5aae855b12379378ea1439675c`
- Copyright: 2004-2026 the Stockfish developers (see upstream `AUTHORS`)
- License: GNU General Public License version 3 or later
  (`GPL-3.0-or-later`)
- Release: <https://github.com/official-stockfish/Stockfish/releases/tag/sf_18>
- Corresponding source:
  <https://github.com/official-stockfish/Stockfish/tree/cb3d4ee9b47d0c5aae855b12379378ea1439675c>
- Source archive:
  <https://github.com/official-stockfish/Stockfish/archive/cb3d4ee9b47d0c5aae855b12379378ea1439675c.tar.gz>
- Upstream license:
  <https://github.com/official-stockfish/Stockfish/blob/cb3d4ee9b47d0c5aae855b12379378ea1439675c/Copying.txt>

The Python wheel and source archive do not bundle a Stockfish executable. When a
Chess Lab container includes Stockfish, its supported production contract is an
unmodified Linux x86-64 binary built from the exact source commit above. The
container build must retain this notice and the complete GPL text, and its build
recipe must be sufficient to reproduce the distributed binary.

## lichess-org/chess-openings

- Dataset revision: `4b8622759e7ae6f93f011cc6c83a3823401ab45e`
- Copyright: the lichess-org/chess-openings contributors
- License/dedication: CC0 1.0 Universal (`CC0-1.0`)
- Exact source:
  <https://github.com/lichess-org/chess-openings/tree/4b8622759e7ae6f93f011cc6c83a3823401ab45e>
- Legal code:
  <https://github.com/lichess-org/chess-openings/blob/4b8622759e7ae6f93f011cc6c83a3823401ab45e/COPYING.txt>

`scripts/download_eco.py` downloads the five upstream TSV files at that exact
revision and generates the ignored local `data/eco.json` artifact.

## Independence

Chess Lab is an independent project. It is not affiliated with, endorsed by, or
sponsored by Lichess, the Stockfish project, or their contributors. Their names are
used only to identify the upstream software and data described above.
