# Contributing to Thea

Thank you for contributing to Thea. This repository contains three
public Python packages:

- `thea-harness`, the provider-neutral runtime for embodied agents;
- `thea-simulation`, optional LIBERO and RoboTwin benchmark integrations;
- `thea-lark`, the optional Feishu/Lark user channel.

Contributions should keep all three packages portable, deployment-neutral, and
consistent with the runtime contracts described in
*Towards the Harness of Embodied Agents*.

## Ways to contribute

Bug fixes, focused tests, documentation corrections, and small usability
improvements can go directly to a pull request. Open an issue before starting
work that would introduce or materially change any of the following:

- a public interface, configuration key, event shape, or dependency;
- Agentic Loop behavior or a context lifecycle;
- a trust, authorization, evaluation, or physical-safety boundary;
- a cross-package refactor or compatibility break.

The issue should state the problem, the proposed boundary, compatibility and
safety implications, and alternatives considered. This gives maintainers a
chance to confirm direction before substantial implementation work begins.

Report suspected vulnerabilities privately as described in
[SECURITY.md](SECURITY.md). Do not disclose them in a public issue or pull
request.

## Development setup

Python 3.10 or newer is required when working across the repository.

```bash
git clone <repository-url>
cd <repository-directory>

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e './harness[dev,all]'
python -m pip install -e './simulation[dev]'
python -m pip install -e './lark[dev]'
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

Verify the public imports:

```bash
python -c "import harness, thea_lark, thea_simulation; print(harness.__version__)"
```

## Repository boundaries

| Area | Public responsibility |
|---|---|
| `harness/` | Agentic Loop, Context, Tool Definitions and Tool Results, hooks, Observation, Scene Graph interfaces, Evaluation, Memory, Skills, and provider boundaries |
| `simulation/` | Optional benchmark runtime and LIBERO/RoboTwin adapters built only on public Harness boundaries |
| `lark/` | Feishu/Lark transport, authentication, session ownership, cards, images, and user-interaction tools |
| Deployment code | Robot SDKs, hardware connections, perception backends, motion policies, evaluator implementations, credentials, and operational configuration |

Robot-specific adapters, datasets, recorded runs, credentials, private
endpoints, and machine-specific paths do not belong in this repository.
Integrate deployment behavior through the public Protocols, dependency
injection, and Tool Definitions. Do not import private deployment packages
from the Harness or Lark packages.

## Runtime contracts

Changes must preserve the following contracts:

- One turn contains one model decision and at most one model-selected Tool
  execution.
- Resident, Refreshed, and Accumulated are the three context lifetimes.
- The current Observation and transient Tool Result images do not accumulate
  in message history.
- The Scene Graph Brief is regenerated before every model decision, while the
  working Scene Graph remains deployment-owned.
- Scene Graph detail queries are keyed by an existing object ref.
- Tool Definitions contain `name`, `description`, and `inputSchema`.
- Tool Results use a structured success or failure envelope.
- Evaluator post-conditions and `evaluate_run` remain hidden from the model.
- Execution-derived Scene Graph updates require evaluator-confirmed success.
- Task Notes expire at task end. Normally completed tasks consolidate Memory
  and Tool Experience before expiry; aborted tasks do not write durable state.
- Physical Tool Calls pass through schema validation and configured hooks.
- Tasks sharing physical hardware are serialized unless the deployment
  provides independent resource ownership.

Use the established public terms exactly. Do not reintroduce retired aliases
for Scene Graph Brief, Observation, Evaluation as Exit Codes, Task Notes, or
the three context lifetimes.

A proposal to revise one of these contracts requires maintainer agreement
before implementation. The public API, documentation, tests, and manuscript
terminology must remain mutually consistent.

## Development workflow

1. Create a focused branch from the current public branch.
2. Make the smallest coherent change that resolves the issue.
3. Add or update tests that fail without the change.
4. Update the nearest guide when public behavior, configuration, security
   properties, or operational workflows change.
5. Run the relevant checks listed below.
6. Inspect the complete diff for secrets, private paths, internal endpoints,
   recorded user data, and generated artifacts.
7. Open a pull request with a clear rationale and reproducible verification
   evidence.

Keep unrelated formatting, renaming, dependency upgrades, and behavior
changes in separate pull requests. Clear commit messages and a reviewable
history make regressions easier to isolate.

## Engineering standards

- Follow the Python versions and Ruff configuration declared by each package.
- Add type hints to public interfaces and Protocol implementations.
- Keep deployment behavior behind explicit, dependency-injected boundaries.
- Return structured failures at Tool boundaries instead of leaking exceptions
  into the Agentic Loop.
- Use deterministic checks for schema validation, authorization, and
  pre-execution safety requirements. Prompt text is not an enforcement
  mechanism.
- Document non-obvious ownership, lifecycle, concurrency, and safety
  decisions. Avoid comments that only restate the code.
- Keep examples credential-free and executable without physical hardware.
- Use relative repository links and portable paths in public documentation.
- Do not claim compatibility, capability, or safety properties that are not
  implemented and tested.

New dependencies require a clear runtime need, a compatible license, and a
review of maintenance and security impact. Prefer the standard library or an
existing dependency when it provides the required behavior.

## Tests and quality checks

Run the checks for every package affected by the change.

### Harness

```bash
(cd harness && python -m pytest -q)
(cd harness && python -m ruff check .)
(cd harness && python -m ruff format --check .)
```

### Lark

```bash
(cd lark && python -m pytest -q)
(cd lark && python -m ruff check .)
(cd lark && python -m ruff format --check .)
```

### Simulation

```bash
(cd simulation && python -m pytest -q)
(cd simulation && python -m ruff check .)
(cd simulation && python -m ruff format --check .)
```

### Repository-level checks

```bash
python -m compileall -q harness simulation lark
```

### Documentation

Install the documentation dependencies and build the Sphinx site with warnings
treated as errors:

```bash
python -m pip install -r docs/requirements.txt
make -C docs html
```

The Markdown guides in `harness/docs/` are authoritative. Pages under
`docs/harness/` should remain thin MyST include wrappers so that package
documentation and the website cannot diverge.

For package metadata, dependency, package-data, or public-import changes, also
build all distributions:

```bash
python -m build harness
python -m build simulation
python -m build lark
```

Install the generated wheels in a clean virtual environment, run
`python -m pip check`, verify public imports, and run `thea-lark --help`.

Continuous integration tests Harness and Lark on Python 3.10 through 3.13,
tests the optional simulation package, and performs a clean build and
installation smoke test.

## Pull request requirements

The pull request description should include:

- the problem and the reason for the change;
- the affected package, public boundary, or user workflow;
- compatibility, security, and physical-safety implications;
- tests and manual checks performed;
- documentation updated;
- intentionally deferred follow-up work.

Before requesting review, confirm that:

- the diff is limited to the stated purpose;
- new behavior has focused tests;
- all relevant checks pass;
- public documentation matches the implementation;
- no credentials, private paths, internal endpoints, datasets, user records,
  or deployment artifacts are included;
- authorization, Tool validation, hooks, evaluation, task serialization, and
  log redaction have not been weakened.

Maintainers may request that independent changes be split when they have
different review, compatibility, or risk profiles. Contributions accepted
into this repository are licensed under the repository's
[Apache License 2.0](LICENSE).
