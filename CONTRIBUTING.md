# Contributing

This is an open-source development snapshot under the [MIT License](LICENSE).
Contributions are welcome under the same license. Submit only material you have
the right to contribute. Corpus access does not grant corpus redistribution rights.

## Useful first contributions

- Reproducible installation and synthetic retrieval examples.
- Historical spelling and exact/variant/related-hit regression cases.
- Source identity, TEXT/APPARAT attribution, and language-coverage tests.
- Small evaluation sets whose text can legally be shared, with explicit scope and limitations.

Read [AGENTS.md](AGENTS.md). Do not replace source identity with a similarity score,
infer corpus completeness from file counts, or merge editorial and authorial text.

Include commit, OS/Python version, commands, expected/actual behavior, and test
results. Distinguish local fixture results from corpus-level benchmarks. Use
temporary databases; do not run importer/index/vector writers against a live corpus.
Do not attach private research outputs, credentials, full copyrighted volumes,
vector stores, or personal configuration. For sensitive reports, contact the owner privately.
