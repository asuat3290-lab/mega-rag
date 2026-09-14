# Contributing

This is a development snapshot shared in a private repository. No open-source
license has been declared. Corpus access and code access do not grant redistribution rights.

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
