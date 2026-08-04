# Maintain the documentation site

The Sphinx site renders Harness component documentation from `harness/docs/`
and the simulation guide from `simulation/README.md` through small include
wrappers. Site-specific Usage, setup, navigation, Lark reference, and other
reference pages live directly under `docs/`. Keep detailed channel operation
in `lark/docs/` and link to it from the site rather than copying the same
mechanism into two files.

## Preview locally

Create or activate a Python environment, then install the documentation
dependencies:

```bash
python -m pip install -r docs/requirements.txt
make -C docs serve
```

The command builds the site and serves it at `http://127.0.0.1:8000`.

## Validate a change

```bash
make -C docs html
```

The build treats Sphinx warnings as errors. Invalid navigation, missing
includes, malformed references, and theme configuration errors therefore fail
before publication.

## Publish to the project subroute

The documentation is published to
`https://eit-hai.github.io/thea/documentation/`. The Documentation workflow
builds this source tree and updates only `thea/documentation/` on the
`gh-pages` branch of `EIT-HAI/EIT-HAI.github.io`; the project page under
`thea/` is preserved.

Configure the repository secret `THEA_DOCS_DEPLOY_TOKEN` with a fine-grained
GitHub token that has Contents read/write access to
`EIT-HAI/EIT-HAI.github.io`. A push to `main` that changes documentation
sources publishes automatically. Maintainers can also run the workflow
manually from GitHub Actions.

## Add a component document

1. Add the source Markdown file under `harness/docs/`.
2. Add a MyST include wrapper under `docs/harness/`.
3. Add the wrapper to the appropriate nested `toctree`.

Top-level navigation is declared in `docs/index.md`. Each section index owns
its child pages, following the order:

```text
Usage
Install & Setup
Harness
Scene Graph
Evaluation
Reference
```

Keep deployment-specific secrets, private endpoints, generated logs, and robot
assets outside the documentation source.
