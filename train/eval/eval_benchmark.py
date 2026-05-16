from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STEP_PATTERN = re.compile(r"global_step_(\d+)$")
HF_WEIGHT_GLOBS = ("*.safetensors", "pytorch_model*.bin", "model*.safetensors")
HF_MODEL_INDEX_GLOBS = ("*.safetensors.index.json", "pytorch_model.bin.index.json")
BACKEND_NAME = "vllm-api-stream"
MERGE_DONE_MARKER = ".fsdp_merge_done"


@dataclass
class Sample:
    idx: int
    prompt: str
    answer: str
    raw: dict[str, Any]


@dataclass
class ParsedCkptName:
    algorithm: str
    model: str
    train_dataset: str
    timestamp: str


DEFAULT_PROMPT_KEYS = ("prompt", "problem", "question")
DEFAULT_ANSWER_KEY = "answer"


def _load_math_reward_score_fn():
    module_path = PROJECT_ROOT / "verl/utils/reward_score/math_reward.py"
    spec = importlib.util.spec_from_file_location("math_reward_local", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load score function from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


math_reward_module = _load_math_reward_score_fn()
score_fn = math_reward_module.compute_score
process_validation_metrics_fn = None


def _load_process_validation_metrics():
    global process_validation_metrics_fn
    if process_validation_metrics_fn is not None:
        return process_validation_metrics_fn

    module_path = PROJECT_ROOT / "verl/trainer/ppo/metric_utils.py"
    spec = importlib.util.spec_from_file_location("verl_metric_utils_local", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load process_validation_metrics from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    process_validation_metrics_fn = module.process_validation_metrics
    return process_validation_metrics_fn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate checkpoints on datasets with a shared scoring rule.")
    parser.add_argument(
        "--dataset-path",
        type=Path,
        required=True,
        help="Dataset path (JSON/JSONL). All benchmarks are assumed to share keys/scorer.",
    )
    parser.add_argument(
        "--benchmark-name",
        type=str,
        required=True,
        help="Name tag used in outputs. Defaults to dataset file stem.",
    )
    parser.add_argument(
        "--ckpt-root",
        type=Path,
        default=None,
        help="Checkpoint experiment directory containing global_step_* subdirectories.",
    )
    parser.add_argument(
        "--include-base-model",
        action="store_true",
        help="Also evaluate a base model as step 0 (global_step_0).",
    )
    parser.add_argument(
        "--base-model-path",
        type=str,
        default=None,
        help="Base model path or HF repo id used for step 0 evaluation.",
    )
    parser.add_argument(
        "--base-step-name",
        type=str,
        default="global_step_0",
        help="Step name tag used for base model evaluation results.",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=None,
        help="Model name tag for output filenames. Defaults to ckpt-root directory name.",
    )
    parser.add_argument(
        "--run-name",
        type=str,
        default=None,
        help="Run name suffix for output directory/file names. Auto-generated when omitted.",
    )
    parser.add_argument(
        "--train-dataset",
        type=str,
        default=None,
        help="Train dataset tag used for organizing result directory names.",
    )
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--n-samples", type=int, default=1, help="Number of sampled responses per prompt.")
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Bootstrap seed for pass@k/maj@k metrics (same method as verl validation metrics).",
    )
    parser.add_argument("--tensor-parallel-size", type=int, default=1)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9)
    parser.add_argument("--max-model-len", type=int, default=None)
    parser.add_argument("--vllm-api-host", type=str, default="127.0.0.1")
    parser.add_argument(
        "--vllm-api-port",
        type=int,
        default=0,
        help="Port for vLLM OpenAI API server. 0 means auto-select free port.",
    )
    parser.add_argument("--vllm-api-key", type=str, default="EMPTY")
    parser.add_argument("--vllm-max-concurrency", type=int, default=64)
    parser.add_argument("--vllm-start-timeout", type=int, default=180)
    parser.add_argument(
        "--steps",
        type=int,
        nargs="*",
        default=None,
        help="Only evaluate selected global steps (e.g., --steps 200 250).",
    )
    parser.add_argument(
        "--step",
        type=int,
        default=None,
        help="Single-step alias of --steps.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of samples for quick checks.")
    parser.add_argument(
        "--auto-merge-fsdp",
        action="store_true",
        help="If HF weights are missing under actor/huggingface, merge FSDP shards automatically.",
    )
    parser.add_argument(
        "--merged-model-root",
        type=Path,
        default=Path("ckpts_merged_hf"),
        help="Output directory for merged HF models.",
    )
    parser.add_argument("--inspect-only", action="store_true", help="Only inspect checkpoint structure.")
    parser.add_argument("--save-predictions", action="store_true", help="Save per-sample predictions.")
    parser.add_argument(
        "--save-step-results",
        action="store_true",
        default=True,
        help="Save one result json per step with model name and step in filename.",
    )
    parser.add_argument(
        "--no-save-step-results",
        dest="save_step_results",
        action="store_false",
        help="Disable per-step result json files.",
    )
    parser.add_argument("--show-progress", action="store_true", default=True, help="Enable tqdm progress bars.")
    parser.add_argument("--no-progress", dest="show_progress", action="store_false", help="Disable progress bars.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark_eval/results_benchmark.json"),
        help="Result JSON path.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Directory root for outputs. If set, summary filename defaults to results_benchmark.json under this root.",
    )
    parser.add_argument(
        "--no-latest-summary",
        action="store_true",
        help="Do not write/update results_benchmark__latest.json under output root.",
    )
    parser.add_argument(
        "--prompt-key",
        type=str,
        nargs="*",
        default=None,
        help=(
            "Override prompt keys (in priority order). Default tries "
            f"{list(DEFAULT_PROMPT_KEYS)}. Useful for datasets like ThinkARM "
            "(use 'Instruction')."
        ),
    )
    parser.add_argument(
        "--answer-key",
        type=str,
        nargs="*",
        default=None,
        help=(
            "Override answer keys (in priority order). Default tries "
            f"['{DEFAULT_ANSWER_KEY}']. Useful for datasets like ThinkARM "
            "(use 'Correct Answer')."
        ),
    )
    parser.add_argument(
        "--prompt-suffix",
        type=str,
        default="",
        help=(
            "String appended to each prompt after the dataset value. Use this to "
            "inject the boxed-answer instruction for datasets that ship raw problem "
            "text without it (e.g., ThinkARM)."
        ),
    )
    return parser.parse_args()


def resolve_benchmark_name(args: argparse.Namespace, dataset_path: Path) -> str:
    if args.benchmark_name:
        return args.benchmark_name
    return dataset_path.stem


def sanitize_filename_tag(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip())
    cleaned = cleaned.strip("-")
    return cleaned or "unknown"


def parse_ckpt_name(ckpt_root: Path | None) -> ParsedCkptName | None:
    if ckpt_root is None:
        return None
    parts = ckpt_root.name.split("-")
    if len(parts) != 4:
        return None
    return ParsedCkptName(
        algorithm=parts[0],
        model=parts[1],
        train_dataset=parts[2],
        timestamp=parts[3],
    )


def infer_model_name_tag(
    explicit_model_name: str | None,
    ckpt_root: Path | None,
    base_model_path: str | None,
) -> str:
    if explicit_model_name:
        return sanitize_filename_tag(explicit_model_name)
    if ckpt_root is not None:
        return sanitize_filename_tag(ckpt_root.name)
    if base_model_path:
        return sanitize_filename_tag(base_model_path.rstrip("/").split("/")[-1])
    return "unknown-model"


def write_step_result_file(
    output_dir: Path,
    benchmark_name: str,
    model_name_tag: str,
    source_tag: str,
    step_name: str,
    step_result: dict[str, Any],
) -> Path:
    if source_tag == "checkpoint":
        filename = f"{step_name}.json"
    else:
        filename = f"{source_tag}_{step_name}.json"
    path = output_dir / filename
    payload = {
        "benchmark": benchmark_name,
        "model_name": model_name_tag,
        "source": source_tag,
        "step_name": step_name,
        "result": step_result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_base_summary_file(
    *,
    output_dir: Path,
    benchmark_name: str,
    model_name_tag: str,
    base_model_path: str | None,
    base_step_name: str,
    seed: int,
    n_samples: int,
    result: dict[str, Any],
) -> Path:
    filename = f"base_report_{base_step_name}.json"
    path = output_dir / filename
    payload = {
        "benchmark_name": benchmark_name,
        "base_model_path": base_model_path,
        "base_step_name": base_step_name,
        "model_name": model_name_tag,
        "n_samples_per_prompt": n_samples,
        "seed": seed,
        "result": result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def make_run_output_dir(output_root: Path, run_name: str) -> Path:
    run_dir_name = sanitize_filename_tag(run_name)
    run_dir = output_root / run_dir_name
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def read_dataset(
    dataset_path: Path,
    limit: int | None = None,
    prompt_keys: tuple[str, ...] = DEFAULT_PROMPT_KEYS,
    answer_keys: tuple[str, ...] = (DEFAULT_ANSWER_KEY,),
    prompt_suffix: str = "",
) -> list[Sample]:
    path = dataset_path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    samples: list[Sample] = []
    with path.open("r", encoding="utf-8") as f:
        first_non_ws = ""
        while True:
            ch = f.read(1)
            if not ch:
                break
            if not ch.isspace():
                first_non_ws = ch
                break
        f.seek(0)

        if first_non_ws == "[":
            iterator = json.load(f)
        else:
            iterator = (json.loads(line) for line in f if line.strip())

        for n, item in enumerate(iterator):
            prompt = None
            for key in prompt_keys:
                value = item.get(key)
                if value is not None:
                    prompt = str(value)
                    break
            answer = None
            for key in answer_keys:
                value = item.get(key)
                if value is not None:
                    answer = value
                    break
            if prompt is None or answer is None:
                continue

            if prompt_suffix:
                prompt = prompt + prompt_suffix

            raw_idx = item.get("idx")
            if raw_idx is None:
                raw_idx = item.get("index", n)
            try:
                idx = int(raw_idx)
            except (TypeError, ValueError):
                idx = n
            samples.append(Sample(idx=idx, prompt=prompt, answer=str(answer), raw=item))
            if limit is not None and len(samples) >= limit:
                break

    if not samples:
        raise RuntimeError(f"No valid samples loaded from {path}")
    return samples


def discover_steps(ckpt_root: Path, selected_steps: list[int] | None = None) -> list[Path]:
    if not ckpt_root.exists():
        raise FileNotFoundError(f"Checkpoint root not found: {ckpt_root}")

    selected = set(selected_steps or [])
    result: list[tuple[int, Path]] = []
    for p in ckpt_root.iterdir():
        if not p.is_dir():
            continue
        match = STEP_PATTERN.match(p.name)
        if not match:
            continue
        step = int(match.group(1))
        if selected and step not in selected:
            continue
        result.append((step, p))

    result.sort(key=lambda x: x[0])
    return [p for _, p in result]


def list_hf_weight_files(hf_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in HF_WEIGHT_GLOBS:
        files.extend(sorted(hf_dir.glob(pattern)))
    return files


def list_hf_index_files(hf_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in HF_MODEL_INDEX_GLOBS:
        files.extend(sorted(hf_dir.glob(pattern)))
    return files


def _looks_like_merged_model_dir(model_dir: Path) -> bool:
    if list_hf_weight_files(model_dir):
        return True
    if list_hf_index_files(model_dir):
        return True
    # Some merger outputs may not include local weight files (e.g., PEFT/base-reference style).
    # In that case, treat a valid HF config plus tokenizer assets as reusable.
    return (model_dir / "config.json").exists() and any(model_dir.glob("tokenizer*"))


def inspect_checkpoint(step_dir: Path) -> dict[str, Any]:
    actor_dir = step_dir / "actor"
    hf_dir = actor_dir / "huggingface"
    actor_model_files = sorted(actor_dir.glob("model_world_size_*_rank_*.pt"))
    actor_optim_files = sorted(actor_dir.glob("optim_world_size_*_rank_*.pt"))
    actor_extra_files = sorted(actor_dir.glob("extra_state_world_size_*_rank_*.pt"))
    hf_weights = list_hf_weight_files(hf_dir)

    return {
        "step_dir": str(step_dir),
        "has_data_pt": (step_dir / "data.pt").exists(),
        "actor_dir_exists": actor_dir.exists(),
        "has_fsdp_config": (actor_dir / "fsdp_config.json").exists(),
        "num_model_shards": len(actor_model_files),
        "num_optim_shards": len(actor_optim_files),
        "num_extra_state_shards": len(actor_extra_files),
        "hf_dir_exists": hf_dir.exists(),
        "num_hf_weight_files": len(hf_weights),
        "hf_weight_files": [p.name for p in hf_weights],
    }


def maybe_merge_fsdp_checkpoint(
    actor_dir: Path,
    merged_model_root: Path,
    trust_remote_code: bool = True,
) -> Path:
    out_dir = merged_model_root / actor_dir.parent.name
    out_dir.mkdir(parents=True, exist_ok=True)
    merge_marker = out_dir / MERGE_DONE_MARKER

    if merge_marker.exists() and _looks_like_merged_model_dir(out_dir):
        print(f"[merge] Reusing cached merged model (marker found): {out_dir}")
        return out_dir

    if _looks_like_merged_model_dir(out_dir):
        merge_marker.write_text("ok\n", encoding="utf-8")
        print(f"[merge] Reusing cached merged model: {out_dir}")
        return out_dir

    cmd = [
        sys.executable,
        "-m",
        "verl.model_merger",
        "merge",
        "--backend",
        "fsdp",
        "--local_dir",
        str(actor_dir),
        "--target_dir",
        str(out_dir),
    ]
    if trust_remote_code:
        cmd.append("--trust-remote-code")
    subprocess.run(cmd, check=True)
    if not _looks_like_merged_model_dir(out_dir):
        raise RuntimeError(
            f"FSDP merge finished but merged artifacts were not found under {out_dir} "
            f"(expected weight/index files, or config+tokenizer)."
        )
    merge_marker.write_text("ok\n", encoding="utf-8")
    return out_dir


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        return int(s.getsockname()[1])


def _check_vllm_server_ready(host: str, port: int, api_key: str) -> bool:
    url = f"http://{host}:{port}/v1/models"
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310
            return int(resp.status) == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def _read_tail_text(path: Path, max_chars: int = 3000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def start_vllm_api_server(model_ref: str, args: argparse.Namespace) -> tuple[subprocess.Popen[Any], str, int, Path]:
    host = args.vllm_api_host
    port = args.vllm_api_port if args.vllm_api_port > 0 else find_free_port()
    served_model_name = "eval-model"
    log_fd, log_path_str = tempfile.mkstemp(prefix="eval_vllm_", suffix=".log")
    os.close(log_fd)
    log_path = Path(log_path_str)

    cmd = [
        sys.executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        model_ref,
        "--served-model-name",
        served_model_name,
        "--host",
        host,
        "--port",
        str(port),
        "--tensor-parallel-size",
        str(args.tensor_parallel_size),
        "--gpu-memory-utilization",
        str(args.gpu_memory_utilization),
        "--trust-remote-code",
        "--api-key",
        args.vllm_api_key,
    ]
    if args.max_model_len is not None:
        cmd += ["--max-model-len", str(args.max_model_len)]

    log_fp = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=log_fp, stderr=log_fp)  # noqa: S603

    deadline = time.time() + max(args.vllm_start_timeout, 10)
    while time.time() < deadline:
        if proc.poll() is not None:
            log_fp.close()
            tail = _read_tail_text(log_path)
            raise RuntimeError(
                f"vLLM API server exited early for model {model_ref}.\n"
                f"log_file={log_path}\n"
                f"log_tail:\n{tail}"
            )
        if _check_vllm_server_ready(host, port, args.vllm_api_key):
            log_fp.close()
            return proc, served_model_name, port, log_path
        time.sleep(1.0)

    proc.terminate()
    log_fp.close()
    tail = _read_tail_text(log_path)
    raise TimeoutError(
        f"Timed out waiting for vLLM API server start ({args.vllm_start_timeout}s) for model {model_ref}.\n"
        f"log_file={log_path}\n"
        f"log_tail:\n{tail}"
    )


def stop_vllm_api_server(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _gen_metric_ks(n_samples: int) -> list[int]:
    if n_samples <= 1:
        return [1]
    ks: list[int] = []
    k = 2
    while k < n_samples:
        ks.append(k)
        k *= 2
    ks.append(n_samples)
    return ks


def _extract_math_prediction(text: str) -> str:
    try:
        boxed = math_reward_module.last_boxed_only_string(text)
        if boxed is None:
            return ""
        raw = math_reward_module.remove_boxed(boxed)
        return math_reward_module.strip_string(raw)
    except Exception:  # noqa: BLE001
        # Any parser/assertion failure is treated as invalid prediction.
        return ""


def _compute_eval_metrics(
    *,
    rows: list[dict[str, Any]],
    n_samples: int,
    seed: int,
) -> dict[str, Any]:
    if n_samples <= 1:
        acc_vals = [float(row["score"] >= 1.0) for row in rows]
        avg_1 = float(sum(acc_vals) / len(acc_vals)) if acc_vals else 0.0
        return {
            "acc/mean@1": avg_1,
            "acc/avg@1": avg_1,
            "num_prompts": len(rows),
            "n_samples_per_prompt": 1,
        }

    data_sources: list[str] = []
    sample_uids: list[str] = []
    infos_dict: dict[str, list[Any]] = {"acc": [], "pred": []}

    for row in rows:
        sample_uid = str(row["idx"])
        preds = row["predictions"]
        scores = row["scores"]
        pred_keys = row["pred_keys"]
        for score, pred_key in zip(scores, pred_keys, strict=True):
            data_sources.append("benchmark")
            sample_uids.append(sample_uid)
            infos_dict["acc"].append(float(score >= 1.0))
            infos_dict["pred"].append(pred_key)

    process_validation_metrics = _load_process_validation_metrics()
    metrics_nested = process_validation_metrics(
        data_sources=data_sources,
        sample_uids=sample_uids,
        infos_dict=infos_dict,
        seed=seed,
    )
    acc_metrics = metrics_nested.get("benchmark", {}).get("acc", {})
    alias_metrics: dict[str, Any] = {}
    for metric_name, metric_val in acc_metrics.items():
        alias_metrics[f"acc/{metric_name}"] = metric_val
        alias = metric_name.replace("mean@", "avg@").replace("best@", "pass@")
        alias_metrics[f"acc/{alias}"] = metric_val

    return {
        **alias_metrics,
        "num_prompts": len(rows),
        "n_samples_per_prompt": n_samples,
        "metric_ks": _gen_metric_ks(n_samples),
    }


async def _async_stream_completions(
    *,
    host: str,
    port: int,
    api_key: str,
    model_name: str,
    prompts: list[str],
    temperature: float,
    top_p: float,
    max_new_tokens: int,
    n_samples: int,
    max_concurrency: int,
    show_progress: bool,
) -> list[list[str]]:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is required for async vLLM API streaming. Please install `httpx`.") from exc

    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    outputs: list[list[str]] = [[""] * n_samples for _ in range(len(prompts))]
    url = f"http://{host}:{port}/v1/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=None) as client:
        pbar = tqdm(
            total=len(prompts),
            desc=f"Rollout(vLLM API stream) {model_name}",
            disable=not show_progress,
            leave=False,
        )

        async def generate_one(i: int, prompt: str) -> None:
            payload = {
                "model": model_name,
                "prompt": prompt,
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_new_tokens,
                "n": n_samples,
                "stream": True,
            }
            chunks_by_choice: list[list[str]] = [[] for _ in range(n_samples)]
            try:
                async with semaphore:
                    async with client.stream("POST", url, headers=headers, json=payload) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[len("data:") :].strip()
                            if data == "[DONE]":
                                break
                            try:
                                message = json.loads(data)
                            except json.JSONDecodeError:
                                # Robust against malformed/truncated SSE chunks; count as empty generation.
                                continue
                            for choice in message.get("choices", []):
                                idx = int(choice.get("index", 0))
                                if idx < 0 or idx >= n_samples:
                                    continue
                                text_piece = choice.get("text", "")
                                if text_piece:
                                    chunks_by_choice[idx].append(text_piece)
            except Exception as exc:  # noqa: BLE001
                # Keep evaluation running; this prompt will become wrong answers.
                print(f"[rollout] prompt_index={i} generation error: {exc}")
            outputs[i] = ["".join(parts) for parts in chunks_by_choice]
            pbar.update(1)

        tasks = [asyncio.create_task(generate_one(i, p)) for i, p in enumerate(prompts)]
        try:
            await asyncio.gather(*tasks)
        finally:
            pbar.close()

    return outputs


def rollout_vllm(
    model_ref: str,
    prompts: list[str],
    temperature: float,
    top_p: float,
    max_new_tokens: int,
    n_samples: int,
    show_progress: bool,
    args: argparse.Namespace,
) -> list[list[str]]:
    proc, served_model_name, port, log_path = start_vllm_api_server(model_ref=model_ref, args=args)
    try:
        return asyncio.run(
            _async_stream_completions(
                host=args.vllm_api_host,
                port=port,
                api_key=args.vllm_api_key,
                model_name=served_model_name,
                prompts=prompts,
                temperature=temperature,
                top_p=top_p,
                max_new_tokens=max_new_tokens,
                n_samples=n_samples,
                max_concurrency=args.vllm_max_concurrency,
                show_progress=show_progress,
            )
        )
    finally:
        stop_vllm_api_server(proc)
        if log_path.exists():
            log_path.unlink(missing_ok=True)


def evaluate_one_checkpoint(
    model_ref: str,
    samples: list[Sample],
    args: argparse.Namespace,
) -> tuple[float, list[dict[str, Any]]]:
    prompts = [s.prompt for s in samples]
    generations = rollout_vllm(
        model_ref=model_ref,
        prompts=prompts,
        temperature=args.temperature,
        top_p=args.top_p,
        max_new_tokens=args.max_new_tokens,
        n_samples=args.n_samples,
        show_progress=args.show_progress,
        args=args,
    )

    if len(generations) != len(samples):
        raise RuntimeError(
            f"Generation count mismatch: got {len(generations)} for {len(samples)} samples"
        )

    rows: list[dict[str, Any]] = []
    prompt_acc_sum = 0.0
    pair_iter = zip(samples, generations)
    for sample, preds in tqdm(
        pair_iter,
        total=len(samples),
        desc="Scoring",
        disable=not args.show_progress,
        leave=False,
    ):
        if len(preds) != args.n_samples:
            raise RuntimeError(
                f"n_samples mismatch for idx={sample.idx}: got {len(preds)} vs expected {args.n_samples}"
            )
        scores: list[float] = []
        for pred in preds:
            try:
                scores.append(float(score_fn(pred, sample.answer)))
            except Exception:  # noqa: BLE001
                # Keep evaluation running: score parsing failures are counted as wrong.
                scores.append(0.0)
        accs = [float(score >= 1.0) for score in scores]
        prompt_acc = float(sum(accs) / len(accs))
        prompt_acc_sum += prompt_acc
        pred_keys = [_extract_math_prediction(pred) for pred in preds]
        rows.append(
            {
                "idx": sample.idx,
                "answer": sample.answer,
                "prediction": preds[0],
                "score": scores[0],
                "predictions": preds,
                "scores": scores,
                "pred_keys": pred_keys,
                "avg_acc": prompt_acc,
            }
        )
    accuracy = prompt_acc_sum / len(samples)
    return accuracy, rows


def main() -> None:
    args = parse_args()
    if args.n_samples < 1:
        raise ValueError("--n-samples must be >= 1")
    if args.step is not None:
        args.steps = [args.step]

    if args.include_base_model and not args.base_model_path:
        raise ValueError("--include-base-model requires --base-model-path")

    ckpt_root = args.ckpt_root.resolve() if args.ckpt_root is not None else None
    parsed_ckpt = parse_ckpt_name(ckpt_root)
    if ckpt_root is not None and parsed_ckpt is None:
        print(
            "WARNING: ckpt-root basename does not match {algo}-{model}-{train_dataset}-{timestamp}. "
            f"got={ckpt_root.name}"
        )
    dataset_path = args.dataset_path.resolve()
    benchmark_name = sanitize_filename_tag(resolve_benchmark_name(args, dataset_path))
    merged_model_root = args.merged_model_root.resolve()
    resolved_model_name = args.model_name or (parsed_ckpt.model if parsed_ckpt is not None else None)
    resolved_train_dataset = args.train_dataset or (
        parsed_ckpt.train_dataset if parsed_ckpt is not None else "unknown-train-dataset"
    )
    default_run_name = ckpt_root.name if ckpt_root is not None else time.strftime("%Y%m%d_%H%M%S")
    run_name = sanitize_filename_tag(args.run_name or default_run_name)
    timestamp_tag = run_name
    if args.output_root is not None:
        output_root = args.output_root.resolve()
        output_path = output_root / "results_benchmark.json"
    else:
        output_path = args.output.resolve()
        output_root = output_path.parent
    output_root.mkdir(parents=True, exist_ok=True)
    model_name_tag = infer_model_name_tag(resolved_model_name, ckpt_root, args.base_model_path)
    train_dataset_tag = sanitize_filename_tag(resolved_train_dataset)
    run_output_dir = make_run_output_dir(output_root=output_root, run_name=run_name)
    scoped_output_path = run_output_dir / output_path.name
    latest_output_path = output_root / f"{output_path.stem}__latest{output_path.suffix}"

    step_dirs: list[Path] = []
    inspections: list[dict[str, Any]] = []
    if ckpt_root is not None:
        step_dirs = discover_steps(ckpt_root, args.steps)
        for step_dir in tqdm(step_dirs, desc="Inspect checkpoints", disable=not args.show_progress):
            inspections.append(inspect_checkpoint(step_dir))
    elif args.steps:
        raise ValueError("--steps/--step requires --ckpt-root")

    if not step_dirs and not args.include_base_model:
        raise RuntimeError("No evaluation target found. Provide --ckpt-root and/or --include-base-model")

    print("\n[Checkpoint Inspection]")
    for item in inspections:
        print(json.dumps(item, ensure_ascii=False))
    if args.include_base_model:
        base_info = {
            "step_dir": None,
            "step_name": args.base_step_name,
            "source": "base_model",
            "base_model_path": args.base_model_path,
            "hf_dir_exists": True,
            "num_hf_weight_files": None,
        }
        print(json.dumps(base_info, ensure_ascii=False))

    if args.inspect_only:
        payload = {
            "ckpt_root": str(ckpt_root) if ckpt_root is not None else None,
            "benchmark_name": benchmark_name,
            "dataset_path": str(dataset_path),
            "prompt_keys": list(args.prompt_key) if args.prompt_key else list(DEFAULT_PROMPT_KEYS),
            "answer_key": list(args.answer_key) if args.answer_key else [DEFAULT_ANSWER_KEY],
            "prompt_suffix": args.prompt_suffix,
            "scorer": "math_reward",
            "inspect_only": True,
            "checkpoints": inspections,
            "base_model": {
                "enabled": args.include_base_model,
                "base_model_path": args.base_model_path,
                "step_name": args.base_step_name,
            },
        }
        scoped_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.no_latest_summary:
            latest_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved inspection report: {scoped_output_path}")
        if not args.no_latest_summary:
            print(f"Saved latest summary link: {latest_output_path}")
        return

    prompt_keys = tuple(args.prompt_key) if args.prompt_key else DEFAULT_PROMPT_KEYS
    answer_keys = tuple(args.answer_key) if args.answer_key else (DEFAULT_ANSWER_KEY,)
    samples = read_dataset(
        dataset_path=dataset_path,
        limit=args.limit,
        prompt_keys=prompt_keys,
        answer_keys=answer_keys,
        prompt_suffix=args.prompt_suffix,
    )
    print(f"\nLoaded {len(samples)} evaluation samples from {dataset_path}")

    run_results: list[dict[str, Any]] = []
    base_results: list[dict[str, Any]] = []
    eval_targets: list[dict[str, Any]] = []
    for step_dir, inspection in zip(step_dirs, inspections):
        eval_targets.append(
            {
                "type": "checkpoint",
                "step_name": step_dir.name,
                "step_dir": step_dir,
                "inspection": inspection,
            }
        )
    if args.include_base_model:
        eval_targets.append(
            {
                "type": "base_model",
                "step_name": args.base_step_name,
                "step_dir": None,
                "inspection": {
                    "step_dir": None,
                    "source": "base_model",
                    "base_model_path": args.base_model_path,
                },
            }
        )

    for target in tqdm(
        eval_targets,
        total=len(eval_targets),
        desc=f"Evaluate steps ({benchmark_name})",
        disable=not args.show_progress,
    ):
        target_type = target["type"]
        step_name = target["step_name"]
        step_dir = target["step_dir"]
        inspection = target["inspection"]
        model_ref: str | None = None
        skip_reason: str | None = None

        if target_type == "base_model":
            model_ref = str(args.base_model_path)
        else:
            actor_dir = step_dir / "actor"
            hf_dir = actor_dir / "huggingface"
            if inspection["num_hf_weight_files"] > 0:
                model_ref = str(hf_dir)
            elif args.auto_merge_fsdp:
                print(f"\n[{step_name}] HF weights not found, merging FSDP shards...")
                try:
                    model_ref = str(
                        maybe_merge_fsdp_checkpoint(actor_dir=actor_dir, merged_model_root=merged_model_root)
                    )
                except subprocess.CalledProcessError as exc:
                    skip_reason = f"FSDP merge failed: {exc}"
            else:
                skip_reason = "HF weight files missing under actor/huggingface (try --auto-merge-fsdp)"

        result: dict[str, Any] = {
            "step_dir": str(step_dir) if step_dir is not None else None,
            "inspection": inspection,
            "backend": BACKEND_NAME,
            "benchmark": benchmark_name,
            "scorer": "math_reward",
            "source": target_type,
        }

        if model_ref is None:
            result["status"] = "skipped"
            result["reason"] = skip_reason
            print(f"\n[{step_name}] skipped: {skip_reason}")
            if args.save_step_results:
                step_file = write_step_result_file(
                    output_dir=run_output_dir,
                    benchmark_name=benchmark_name,
                    model_name_tag=model_name_tag,
                    source_tag=target_type,
                    step_name=step_name,
                    step_result=result,
                )
                result["step_result_path"] = str(step_file)
            if target_type == "base_model":
                base_results.append(result)
            else:
                run_results.append(result)
            continue

        print(f"\n[{step_name}] evaluating with model: {model_ref}")
        try:
            accuracy, rows = evaluate_one_checkpoint(
                model_ref=model_ref,
                samples=samples,
                args=args,
            )
        except Exception as exc:  # noqa: BLE001
            result["status"] = "error"
            reason = str(exc) or repr(exc)
            result["reason"] = reason
            print(f"[{step_name}] evaluation failed: {reason}")
            if args.save_step_results:
                step_file = write_step_result_file(
                    output_dir=run_output_dir,
                    benchmark_name=benchmark_name,
                    model_name_tag=model_name_tag,
                    source_tag=target_type,
                    step_name=step_name,
                    step_result=result,
                )
                result["step_result_path"] = str(step_file)
            if target_type == "base_model":
                base_results.append(result)
            else:
                run_results.append(result)
            continue

        result["status"] = "ok"
        result["model_path"] = model_ref
        result["num_samples"] = len(samples)
        result["accuracy"] = accuracy
        metric_summary = _compute_eval_metrics(
            rows=rows,
            n_samples=args.n_samples,
            seed=args.seed,
        )
        result["metrics"] = metric_summary
        result["accuracy"] = float(metric_summary.get(f"acc/avg@{args.n_samples}", accuracy))
        print(
            f"[{step_name}] avg@{args.n_samples}={result['accuracy']:.4f} "
            f"({len(samples)} prompts x {args.n_samples} samples)"
        )
        if args.n_samples > 1:
            pass_k = metric_summary.get(f"acc/pass@{args.n_samples}/mean")
            maj_k = metric_summary.get(f"acc/maj@{args.n_samples}/mean")
            if pass_k is not None:
                print(f"[{step_name}] pass@{args.n_samples}={pass_k:.4f}")
            if maj_k is not None:
                print(f"[{step_name}] maj@{args.n_samples}={maj_k:.4f}")

        if args.save_predictions:
            pred_path = run_output_dir / f"predictions_{benchmark_name}_{model_name_tag}_{target_type}_{step_name}.jsonl"
            with pred_path.open("w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            result["predictions_path"] = str(pred_path)

        if args.save_step_results:
            step_file = write_step_result_file(
                output_dir=run_output_dir,
                benchmark_name=benchmark_name,
                model_name_tag=model_name_tag,
                source_tag=target_type,
                step_name=step_name,
                step_result=result,
            )
            result["step_result_path"] = str(step_file)

        if target_type == "base_model":
            base_results.append(result)
        else:
            run_results.append(result)

    payload = {
        "ckpt_root": str(ckpt_root) if ckpt_root is not None else None,
        "benchmark_name": benchmark_name,
        "dataset_path": str(dataset_path),
        "prompt_keys": list(prompt_keys),
        "answer_key": list(answer_keys),
        "prompt_suffix": args.prompt_suffix,
        "scorer": "math_reward",
        "num_checkpoints": len(step_dirs),
        "include_base_model": args.include_base_model,
        "base_model_path": args.base_model_path,
        "base_step_name": args.base_step_name,
        "num_eval_targets": len(eval_targets),
        "num_samples": len(samples),
        "n_samples_per_prompt": args.n_samples,
        "seed": args.seed,
        "run_name": run_name,
        "timestamp_tag": timestamp_tag,
        "train_dataset": train_dataset_tag,
        "ckpt_name_parsed": (
            {
                "algorithm": parsed_ckpt.algorithm,
                "model": parsed_ckpt.model,
                "train_dataset": parsed_ckpt.train_dataset,
                "timestamp": parsed_ckpt.timestamp,
            }
            if parsed_ckpt is not None
            else None
        ),
        "backend": BACKEND_NAME,
        "model_name": model_name_tag,
        "run_output_dir": str(run_output_dir),
        "results": run_results,
    }
    if base_results:
        payload["num_base_eval_targets"] = len(base_results)
        payload["base_results"] = [
            {
                "status": item.get("status"),
                "reason": item.get("reason"),
                "step_result_path": item.get("step_result_path"),
            }
            for item in base_results
        ]
        base_summary_paths = [
            write_base_summary_file(
                output_dir=run_output_dir,
                benchmark_name=benchmark_name,
                model_name_tag=model_name_tag,
                base_model_path=args.base_model_path,
                base_step_name=args.base_step_name,
                seed=args.seed,
                n_samples=args.n_samples,
                result=item,
            )
            for item in base_results
        ]
        payload["base_summary_paths"] = [str(path) for path in base_summary_paths]
    scoped_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_latest_summary:
        latest_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved evaluation report: {scoped_output_path}")
    if not args.no_latest_summary:
        print(f"Saved latest summary link: {latest_output_path}")


if __name__ == "__main__":
    main()
