#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${THEA_VENV:-${ROOT_DIR}/.venv}"
VENV_PYTHON="${VENV_DIR}/bin/python"
THEA_LARK="${VENV_DIR}/bin/thea-lark"
CONFIG_PATH="${THEA_CONFIG:-${ROOT_DIR}/config.yaml}"
ENV_PATH="${THEA_ENV:-${ROOT_DIR}/.env}"
TRANSPORT="${THEA_LARK_TRANSPORT:-ws}"
PROVIDER_OVERRIDE="${LLM_PROVIDER:-}"
FACTORY_OVERRIDE="${LARK_HARNESS_FACTORY:-}"
MODE="${THEA_MODE:-channel}"
CHECK_ONLY=0
INSTRUCTION=""

usage() {
  cat <<'EOF'
Usage: ./run.sh --mode MODE [OPTIONS]

Modes:
  cli         Run an interactive terminal, or one Task with --instruction.
  channel     Run the default Harness through Feishu/Lark (default).
  simulation  Run a simulator Harness factory through Feishu/Lark.
  robot       Run a robot Harness factory through Feishu/Lark.

Options:
  --check                     Validate startup without opening a channel or Task.
  --config PATH               Trusted Harness YAML (default: ./config.yaml).
  --env PATH                  Dotenv file (default: ./.env).
  --provider NAME             Override llm.provider from the YAML file.
  --harness-factory PATH      Deployment factory in module:callable form.
  --transport ws|webhook      Feishu/Lark transport (default: ws).
  --instruction TEXT          Run one non-interactive Task in cli mode.
  --max-turns N               Maximum model turns in cli mode.
  --failure-budget N          Maximum recoverable failures in cli mode.
  -h, --help                  Show this help and exit.

Simulation and robot modes require Feishu/Lark credentials and an importable
Harness factory. Applications without Lark should construct Harness in Python
and call run_stream() directly.
EOF
}

LARK_ARGS=()
CLI_ARGS=()
while (($#)); do
  argument="$1"
  case "${argument}" in
    -h | --help)
      usage
      exit 0
      ;;
    --check)
      CHECK_ONLY=1
      shift
      ;;
    --mode)
      if (($# < 2)); then
        echo "error: --mode requires a value." >&2
        exit 1
      fi
      MODE="$2"
      shift 2
      ;;
    --mode=*)
      MODE="${argument#*=}"
      shift
      ;;
    --instruction)
      if (($# < 2)); then
        echo "error: --instruction requires a value." >&2
        exit 1
      fi
      INSTRUCTION="$2"
      shift 2
      ;;
    --instruction=*)
      INSTRUCTION="${argument#*=}"
      shift
      ;;
    --env)
      if (($# < 2)); then
        echo "error: --env requires a value." >&2
        exit 1
      fi
      ENV_PATH="$2"
      shift 2
      ;;
    --env=*)
      ENV_PATH="${argument#*=}"
      shift
      ;;
    --max-turns | --failure-budget)
      if (($# < 2)); then
        echo "error: ${argument} requires a value." >&2
        exit 1
      fi
      CLI_ARGS+=("${argument}" "$2")
      shift 2
      ;;
    --max-turns=* | --failure-budget=*)
      CLI_ARGS+=("${argument}")
      shift
      ;;
    --config | --transport | --provider | --harness-factory)
      if (($# < 2)); then
        echo "error: ${argument} requires a value." >&2
        exit 1
      fi
      value="$2"
      case "${argument}" in
        --config) CONFIG_PATH="${value}" ;;
        --transport) TRANSPORT="${value}" ;;
        --provider) PROVIDER_OVERRIDE="${value}" ;;
        --harness-factory) FACTORY_OVERRIDE="${value}" ;;
      esac
      LARK_ARGS+=("${argument}" "${value}")
      shift 2
      ;;
    --config=*)
      CONFIG_PATH="${argument#*=}"
      LARK_ARGS+=("${argument}")
      shift
      ;;
    --transport=*)
      TRANSPORT="${argument#*=}"
      LARK_ARGS+=("${argument}")
      shift
      ;;
    --provider=*)
      PROVIDER_OVERRIDE="${argument#*=}"
      LARK_ARGS+=("${argument}")
      shift
      ;;
    --harness-factory=*)
      FACTORY_OVERRIDE="${argument#*=}"
      LARK_ARGS+=("${argument}")
      shift
      ;;
    *)
      LARK_ARGS+=("${argument}")
      CLI_ARGS+=("${argument}")
      shift
      ;;
  esac
done

if [[ "${CONFIG_PATH}" != /* ]]; then
  CONFIG_PATH="${ROOT_DIR}/${CONFIG_PATH}"
fi
if [[ "${ENV_PATH}" != /* ]]; then
  ENV_PATH="${ROOT_DIR}/${ENV_PATH}"
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "error: Thea is not installed in ${VENV_DIR}." >&2
  echo "Run ${ROOT_DIR}/install.sh first." >&2
  exit 1
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "error: Harness configuration not found: ${CONFIG_PATH}" >&2
  echo "Run ${ROOT_DIR}/install.sh or set THEA_CONFIG." >&2
  exit 1
fi

if [[ "${MODE}" != "cli" && "${MODE}" != "channel" && "${MODE}" != "simulation" && "${MODE}" != "robot" ]]; then
  echo "error: --mode must be 'cli', 'channel', 'simulation', or 'robot'." >&2
  exit 1
fi

if [[ "${MODE}" == "cli" ]]; then
  CLI_COMMAND=(
    "${VENV_PYTHON}" -m harness.cli
    --config "${CONFIG_PATH}"
    --env "${ENV_PATH}"
  )
  if [[ -n "${PROVIDER_OVERRIDE}" ]]; then
    CLI_COMMAND+=(--provider "${PROVIDER_OVERRIDE}")
  fi
  if [[ -n "${INSTRUCTION}" ]]; then
    CLI_COMMAND+=(--instruction "${INSTRUCTION}")
  fi
  if ((CHECK_ONLY)); then
    CLI_COMMAND+=(--check)
  fi
  if ((${#CLI_ARGS[@]})); then
    exec "${CLI_COMMAND[@]}" "${CLI_ARGS[@]}"
  fi
  exec "${CLI_COMMAND[@]}"
fi

if [[ -n "${INSTRUCTION}" ]]; then
  echo "error: --instruction is available only with --mode cli." >&2
  exit 1
fi

if [[ ! -x "${THEA_LARK}" ]]; then
  echo "error: thea-lark is not installed in ${VENV_DIR}." >&2
  echo "Run ${ROOT_DIR}/install.sh first." >&2
  exit 1
fi

if [[ "${TRANSPORT}" != "ws" && "${TRANSPORT}" != "webhook" ]]; then
  echo "error: THEA_LARK_TRANSPORT must be 'ws' or 'webhook'." >&2
  exit 1
fi

"${VENV_PYTHON}" - \
  "${ENV_PATH}" \
  "${CONFIG_PATH}" \
  "${TRANSPORT}" \
  "${PROVIDER_OVERRIDE}" \
  "${FACTORY_OVERRIDE}" \
  "${MODE}" \
  "${ROOT_DIR}" <<'PY'
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from thea_lark.runtime import apply_provider_override, load_runtime_config


env_path = Path(sys.argv[1])
config_path = Path(sys.argv[2])
transport = sys.argv[3]
load_dotenv(env_path)
provider_override = sys.argv[4].strip() or os.environ.get("LLM_PROVIDER", "").strip()
factory_override = (
    sys.argv[5].strip() or os.environ.get("LARK_HARNESS_FACTORY", "").strip()
)
mode = sys.argv[6]
root_dir = Path(sys.argv[7])
sys.path.insert(0, str(root_dir))

missing = [
    name
    for name in ("LARK_APP_ID", "LARK_APP_SECRET", "LARK_ALLOWED_OPEN_IDS")
    if not os.environ.get(name, "").strip()
]

try:
    payload = load_runtime_config(config_path)
    apply_provider_override(payload, provider_override)
except (OSError, TypeError, ValueError) as exc:
    raise SystemExit(f"error: cannot load {config_path}: {exc}") from exc

factory = factory_override.strip()
llm = payload.get("llm") or {}
if not isinstance(llm, dict):
    raise SystemExit(f"error: {config_path} field 'llm' must be a mapping.")

provider = str(llm.get("provider") or "anthropic").strip().lower()
if not factory and not str(llm.get("api_key") or "").strip():
    configured_key = str(llm.get("api_key_env") or "").strip()
    accepted_keys = {
        "anthropic": ("ANTHROPIC_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "mimo": ("MIMO_API_KEY", "XIAOMI_API_KEY", "MI_API_KEY"),
        "xiaomi_mimo": ("MIMO_API_KEY", "XIAOMI_API_KEY", "MI_API_KEY"),
        "xiaomi": ("MIMO_API_KEY", "XIAOMI_API_KEY", "MI_API_KEY"),
        "qwen": ("DASHSCOPE_API_KEY",),
        "deepseek": ("DEEPSEEK_API_KEY",),
    }.get(provider, ())
    if configured_key:
        accepted_keys = (configured_key,)
    if accepted_keys and not any(os.environ.get(key, "").strip() for key in accepted_keys):
        missing.append(" or ".join(accepted_keys))
    if provider == "mock":
        replay = str(llm.get("replay_path") or os.environ.get("LLM_REPLAY") or "")
        if not replay.strip():
            missing.append("llm.replay_path or LLM_REPLAY")

if transport == "webhook" and not (
    os.environ.get("LARK_VERIFICATION_TOKEN", "").strip()
    or os.environ.get("LARK_ENCRYPT_KEY", "").strip()
):
    missing.append("LARK_VERIFICATION_TOKEN or LARK_ENCRYPT_KEY")

if mode in {"simulation", "robot"} and not factory_override:
    missing.append(
        "LARK_HARNESS_FACTORY or --harness-factory "
        f"(required for {mode} mode)"
    )

if missing:
    rendered = "\n".join(f"  - {item}" for item in dict.fromkeys(missing))
    raise SystemExit(
        f"error: complete the following settings in {env_path} or the environment:\n"
        f"{rendered}"
    )

if factory_override:
    module_name, separator, attribute = factory_override.partition(":")
    if not separator or not module_name.strip() or not attribute.strip():
        raise SystemExit(
            "error: harness factory must use module:callable syntax."
        )
    try:
        module = importlib.import_module(module_name.strip())
    except ImportError as exc:
        raise SystemExit(
            f"error: cannot import harness factory module {module_name!r}: {exc}"
        ) from exc
    factory = getattr(module, attribute.strip(), None)
    if not callable(factory):
        raise SystemExit(
            f"error: harness factory {factory_override!r} is not callable."
        )

if mode == "simulation":
    try:
        import thea_simulation
    except ImportError as exc:
        raise SystemExit(
            "error: simulation mode requires thea-simulation. "
            "Run ./install.sh."
        ) from exc
    required = ("SimulationRuntime", "LiberoEpisode", "RoboTwinEpisode")
    missing_interfaces = [
        name for name in required if not hasattr(thea_simulation, name)
    ]
    if missing_interfaces:
        raise SystemExit(
            "error: incomplete thea-simulation installation: "
            + ", ".join(missing_interfaces)
        )

print(
    f"Preflight passed: transport={transport}, provider={provider}, "
    f"mode={mode}, config={config_path}"
)
PY

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  exit 0
fi

cd "${ROOT_DIR}"
export THEA_ENV="${ENV_PATH}"
exec "${THEA_LARK}" \
  --transport "${TRANSPORT}" \
  --config "${CONFIG_PATH}" \
  "${LARK_ARGS[@]}"
