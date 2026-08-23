---
title: Documentation pipeline
---

# Documentation pipeline

This site is built, validated and published on its own, without compiling the
firmware or installing the training toolkit. Editing a page, checking it and
shipping it takes seconds and needs nothing but Python.

```text
Edit docs -> Validate -> Build docs only -> Review -> Merge -> GitHub Actions -> Deploy
```

## Commands

All five live in `plxy.sh`, the script that already runs everything else in this
project — there is no separate docs tool to learn or to keep in step.

| Command | Does |
| --- | --- |
| `./plxy.sh docs-preview [port]` | serve the site locally with hot reload |
| `./plxy.sh docs-check` | validate without publishing — what CI runs on a PR |
| `./plxy.sh docs-build` | validate, then generate the static site into `site/` |
| `./plxy.sh docs-deploy` | validate, then trigger the GitHub Actions deployment |
| `./plxy.sh docs-clean` | remove `site/` |

`docs-build` is a superset of `docs-check`: the build validates first and refuses
to emit a site that did not pass, so there is no way to publish an unvalidated
build by picking the wrong command.

## Local preview

```bash
./plxy.sh docs-preview
```

Then open **<http://127.0.0.1:8001/>**.

Port **8001**, not 8000 — 8000 is the [live dashboard](../guide/live-dashboard.md),
and having both open at once is normal. Override with an argument or the
environment:

```bash
./plxy.sh docs-preview 8010
PLXY_DOCS_PORT=8010 ./plxy.sh docs-preview
```

The preview gives you:

- **hot reload** — save any file under `docs/` and the open page refreshes
- **local search**, the same index the published site uses
- the real **navigation**, so tab and section structure can be tested
- **light, dark and system themes** via the toggle in the header
- **responsive layout** — narrow the window, or use the browser's device mode
- **broken links visible** as you click, and images that resolve or do not

The preview is deliberately **not** strict: it must not die because a link you
are halfway through typing does not resolve yet. `docs-check` is the gate.

## First run

The docs commands build their own environment on first use:

```text
==> creating the docs environment in .venv-docs
  ok  docs toolchain installed
```

`.venv-docs` is created from `requirements-docs.txt`, which contains exactly one
requirement — `mkdocs-material`. It is separate from the project `.venv` on
purpose: the docs build must never be able to pull in torch, opencv or
onnxruntime.

| Variable | Default | Effect |
| --- | --- | --- |
| `PLXY_DOCS_PYTHON` | discovered | interpreter to use |
| `PLXY_DOCS_VENV` | `.venv-docs` | where the toolchain is installed |
| `PLXY_DOCS_PORT` | `8001` | preview port |
| `PLXY_DOCS_NO_VENV` | unset | `1` uses the ambient interpreter and never creates a venv — what CI sets |
| `PLXY_DOCS_SITE_URL` | the published Pages URL | base URL to build for — `docs-preview` sets it to the local address, and a fork can point it at its own Pages URL without editing `mkdocs.yml` |

## Validation

`docs-check` runs a **strict** MkDocs build, in which every warning is an error,
followed by a credential scan. It fails on:

| Checked | Caught as |
| --- | --- |
| Broken internal links | *contains a link, but the target is not found* |
| Broken `#anchors` | *does not contain an anchor* |
| Nav entries pointing at nothing | *included in the nav configuration, which is not found* |
| Pages missing from the nav | *omitted from the nav configuration* |
| Missing images and assets | reported as an unresolved link |
| Duplicate routes | two files claiming one URL |
| Invalid Markdown that breaks the build | build error |
| Absolute links that should be relative | the `absolute_links` validation |
| Credential leakage | the secret scan below |
| Any other build warning | *Aborted with N warnings in strict mode* |

Links that leave `docs/` — to source files, or to `PROJECT_STATE.md` — must be
**absolute GitHub URLs**, because those files do not exist in the published
site. Strict validation is what enforces that.

### The credential scan

The scan runs over `docs/`, `mkdocs.yml` **and the generated site**, because a
secret can also arrive through a theme setting or an included snippet and never
appear in `docs/` at all. It matches unambiguous credential shapes only — PEM
private keys, AWS access key ids, GitHub tokens, Slack tokens, Google API keys
and JWTs.

It deliberately does **not** use a broad password-assignment pattern. This
project documents a compiled-in Wi-Fi AP password on purpose
([and says so](../security.md#the-access-point-is-open-by-default)), and a scan
that fires on every page mentioning it is a scan somebody switches off.

## GitHub Actions

Two workflows, and only one of them can publish.

### `docs-deploy.yml` — publish

```yaml
on:
  push:
    branches: [main]
    paths:
      - "docs/**"
      - "mkdocs.yml"
      - "requirements-docs.txt"
      - "plxy.sh"
      - ".github/workflows/docs-deploy.yml"
  workflow_dispatch:
```

```text
Checkout -> Setup Python 3.12 (pip cache) -> Install requirements-docs.txt
   -> Configure Pages -> ./plxy.sh docs-build   (validate + links + secret scan)
   -> Upload Pages artifact -> Deploy -> Report URL, commit and timestamp
```

It requests the minimum permissions Pages needs, and notably **not**
`contents: write` — the workflow never pushes to the repository:

```yaml
permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false
```

`cancel-in-progress: false` because cancelling a Pages deployment half way is
how a site ends up in a partial state.

### `docs-check.yml` — pull requests

Runs on documentation pull requests with `permissions: contents: read` only, so
it **cannot** deploy even in principle:

```text
Pull request -> docs-check -> docs-build -> upload the rendered site as an artifact
```

Reviewers download the `documentation-site` artifact and open it locally before
approving. Nothing is published until the pull request is merged to `main`.

### Running the same thing everywhere

Both workflows call `./plxy.sh docs-check` and `./plxy.sh docs-build` rather than
inlining `mkdocs` commands. A green CI run and a green local run therefore mean
the same thing, and there is no second copy of the build to drift.

## Deployment safety

The pipeline is constrained so a documentation change cannot affect anything
else:

- **Never builds or deploys the application.** No firmware compilation, no
  container image, no registry push, no database, no integration environment,
  no release.
- **Never modifies production infrastructure.** The only artifact it produces is
  a static site, and the only thing it writes to is GitHub Pages.
- **Never writes to the repository.** `contents: read`.
- **Never publishes an invalid build.** `deploy` is a separate job gated on
  `needs: build`; if validation fails the deployment never starts, and the
  **previously published site stays exactly as it was**.
- **Never publishes from a laptop.** `docs-deploy` validates locally and then
  dispatches the workflow, so every published build went through CI.
- **Never exposes secrets.** No workflow secret is read, and the credential scan
  runs over the generated output as well as the sources.

## Deployment result

Each run writes a summary to the GitHub Actions job page:

| Reported | From |
| --- | --- |
| Build status | the `build` job |
| Page count | the generated site |
| Commit SHA | `GITHUB_SHA` |
| Build timestamp | UTC, at build time |
| Deployment status | the `deploy` job |
| Documentation URL | the `deploy-pages` output |
| Deployment timestamp | UTC, at deploy time |

The live site is <https://sengphirum.github.io/PLXY_DrowsyGuard/>, linked from
the repository README.

## Enabling Pages on a fork

Once, in **Settings → Pages**, set **Source** to **GitHub Actions**. No
`gh-pages` branch is used and none is created — the site is served from the
uploaded artifact, which is why the workflow needs no write access to the
repository.

## Adding a page

1. Create the Markdown file under `docs/`.
2. Add it to `nav:` in `mkdocs.yml`. A page that is not in the nav fails strict
   validation, which is deliberate — an unreachable page is a bug, not a draft.
3. `./plxy.sh docs-preview` and read it.
4. `./plxy.sh docs-check` before pushing.

Open a pull request and the same checks run again in CI; merge to `main` and it
publishes itself.
