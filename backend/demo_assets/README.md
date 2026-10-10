# Optional local demo paper

ScholarScout's one-click demo uses **Attention Is All You Need** by Ashish
Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez,
Lukasz Kaiser and Illia Polosukhin (2017).

- Original record: <https://arxiv.org/abs/1706.03762>
- Original PDF: <https://arxiv.org/pdf/1706.03762>
- Recorded arXiv distribution license:
  <https://arxiv.org/licenses/nonexclusive-distrib/1.0/license.html>

The PDF is **not included in the Git repository or release container**. When
this directory has no local `attention-is-all-you-need.pdf`, clicking the demo
downloads the original public PDF through the same bounded HTTPS downloader
used for identifier ingestion. The downloaded copy belongs to the visitor's
temporary session, is stored encrypted and follows that session's deletion
policy. A repeated request in the same session reuses its existing paper.

For local development you may place a legitimately obtained copy at
`backend/demo_assets/attention-is-all-you-need.pdf`; it remains ignored by Git.
If the source is unavailable, the interface offers retry or manual upload.
The paper remains subject to its authors' and publishers' rights; ScholarScout's
MIT application license does not license the paper.
