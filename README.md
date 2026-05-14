# SHAPE: Semantic-space and Heuristic Analysis for Problem-solving Evolution

SHAPE analyzes Chain-of-Thought (CoT) in math reasoning at the level where problem solving actually happens: not only surface text, self-revision markers, or generic reasoning episodes, but the model’s evolving mathematical interpretation of the problem.

We represent each CoT as a sequence of observable heuristic actions and semantic-space transitions. This makes it possible to ask whether a model stays within one mathematical framing, shifts to another, returns to a previous one, or scatters effort across many disconnected interpretations.


## Why SHAPE?

Recent reasoning models often generate long CoT traces, but we still know little about what mathematical activity is actually taking place inside them. A reasoning trace may contain planning, checking, or self-correction markers, yet still fail to reveal how the model is representing and transforming the mathematical problem itself.

SHAPE addresses this by borrowing two concepts from mathematics education:

- **Heuristics**: local mathematical actions, such as introducing notation, changing representation, trying cases, working backward, or verifying a result.
- **Semantic spaces**: trajectory-relative mathematical interpretations of the problem, defined by the objects, constraints, goals, and operations the solver is currently using.

Together, these allow us to analyze not only what actions appear in a CoT, but how those actions are organized across changing mathematical interpretations.

## What the paper shows

Using SHAPE, we study how LLMs organize mathematical reasoning across semantic spaces and heuristics.

Our main findings are:

1. **Correct solutions tend to stay focused.**  
   Successful trajectories often concentrate heuristic effort within a small number of semantic spaces, while incorrect trajectories revisit or scatter across spaces without making progress.

2. **Hard perturbations reveal adaptive but error-like reasoning.**  
   When a problem looks similar but requires a different solution method, models change their heuristic choices but often fail to reorganize their semantic-space structure effectively.

3. **RL post-training narrows heuristic usage.**  
   Post-trained models concentrate successful trajectories into a narrower region of the base model’s heuristic distribution, suggesting structural mode-seeking at the level of mathematical strategy.


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


---

### Step 2 — Heuristic Labeling

Annotates each Content Unit with one or more heuristic codes (H1–H13) using a local vLLM model.

```bash
bash run_heuristics.sh
```

Configure `DIRS`, `MODELS`, and sampling parameters at the top of the script. The default model is `Qwen/Qwen3.5-27B`. Runs sequentially on a single GPU to avoid OOM.

Output: `data/llm-label-heuristics/`

**Heuristic codes** (Appendix A):

| Code | Strategy |
|---|---|
| H1 | Changing the register of semiotic representation |
| H2 | Cognitive Reinterpretation |
| H3 | Introduce Symbolic Representation, Formalization, and Structural Augmentation |
| H4 | Problem Classification / Rephrase the Problem and Goal |
| H5 | Wishful Thinking — simplify or reduce the problem and conditions |
| H6 | Explicit Case Analysis / Decompose into Subproblems |
| H7 | Arguing by Contradiction |
| H8 | Analogy and Presenting Related Theorems |
| H9 | Experimental and Pattern Exploration |
| H10 | Working Backward |
| H11 | Verification and Looking Back |

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
