# SHAPE: Structured Heuristic Analysis of Problem-solving in rEasoning models

> **Dataset release coming soon.**

SHAPE is a framework for analyzing the reasoning process of Large Reasoning Models (LRMs) through the lens of mathematical heuristics. We segment Chain-of-Thought (CoT) traces into *Content Units* (plan–execution cycles), label each unit with expert-grounded heuristic codes (H1–H13), and track *Semantic Space* transitions to derive interpretable metrics that predict answer correctness and characterize model capability.

---

## Repository Structure

```
SHAPE/
├── vllm_build.sh               # Environment setup
├── run_content_unit.sh         # Step 1: Content unit segmentation
├── run_heuristics.sh           # Step 2: Heuristic code labeling
├── run_semantic_space.sh       # Step 3: Semantic space tracking
├── utils/
│   ├── content_unit/           # Preprocessing & segmentation scripts
│   ├── guidebook/              # Annotation guidebooks for each stage
│   └── method/                 # vLLM inference & labeling backends
└── analaysis_notebook/
    ├── 3_1.ipynb               # §3 — Correctness prediction baselines
    ├── 3_2.ipynb               # §3 — SHAPE metric summary by model
    ├── 4_1.ipynb               # §4 — Heuristic frequency distribution
    ├── 4_2.ipynb               # §4 — Post-trained vs. base model trajectories
    └── shape_metrics.py        # Shared SHAPE metric computation
```

---

## 1. Environment Setup

`vllm_build.sh` creates a Conda (or venv) environment with the correct PyTorch and vLLM nightly stack for your CUDA version.

```bash
bash vllm_build.sh
```

The script auto-detects CUDA and tries wheel variants in order: `cu126 → cu128 → cu130 → cpu`. If all wheels fail, it falls back to a source build. On success it registers an `ipykernel` as **Python (vllm-dev)** for notebook use.

**Key environment variables (all optional):**

| Variable | Default | Description |
|---|---|---|
| `ENV_NAME` | `vllm-dev` | Conda environment name |
| `PYTHON_VERSION` | `3.10` | Python version |
| `CUDA_VARIANTS` | auto | Override wheel search order, e.g. `"cu130 cpu"` |

---

## 2. Tagging Pipeline

Activate the environment, then run the three scripts **in order** from the project root:

```bash
conda activate vllm-dev
```

### Step 1 — Content Unit Segmentation

Splits raw CoT traces into *Content Units*: cohesive blocks where the solver formulates a strategic intent (Plan) and immediately carries it out (Execution).

```bash
bash run_content_unit.sh
```

Key options (set as environment variables or edit the script):

| Variable | Default | Description |
|---|---|---|
| `INPUT_FOLDER` | `data/raw` | Folder with `*.jsonl` / `*.json` input files |
| `MODEL` | `google/gemini-2.5-flash-lite` | LLM used for chunking |
| `WORKERS` | `10` | Number of concurrent workers |

Output: `data/content_unit/` (chunked files) and `data/raw/` (normalized raw files).

---

### Step 2 — Heuristic Labeling

Annotates each Content Unit with one or more heuristic codes (H1–H13) using a local vLLM model.

```bash
bash run_heuristics.sh
```

Configure `DIRS`, `MODELS`, and sampling parameters at the top of the script. The default model is `Qwen/Qwen3.5-27B`. Runs sequentially on a single GPU to avoid OOM.

Output: `data/llm-label-heuristics/`

**Heuristic codes** are grounded in Rott (2014) and Favier & Dorier (2024):

| Code | Strategy |
|---|---|
| H1 | Changing the register of semiotic representation |
| H2 | Cognitive Reinterpretation |
| H3 | Introduce Symbolic Representation / Formalization |
| H4 | Problem Classification / Rephrase the Problem and Goal |
| H5 | Structural Augmentation (auxiliary objects / lemmas) |
| H6 | Wishful Thinking — simplify or relax constraints |
| H7 | Explicit Case Analysis / Decompose into Subproblems |
| H8 | Analogy to known problems |
| H9 | Arguing by Contradiction |
| H10 | Presenting Related Theorems, Tools, or Properties |
| H11 | Experimental & Pattern Exploration |
| H12 | Working Backward |
| H13 | Verification and Looking Back |

---

### Step 3 — Semantic Space Labeling

Tracks *Semantic Space* state transitions across Content Units. Each unit is labeled `NEW`, `RETURN`, or `MAINTAIN` depending on whether the active register, constraints, and mathematical framework changed fundamentally.

```bash
bash run_semantic_space.sh
```

Reads from `data/llm-label-heuristics/` (filtered by `HEURISTIC_MODEL`, default `Qwen-Qwen3.5-27B`) and writes to `data/llm-label-semantic-space/`.

---

## 3. Analysis

Open the notebooks in `analaysis_notebook/` with the `vllm-dev` kernel. Each notebook corresponds to a section of the paper:

| Notebook | Paper Section | Description |
|---|---|---|
| `3_1.ipynb` | §3 | Correctness prediction baselines — 6 feature-set conditions under 5-fold stratified CV |
| `3_2.ipynb` | §3 | SHAPE metric descriptive statistics broken down by model and correctness |
| `4_1.ipynb` | §4 | Heuristic frequency distribution analysis (Jensen–Shannon divergence) |
| `4_2.ipynb` | §4 | Density & coverage: post-trained vs. base model reasoning trajectories |

Shared metric computation (SHAPE scores, space/transition efficiency, transition ratio) lives in `analaysis_notebook/shape_metrics.py`.

---

## Dataset

The dataset will be released upon publication. Stay tuned.

---

## Citation

*Coming soon.*
