import os
import glob
from preprocessor import process_file

# Target files to process
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run batch preprocessing.")
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM chunking and just split sentences")
    parser.add_argument("--folder", type=str, help="Folder containing files to process", required=True)
    parser.add_argument("--ext", type=str, help="Comma-separated file extensions to look for (default: *.jsonl,*.json)", default="*.jsonl,*.json")
    parser.add_argument("--label-dir", help="Directory to save label JSON files", default="data/content_unit")
    parser.add_argument("--raw-dir", help="Directory to save raw JSON files", default="data/raw")
    parser.add_argument("--raw-only", action="store_true", help="Only save raw files to raw_dir, skip creating label files")
    parser.add_argument("--workers", type=int, help="Number of concurrent workers (default: 10)", default=10)
    parser.add_argument("--model", type=str, help="Model to use for LLM chunking (default: google/gemini-2.5-flash-lite)", default="google/gemini-2.5-flash-lite")
    parser.add_argument("--all-trials", action="store_true", help="Process all trials instead of just the first one")
    parser.add_argument("--resume", action="store_true", help="Resume: skip problem IDs that have output files with good coverage in label-dir, rerun others")
    # vLLM backend options (mirrors run_stateless_hf_batch.sh)
    parser.add_argument("--backend", type=str, choices=["openrouter", "vllm"], default="openrouter",
                        help="Inference backend: 'openrouter' (default) uses OpenRouter API, 'vllm' uses local vLLM engine")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9,
                        help="[vLLM] Fraction of GPU memory for KV cache (default: 0.9)")
    parser.add_argument("--max-model-len", type=int, default=16384,
                        help="[vLLM] Maximum sequence length (default: 16384)")
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="[vLLM] Sampling temperature (default: 0.7)")
    parser.add_argument("--max-tokens", type=int, default=4096,
                        help="[vLLM] Maximum output tokens per request (default: 4096)")
    parser.add_argument("--top-p", type=float, default=0.8,
                        help="[vLLM] Top-p nucleus sampling (default: 0.8)")
    parser.add_argument("--top-k", type=int, default=20,
                        help="[vLLM] Top-k sampling (default: 20)")
    parser.add_argument("--min-p", type=float, default=0.0,
                        help="[vLLM] Min-p sampling (default: 0.0)")
    parser.add_argument("--presence-penalty", type=float, default=1.5,
                        help="[vLLM] Presence penalty (default: 1.5)")
    parser.add_argument("--repetition-penalty", type=float, default=1.0,
                        help="[vLLM] Repetition penalty (default: 1.0)")
    args = parser.parse_args()

    print("Starting batch preprocessing...")
    if args.backend == "vllm":
        print(f"Backend: vLLM (model={args.model}, gpu_util={args.gpu_memory_utilization}, max_model_len={args.max_model_len})")
    else:
        print(f"Backend: OpenRouter (model={args.model})")
    
    if not os.path.exists(args.folder):
        print(f"Folder not found: {args.folder}")
        return
        
    target_files = []
    for ext in args.ext.split(','):
        target_files.extend(glob.glob(os.path.join(args.folder, ext.strip())))
    if not target_files:
        print(f"No files matching {args.ext} found in {args.folder}")
        return

    for file_path in target_files:
        if os.path.exists(file_path):
            print(f"Processing: {file_path}")
            try:
                process_file(
                    file_path,
                    args.label_dir,
                    args.raw_dir,
                    skip_llm=args.skip_llm,
                    raw_only=args.raw_only,
                    max_workers=args.workers,
                    model=args.model,
                    all_trials=args.all_trials,
                    resume=args.resume,
                    backend=args.backend,
                    gpu_memory_utilization=args.gpu_memory_utilization,
                    max_model_len=args.max_model_len,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    top_p=args.top_p,
                    top_k=args.top_k,
                    min_p=args.min_p,
                    presence_penalty=args.presence_penalty,
                    repetition_penalty=args.repetition_penalty,
                )
            except Exception as e:
                import traceback
                print(f"Failed to process {file_path}: {e}")
                traceback.print_exc()
        else:
            print(f"File not found: {file_path}")
    print("Batch preprocessing completed.")

if __name__ == "__main__":
    main()
