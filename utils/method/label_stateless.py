from openai import OpenAI
import os
import json
from tqdm import tqdm
import concurrent.futures
try:
    from .utils import process_new_data_chunks_stateless, process_new_data_chunks_stateless_batch
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from utils import process_new_data_chunks_stateless, process_new_data_chunks_stateless_batch

import argparse
from dotenv import load_dotenv

# Load environment variables from .env file in project root
env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(dotenv_path=env_path)

parser = argparse.ArgumentParser()
parser.add_argument('--annotate_model', type=str, default='gpt-5', help='Model to use for annotation (e.g., google/gemini-2.5-flash-lite)')
parser.add_argument('--backend', type=str, default='vllm', choices=['vllm', 'openrouter'], help='Inference backend (default: vllm)')
parser.add_argument('--use_hf', action='store_true', help='[DEPRECATED] Use local model via vLLM (use --backend vllm)')
parser.add_argument('--hf_model', type=str, default=None, help='Specific HF model ID if different from annotate_model')
parser.add_argument('--chunk_dir', type=str, required=True, help='Path to directory or a single JSON file')
parser.add_argument('--raw_file', type=str, required=True, help='Path to raw data file for problem text')
parser.add_argument('--output_dir', type=str, default=None, help='Optional custom output directory')
parser.add_argument('--vllm_batch', action='store_true', help='Use vLLM batch inference (all chunks in one generate() call per sample)')
# vLLM engine options
parser.add_argument('--gpu_memory_utilization', type=float, default=0.9, help='Fraction of GPU memory for vLLM KV cache (default: 0.9)')
parser.add_argument('--max_model_len', type=int, default=16384, help='Maximum sequence length for vLLM (default: 16384)')
# Sampling parameters
parser.add_argument('--temperature', type=float, default=0.6, help='Sampling temperature (default: 0.6)')
parser.add_argument('--max_tokens', type=int, default=4096, help='Maximum output tokens per request (default: 4096)')
parser.add_argument('--top_p', type=float, default=0.95, help='Top-p nucleus sampling (default: 0.95)')
parser.add_argument('--top_k', type=int, default=20, help='Top-k sampling (default: 20)')
parser.add_argument('--min_p', type=float, default=0.0, help='Min-p sampling (default: 0.0)')
parser.add_argument('--presence_penalty', type=float, default=0.0, help='Presence penalty (default: 0.0)')
parser.add_argument('--repetition_penalty', type=float, default=1.0, help='Repetition penalty (default: 1.0)')

def process_chunk_file(chunk_file_path, idx, model, problem_text, guidebook=True, output_path=None, backend='vllm', hf_client=None, output_filename=None):
    """Process a chunked JSON file using STATELESS mode"""
    if backend == 'vllm':
        if hf_client is None:
            raise ValueError("hf_client must be provided when backend is vllm")
        client = hf_client
        print(f"✓ Using vLLM (Stateless) with model: {model}")
    else:
        # Use OpenRouter for all models
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is not set. Please set it in .env file or environment.")
        
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        print(f"✓ Using OpenRouter (Stateless) with model: {model}")
    
    max_retry = 10
    retry_count = 0
    while retry_count < max_retry:
        try:
            # Load existing chunks
            with open(chunk_file_path, 'r') as f:
                chunk_list = json.load(f)
            
            # Process chunks STATELESSLY
            process_new_data_chunks_stateless(
                client=client,
                chunk_list=chunk_list,
                problem_text=problem_text,
                sample_index=idx,
                model=model,
                guidebook=guidebook,
                output_path=output_path,
                output_filename=output_filename,
            )
            break
        except Exception as e:
            print(f"Error processing chunk file {idx}: {e}")
            retry_count += 1
            import traceback
            traceback.print_exc()
            continue

def process_chunk_file_batch(chunk_file_path, idx, model, problem_text, guidebook=True, output_path=None, hf_client=None, output_filename=None):
    """Process a chunked JSON file using STATELESS BATCH mode (vLLM only)."""
    if hf_client is None:
        raise ValueError("hf_client must be provided for batch processing")

    max_retry = 10
    retry_count = 0
    while retry_count < max_retry:
        try:
            with open(chunk_file_path, 'r') as f:
                chunk_list = json.load(f)

            process_new_data_chunks_stateless_batch(
                hf_client=hf_client,
                chunk_list=chunk_list,
                problem_text=problem_text,
                sample_index=idx,
                model=model,
                guidebook=guidebook,
                output_path=output_path,
                output_filename=output_filename,
            )
            break
        except Exception as e:
            print(f"Error processing chunk file {idx}: {e}")
            retry_count += 1
            import traceback
            traceback.print_exc()
            continue


def check_exist(idx, output_dir):
    file_path = os.path.join(output_dir, f"{idx + 1}.json")
    return os.path.exists(file_path)

def main():
    args = parser.parse_args()

    # Back-compat: --use_hf implies vLLM backend
    if args.use_hf:
        args.backend = 'vllm'
    
    if not os.path.exists(args.chunk_dir):
        print(f"Error: Path not found: {args.chunk_dir}")
        return
    
    if os.path.isfile(args.chunk_dir):
        print(f"Processing single chunk file (Stateless): {args.chunk_dir}")
        target_file = os.path.basename(args.chunk_dir)
        args.chunk_dir = os.path.dirname(args.chunk_dir)
        chunk_files = [target_file]
    else:
        print(f"Processing chunks (Stateless) from: {args.chunk_dir}")
        chunk_files = sorted([f for f in os.listdir(args.chunk_dir) if f.endswith('.json')])
    
    if not chunk_files:
        print(f"Error: No JSON files found in {args.chunk_dir}")
        try:
           import sys; sys.exit(1)
        except: return 
    
    print(f"Found {len(chunk_files)} chunk files to process")
    
    if not args.raw_file or not os.path.exists(args.raw_file):
        print(f"Error: Raw file not found or not provided: {args.raw_file}")
        return

    with open(args.raw_file, 'r') as f:
        raw_list = json.load(f)
    print(f"Loaded {len(raw_list)} problems from raw data file: {args.raw_file}")
    
    # Build dictionary mapping integer index (0-indexed) to problem text.
    # ID may be a plain int or a "<problem>_<rollout>" string; prefer
    # `original_index` when present, then fall back to the problem part of `index`/`problem_id`.
    ID_KEYS   = ('original_index', 'index', 'problem_id')
    TEXT_KEYS = ('Instruction', 'problem', 'final_prompt')
    raw_data = {}
    for item in raw_list:
        id_val = next((item[k] for k in ID_KEYS if k in item), None)
        if id_val is None:
            continue
        problem_part = str(id_val).split("_")[0]
        if not problem_part.isdigit():
            continue
        idx = int(problem_part)
        text = next((item[k] for k in TEXT_KEYS if item.get(k)), '')
        raw_data[idx] = text
    
    
    chunk_dir_name = os.path.basename(args.chunk_dir.rstrip('/'))
    sanitized_model = args.annotate_model.replace('/', '-')

    if args.output_dir:
        output_base = os.path.join(args.output_dir, chunk_dir_name, sanitized_model)
    else:
        # Default: .../data/llm-label-stateless/...
        output_base = f"/home/jongsong/math-skills-annotations/data/llm-label-stateless/{chunk_dir_name}/{sanitized_model}"
    
    print(f"Output directory: {output_base}")
    os.makedirs(output_base, exist_ok=True)
    
    hf_client = None
    if args.backend == 'vllm':
        try:
            from hf_client import HFClient
        except ImportError:
            # Handle if executed as module or directly
            try:
                from .hf_client import HFClient
            except ImportError:
                import sys
                sys.path.append(os.path.dirname(os.path.abspath(__file__)))
                from hf_client import HFClient
        
        hf_model_id = args.hf_model if args.hf_model else args.annotate_model
        hf_client = HFClient(
            hf_model_id,
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

    def resolve_chunk_file(chunk_file):
        """Return (file_idx, chunk_file_path, problem_text, output_filename) or None if skipped.

        Supported filename formats:
          - ``<problem_idx>.json``          e.g. 42.json
          - ``<problem_idx>_<rollout>.json`` e.g. 1_14.json
        The original filename (stem + .json) is preserved as the output filename.
        """
        try:
            stem = os.path.splitext(chunk_file)[0]   # e.g. "1_14"
            problem_part = stem.split("_")[0]         # e.g. "1"
            if not problem_part.isdigit():
                print(f"Warning: Skipping file with non-numeric name: {chunk_file}")
                return None
            file_idx = int(problem_part)
        except Exception:
            print(f"Warning: error parsing filename {chunk_file}")
            return None

        output_filename = stem + ".json"              # preserve original name e.g. "1_14.json"

        if os.path.exists(os.path.join(output_base, output_filename)):
            print(f"Skipping {chunk_file} (already processed)")
            return None

        chunk_file_path = os.path.join(args.chunk_dir, chunk_file)
        problem_text = raw_data.get(file_idx, "")
        if not problem_text:
            print(f"Error: Problem text missing for index {file_idx} in raw data file.")
            return None

        return file_idx, chunk_file_path, problem_text, output_filename

    use_vllm_batch = args.backend == 'vllm' and args.vllm_batch

    if use_vllm_batch:
        print("Using vLLM BATCH inference mode (all chunks per sample in one generate() call).")
        for chunk_file in tqdm(chunk_files, desc="Processing (vLLM batch)"):
            resolved = resolve_chunk_file(chunk_file)
            if resolved is None:
                continue
            file_idx, chunk_file_path, problem_text, output_filename = resolved
            print(f"Processing chunk file {chunk_file} (index {file_idx}) [batch]...")
            process_chunk_file_batch(
                chunk_file_path=chunk_file_path,
                idx=file_idx,
                model=args.annotate_model,
                problem_text=problem_text,
                guidebook=True,
                output_path=output_base,
                hf_client=hf_client,
                output_filename=output_filename,
            )
    else:
        def process_one(chunk_file):
            resolved = resolve_chunk_file(chunk_file)
            if resolved is None:
                return
            file_idx, chunk_file_path, problem_text, output_filename = resolved
            print(f"Processing chunk file {chunk_file} (index {file_idx})...")
            process_chunk_file(
                chunk_file_path=chunk_file_path,
                idx=file_idx,
                model=args.annotate_model,
                problem_text=problem_text,
                guidebook=True,
                output_path=output_base,
                backend=args.backend,
                hf_client=hf_client,
                output_filename=output_filename,
            )

        if args.backend == 'vllm':
            max_workers = 1
            print("Using sequential processing (max_workers=1) for vLLM backend.")
        else:
            max_workers = min(10, len(chunk_files))
            print(f"Running with up to {max_workers} parallel workers...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_one, cf): cf for cf in chunk_files}
            for future in tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc="Processing"):
                cf = futures[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"Error in {cf}: {e}")

    print(f"\nStateless Processing complete! Results saved to: {output_base}")

if __name__ == '__main__':
    main()
