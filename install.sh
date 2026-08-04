#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${THEA_VENV:-${ROOT_DIR}/.venv}"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="${PYTHON}"
else
  PYTHON_BIN=""
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      PYTHON_BIN="${candidate}"
      break
    fi
  done
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "error: Python was not found. Install Python 3.10 or newer." >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "error: ${PYTHON_BIN} was not found. Install Python 3.10 or newer." >&2
  exit 1
fi

if ! "${PYTHON_BIN}" -c '
import sys
if sys.version_info < (3, 10):
    raise SystemExit(
        f"Thea requires Python 3.10 or newer; found {sys.version.split()[0]}."
    )
'; then
  exit 1
fi

echo "Creating the virtual environment at ${VENV_DIR}"
if ! "${PYTHON_BIN}" -m venv "${VENV_DIR}"; then
  echo "error: ${PYTHON_BIN} could not create a virtual environment." >&2
  echo "Install the Python venv component or select another interpreter," >&2
  echo "for example: PYTHON=python3.12 ./install.sh" >&2
  exit 1
fi

VENV_PYTHON="${VENV_DIR}/bin/python"
if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "error: the virtual environment did not provide ${VENV_PYTHON}." >&2
  exit 1
fi

echo \
  "Installing Thea Harness, Harness-side simulation adapters, and the Lark channel"
"${VENV_PYTHON}" -m pip install --upgrade pip
"${VENV_PYTHON}" -m pip install \
  --editable "${ROOT_DIR}/harness[all]" \
  --editable "${ROOT_DIR}/simulation" \
  --editable "${ROOT_DIR}/lark"

if [[ ! -e "${ROOT_DIR}/.env" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  echo "Created .env from .env.example"
else
  echo "Preserved existing .env"
fi

if [[ ! -e "${ROOT_DIR}/config.yaml" ]]; then
  cp "${ROOT_DIR}/lark/config.example.yaml" "${ROOT_DIR}/config.yaml"
  echo "Created config.yaml from lark/config.example.yaml"
else
  echo "Preserved existing config.yaml"
fi

"${VENV_PYTHON}" -c \
  'import harness, thea_lark, thea_simulation; print(f"Installed Thea Harness {harness.__version__}.")'

echo
echo "Installation complete."
echo "1. Add the selected model API key to ${ROOT_DIR}/.env"
echo "2. Review ${ROOT_DIR}/config.yaml"
echo "3. Run ${ROOT_DIR}/run.sh --mode cli"
echo "   Lark, simulation, and robot modes require their respective integrations."
echo "   For simulation or a physical robot, provide --harness-factory."
echo "   Install LIBERO or RoboTwin 2.0 separately before using simulation mode."
