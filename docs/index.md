---
html_theme.sidebar_secondary.remove: true
---

{.thea-document-title}
# Thea Documentation

<section class="thea-hero">
  <div class="thea-hero__copy">
    <span class="thea-eyebrow">Harness for embodied agents</span>
    <h1>Build robot agents around a reliable loop.</h1>
    <p>
      Thea connects models, physical tools, current evidence, persistent world
      state, post-execution evaluation, and durable experience behind a small
      set of public boundaries.
    </p>
    <div class="thea-actions">
      <a class="thea-button thea-button--primary" href="usage/index.html">
        Start with Thea
      </a>
      <a class="thea-button" href="setup/index.html">
        Install and set up
      </a>
    </div>
  </div>
  <div class="thea-hero__signal" aria-label="Thea design principles">
    <div>
      <strong>01</strong>
      <span>One model decision and at most one selected Tool Call per turn</span>
    </div>
    <div>
      <strong>02</strong>
      <span>Fresh Observation and a ref-addressable Scene Graph</span>
    </div>
    <div>
      <strong>03</strong>
      <span>Evaluation as Exit Codes after physical execution</span>
    </div>
  </div>
</section>

<section class="thea-architecture">
  <div class="thea-section-heading">
    <span>System view</span>
    <h2>A portable layer between model and body</h2>
    <p>
      The model, robot embodiment, policies, and user channel remain
      replaceable. The Harness owns the loop and the boundaries between them.
    </p>
  </div>
  <div class="thea-architecture__frame">
    <img
      src="overview.png"
      alt="Thea architecture connecting the user, model, tools, body, Scene Graph, evaluator, and physical world"
    >
  </div>
</section>

<section class="thea-paths">
  <div class="thea-section-heading">
    <span>Read by task</span>
    <h2>Start where your integration starts</h2>
  </div>
  <div class="thea-card-grid">
    <a class="thea-card" href="harness/concepts.html">
      <span class="thea-card__index">01</span>
      <h3>Runtime and context</h3>
      <p>Follow Task, Turn, and Session lifetimes through the Agentic Loop.</p>
      <strong>Open concepts →</strong>
    </a>
    <a class="thea-card" href="harness/model-and-tools.html">
      <span class="thea-card__index">02</span>
      <h3>Models and tools</h3>
      <p>Implement a provider adapter or register a local or MCP-backed Tool.</p>
      <strong>Open interfaces →</strong>
    </a>
    <a class="thea-card" href="scene-graph/index.html">
      <span class="thea-card__index">03</span>
      <h3>Scene Graph</h3>
      <p>Connect persistent world state, its Brief, and ref-keyed queries.</p>
      <strong>Open Scene Graph →</strong>
    </a>
    <a class="thea-card" href="evaluation/index.html">
      <span class="thea-card__index">04</span>
      <h3>Evaluation</h3>
      <p>Reconstruct an exit code from post-execution physical evidence.</p>
      <strong>Open Evaluation →</strong>
    </a>
  </div>
</section>

<section class="thea-quickstart">
  <div>
    <span class="thea-eyebrow">First run</span>
    <h2>Install the public packages in one step.</h2>
    <p>
      The installer creates an isolated environment and initializes local
      configuration without overwriting existing values.
    </p>
  </div>

```bash
./install.sh
./run.sh --mode cli
```

  <p class="thea-quickstart__next">
    Ready to connect a simulator or robot?
    <a href="usage/index.html">Follow the Usage guide.</a>
  </p>
</section>

```{toctree}
:hidden:
:maxdepth: 3

usage/index
setup/index
harness/index
scene-graph/index
evaluation/index
reference/index
```
