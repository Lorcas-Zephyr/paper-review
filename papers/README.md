# Local paper corpus

Put the private corpus under this directory using the following layout:

```text
papers/<sample-id>/paper.pdf
papers/<sample-id>/reviews/review-01.pdf
papers/<sample-id>/reviews/review-02.pdf
```

Raw PDFs are intentionally ignored by Git. Build a lightweight manifest with:

```bash
python app/scripts/build_paper_manifest.py --root papers --output papers/manifest.jsonl
```

The manifest stores relative paths, hashes, review counts, optional page
counts, and deterministic train/validation/test splits. Configure the service
with `PAPERS_ROOT` and `PAPERS_MANIFEST`; do not place private PDF content in
the public repository.
