# Third-party notices

The root `LICENSE` applies to ScholarScout's original application code. It does
not replace licenses for dependencies, fonts, research papers or other
third-party material. Dependency versions are recorded in
`backend/requirements.lock.txt` and `frontend/package-lock.json`.

## PyMuPDF and MuPDF

ScholarScout uses PyMuPDF for PDF processing. PyMuPDF and its underlying MuPDF
engine are offered under the GNU Affero General Public License (AGPL) and
commercial license agreements by Artifex Software Inc. No commercial license
is supplied by this repository.

- PyMuPDF source: <https://github.com/pymupdf/PyMuPDF>
- MuPDF source: <https://github.com/ArtifexSoftware/mupdf>
- Official licensing information:
  <https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright>
- GNU AGPL version 3: <https://www.gnu.org/licenses/agpl-3.0.html>

Deployments retaining the open-source dependency must meet the applicable
AGPL terms, including source-availability obligations where applicable. The
application's MIT notice does not override those obligations. A commercial
deployment that cannot meet them requires an appropriate Artifex license or a
different PDF-processing dependency. Consult the included upstream license
materials for the exact versions used.

## Research demo

The demo paper is **Attention Is All You Need**, Ashish Vaswani, Noam Shazeer,
Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Lukasz Kaiser and
Illia Polosukhin, 2017, arXiv:1706.03762.

- Paper and source record: <https://arxiv.org/abs/1706.03762>
- Recorded distribution license:
  <https://arxiv.org/licenses/nonexclusive-distrib/1.0/license.html>

The arXiv license records the submitter's grant to arXiv; it is not a grant to
relicense this paper under MIT. The original paper also contains an attributed
permission concerning reproduction of tables and figures in journalistic or
scholarly works. Neither notice makes ScholarScout the owner of the paper.
The PDF binary is excluded from Git and release images. The demo retrieves the
original public PDF into a temporary visitor session when needed. See
`backend/demo_assets/README.md` for the behavior and attribution.

## Red Rose font

The frontend requests Red Rose through Google Fonts, copyright 2018 The Red
Rose Project Authors (<https://github.com/magictype/redrose>). The font is licensed
under the SIL Open Font License, version 1.1; it is not relicensed by
ScholarScout. Font assets are not bundled in this repository.

- Google Fonts upstream family and license:
  <https://github.com/google/fonts/tree/main/ofl/redrose>
- SIL Open Font License: <https://openfontlicense.org/>

## Other dependencies

Python and JavaScript dependencies retain their upstream copyrights and
licenses. Their packages include license material. Distributing a built
image or frontend bundle must preserve the notices required by those licenses.
