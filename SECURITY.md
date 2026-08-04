# Security Policy

Thea can connect a language model, a remote user channel, local
processes, and physical robot capabilities. Its security model therefore
covers both software boundaries and control over actions in the physical
world. Model instructions, prompts, and model reasoning are never treated as
security boundaries.

## Supported versions

Thea is currently a pre-1.0 public preview.

| Version | Security support |
|---|---|
| Current default branch | Supported |
| Latest published `0.1.x` packages | Supported |
| Older snapshots and deployment forks | No guaranteed backports |

Security fixes are developed against the current public code. A report should
identify the exact package version or commit under assessment.

## Reporting a vulnerability

Use GitHub private vulnerability reporting for this repository:

1. open the repository's **Security** tab;
2. select **Advisories**;
3. choose **Report a vulnerability**.

Do not open a public issue, discussion, or pull request for a vulnerability
that may expose credentials, private logs, user messages, physical locations,
deployment topology, robot-control details, or a working exploit. If private
vulnerability reporting is unavailable, contact a repository maintainer
through a verified private channel before sending sensitive material.

Include the minimum information required to reproduce and assess the issue:

- affected package, version, or commit;
- deployment assumptions and required configuration;
- concise reproduction steps or a minimal proof of concept;
- expected and observed behavior;
- potential confidentiality, integrity, availability, or physical-action
  impact;
- suggested mitigation, if known.

Remove secrets, personal data, exact physical locations, and unrelated
operational logs. Use synthetic data where possible.

## Safe testing and disclosure

Only test systems, accounts, workspaces, and robots that you own or have
explicit permission to assess. Do not use a production robot, occupied
workspace, or valuable object to demonstrate a vulnerability. Prefer the
hardware-free example or an isolated simulator.

Maintainers will validate the report, identify the affected trust boundary,
assess severity, and develop a mitigation where required. Public disclosure
should be coordinated so affected users have a reasonable opportunity to
update. Timing depends on reproducibility, impact, and maintainer
availability; this policy does not promise a fixed response or remediation
service level.

## Trust boundaries

Thea treats the following as untrusted input:

- user messages, commands, and uploaded media;
- model output, Tool Calls, and Tool arguments;
- webhook payloads and network-fetched content;
- content displayed through Observations, Tool Results, and model responses.

The following are trusted, operator-controlled inputs:

- Harness and Lark configuration;
- MCP server commands, arguments, working directories, and environments;
- deployment factory paths and imported deployment code;
- robot adapters, perception providers, evaluator implementations, and
  low-level safety systems;
- Skill and Embodiment Profile directories selected by the operator.

Never derive trusted configuration from user input or model output.
Configuration that can start a process, import a Python callable, or select
executable local content has the same trust level as local code execution.

## Vulnerabilities in scope

Reports are in scope when they demonstrate a reachable weakness in the public
Harness or Lark packages. Examples include:

- authentication, authorization, or allowlist bypass;
- webhook signature, timestamp, token, or encryption-verification bypass;
- cross-user session confusion or unintended data disclosure;
- Tool Definition, hook, evaluator, or physical-task serialization bypass;
- model access to evaluator-only post-conditions or internal tools;
- execution-derived Scene Graph updates without confirmed evaluation;
- arbitrary command execution through untrusted MCP configuration;
- path traversal or file access outside an explicitly allowed root;
- server-side request forgery or unsafe remote image retrieval;
- credential, token, private-path, or sensitive-log disclosure;
- unbounded remote input that creates a practical denial of service;
- default configuration that silently enables unsafe network or physical
  behavior.

Third-party dependency reports are welcome when they demonstrate a reachable
impact through Thea. Otherwise, report the issue directly to the
upstream project.

## Generally out of scope

The following are not normally considered Thea vulnerabilities unless
they bypass an enforced boundary:

- undesirable model output or prompt injection without authorization bypass,
  data exposure, code execution, or physical-control impact;
- behavior caused by deliberately disabling a documented safeguard;
- weaknesses in robot SDKs, motion planners, or infrastructure outside the
  public Harness and Lark packages;
- access obtained through credentials or trusted configuration already
  disclosed by the operator;
- theoretical issues without a plausible, reachable execution path.

Physical hazards remain important even when their root cause falls outside
this repository. Stop using the affected robot and report the issue to the
responsible deployment owner.

## Deployment security baseline

Before connecting Thea to physical hardware:

- authorize users before creating a Harness session;
- configure `LARK_ALLOWED_OPEN_IDS` to fail closed and require bot mentions in
  group chats;
- use TLS and the documented webhook trust anchors when webhook transport is
  enabled;
- store API keys and application secrets in environment variables or a secret
  manager;
- treat Harness YAML, deployment factories, MCP commands, Skills, and
  Embodiment Profiles as trusted local resources;
- expose only the network routes required by the selected transport;
- leave remote image loading disabled unless it is required and constrained;
- restrict local image access to an explicit root;
- serialize tasks that share a robot or physical workspace;
- preserve schema validation and deterministic pre-execution hooks for
  physical tools;
- keep emergency stops, collision avoidance, actuator limits, force limits,
  and hardware interlocks independent of the Agentic Loop;
- protect run logs as sensitive operational data and define an appropriate
  retention policy;
- treat a timeout or lost tool connection as an unknown physical state, then
  stop and inspect the robot before resuming.

Do not expose a development server directly to an untrusted network. Use a
production reverse proxy and process supervisor where appropriate, apply
least-privilege filesystem and network permissions, and isolate robot-control
services from public ingress.

## Logs and diagnostic material

Run logs may contain user instructions, model reasoning, Tool arguments,
evaluator evidence, images, object refs, and details of the physical
environment. Built-in redaction and event-size limits reduce accidental
exposure; they do not make logs safe to publish.

When sharing diagnostic material:

- reproduce with synthetic data when possible;
- remove tokens, identifiers, user content, images, physical locations, and
  internal endpoints;
- include only the smallest event sequence needed for analysis;
- transfer sensitive artifacts through the agreed private reporting channel.

## Physical incident response

If a suspected vulnerability may have affected a robot:

1. stop autonomous execution using an independent hardware control;
2. isolate the user channel and robot-control services;
3. revoke or rotate potentially exposed credentials;
4. preserve the minimum relevant logs under appropriate access controls;
5. inspect the robot and physical workspace before resuming operation;
6. apply the update or mitigation and verify it in an unoccupied, controlled
   workspace.

The controls in this repository are reference safeguards. They are not a
certified functional-safety system and do not replace deployment-specific
hazard analysis, testing, supervision, or regulatory compliance.
