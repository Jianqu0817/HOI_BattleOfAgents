#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ "${1:-all}" != all || -n "${TTS_URL:-}" || ( -z "${GPT_SOVITS_HOME:-}" && ! -d "$PWD/engine" ) ]]; then
  exec "${PYTHON:-python3}" run.py "$@"
fi
export GPT_SOVITS_HOME="${GPT_SOVITS_HOME:-$PWD/engine}"
if [[ -d "$PWD/engine/runtime/open_jtalk_dic" ]]; then
  export OPEN_JTALK_DICT_DIR="${OPEN_JTALK_DICT_DIR:-$PWD/engine/runtime/open_jtalk_dic}"
fi
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
if [[ -z "${GPT_SOVITS_PYTHON:-}" ]]; then
  base="${PYTHON:-python3}"
  if [[ -z "${PYTHON:-}" && -x /opt/conda/bin/python ]]; then base=/opt/conda/bin/python; fi
  "$base" -c 'import torch, torchaudio; print("PyTorch",torch.__version__,"CUDA",torch.cuda.is_available())'
  if [[ ! -x .venv/bin/python ]]; then "$base" -m venv --system-site-packages .venv; fi
  if ! cmp -s inference-requirements.txt .venv/iron-ready; then
    "$base" -c 'from importlib.metadata import version; print("torch=="+version("torch"));print("torchaudio=="+version("torchaudio"))' > .venv/torch-constraints.txt
    .venv/bin/python -m pip install -c .venv/torch-constraints.txt -r inference-requirements.txt
    cp inference-requirements.txt .venv/iron-ready
  fi
  export GPT_SOVITS_PYTHON="$PWD/.venv/bin/python"
fi
exec "$GPT_SOVITS_PYTHON" run.py "$@"
