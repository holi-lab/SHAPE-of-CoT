from openai import OpenAI
import os
import json
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    from .utils import process_semantic_space_chunks
except ImportError:
    # Handle direct execution (without module context) if attempted
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from utils import process_semantic_space_chunks

import argparse
from dotenv import load_dotenv

# Load environment variables from .env file in project root
env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(dotenv_path=env_path)

parser = argparse.ArgumentParser()
parser.add_argument('--annotate_model', type=str, default='x-ai/grok-4.1-fast', help='Model to use for annotation (e.g., google/gemini-2.5-flash-lite)')
parser.add_argument('--backend', type=str, default='openrouter', choices=['vllm', 'openrouter'], help='Inference backend (default: openrouter)')
parser.add_argument('--hf_model', type=str, default=None, help='Specific HF model ID if different from annotate_model')
parser.add_argument('--chunk_dir', type=str, required=True, help='Path to directory containing chunked JSON files already tagged with heuristics')
parser.add_argument('--raw_file', type=str, required=True, help='Path to raw data file for problem text')
parser.add_argument('--output_dir', type=str, default=None, help='Optional custom output directory')
parser.add_argument('--without_llm', action='store_true', help='Skip LLM for semantic space tagging and use heuristics H1-H4 instead')
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

def process_chunk_file(chunk_file_path, idx, model, problem_text, guidebook=True, output_path=None, without_llm=False, backend='openrouter', hf_client=None, output_filename=None):
    """Process a chunked JSON file to add Semantic Space tracking"""
    client = None
    if not without_llm:
        if backend == 'vllm':
            if hf_client is None:
                raise ValueError("hf_client must be provided when backend is vllm")
            client = hf_client
            print(f"✓ Using vLLM for Semantic Space tracking with model: {model}")
        else:
            # Use OpenRouter for all models
            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY environment variable is not set. Please set it in .env file or environment.")
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key
            )
            print(f"✓ Using OpenRouter for Semantic Space tracking with model: {model}")
    else:
        print(f"✓ Skip LLM for Semantic Space tracking (without_llm=True)")
    
    max_retry = 10
    retry_count = 0
    while retry_count < max_retry:
        try:
            # Load existing heuristically tagged chunks
            with open(chunk_file_path, 'r') as f:
                chunk_list = json.load(f)
            
            # Process Semantic Space chunks STATEFULLY
            process_semantic_space_chunks(
                client=client,
                chunk_list=chunk_list,
                problem_text=problem_text,
                sample_index=idx,
                model=model,
                guidebook=guidebook,
                output_path=output_path,
                without_llm=without_llm,
                output_filename=output_filename,
            )
            break
        except Exception as e:
            print(f"Error processing semantic space file {idx}: {e}")
            retry_count += 1
            import traceback
            traceback.print_exc()
            continue

def check_exist(output_dir, output_filename):
    return os.path.exists(os.path.join(output_dir, output_filename))

def main():
    args = parser.parse_args()
    
    if not os.path.exists(args.chunk_dir):
        print(f"Error: Chunk directory not found: {args.chunk_dir}")
        return
    
    print(f"Processing Semantic Space chunks from: {args.chunk_dir}")
    
    chunk_files = sorted([f for f in os.listdir(args.chunk_dir) if f.endswith('.json')])
    
    if not chunk_files:
        print(f"Error: No JSON files found in {args.chunk_dir}")
        try:
           import sys; sys.exit(1)
        except: return 
    
    print(f"Found {len(chunk_files)} chunk files to process for semantic space")
    
    if not os.path.exists(args.raw_file):
        print(f"Error: Raw file not found: {args.raw_file}")
        import sys; sys.exit(1)
    if not os.path.isfile(args.raw_file):
        print(f"Error: --raw_file must be a file, but a directory was provided: {args.raw_file}")
        import sys; sys.exit(1)

    with open(args.raw_file, 'r') as f:
        raw_list = json.load(f)
    print(f"Loaded {len(raw_list)} problems from raw data file: {args.raw_file}")

    # Build dictionary mapping integer index (0-indexed) to problem text.
    # Supports two raw-file schemas:
    #   Schema A: {'index': <1-based int>, 'Instruction': <str>, ...}
    #   Schema B: {'problem_id': <1-based int>, 'problem': <str>, ...}
    ID_KEYS   = ('index', 'problem_id')
    TEXT_KEYS = ('Instruction', 'problem', 'final_prompt')
    raw_data = {}
    for item in raw_list:
        id_val = next((item[k] for k in ID_KEYS if k in item), None)
        if id_val is None:
            continue
        idx = int(id_val) - 1
        text = next((item[k] for k in TEXT_KEYS if item.get(k)), '')
        raw_data[idx] = text
    
    chunk_dir_abs = os.path.abspath(args.chunk_dir.rstrip('/'))
    chunk_dir_name = os.path.basename(chunk_dir_abs)
    # Use the parent directory as the dataset name.
    # When chunk_dir is a model subfolder (e.g. .../dataset/heuristic_model/),
    # this gives the original dataset name (e.g. olmo-3-7b_math_perturb_hard_result_correct).
    # Falls back to chunk_dir_name if there is no meaningful parent.
    parent_dir_name = os.path.basename(os.path.dirname(chunk_dir_abs))
    dataset_name = parent_dir_name if parent_dir_name else chunk_dir_name

    sanitized_model = args.annotate_model.replace('/', '-')
    if args.without_llm:
        sanitized_model += "_without_llm"

    if args.output_dir:
        output_base = os.path.join(args.output_dir, dataset_name, sanitized_model)
    else:
        # Default: .../data/llm-label-semantic-space/...
        output_base = f"/home/jongsong/math-skills-llm/data/llm-label-semantic-space/{dataset_name}/{sanitized_model}"
    
    print(f"Output directory: {output_base}")
    os.makedirs(output_base, exist_ok=True)

    # Initialize vLLM client if needed
    hf_client = None
    if not args.without_llm and args.backend == 'vllm':
        try:
            from hf_client import HFClient
        except ImportError:
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
        The original filename is preserved as the output filename.
        """
        try:
            stem = os.path.splitext(chunk_file)[0]
            problem_part = stem.split("_")[0]
            if not problem_part.isdigit():
                print(f"Warning: Skipping file with non-numeric name: {chunk_file}")
                return None
            file_idx = int(problem_part) - 1
        except Exception:
            print(f"Warning: error parsing filename {chunk_file}")
            return None

        output_filename = stem + ".json"  # preserve original name e.g. "1_14.json"

        if check_exist(output_base, output_filename):
            print(f"Skipping {chunk_file} (already processed)")
            return None

        chunk_file_path = os.path.join(args.chunk_dir, chunk_file)
        problem_text = raw_data.get(file_idx, "")
        if not problem_text:
            problem_text = f"Problem for {chunk_file} (no raw data available)"

        return file_idx, chunk_file_path, problem_text, output_filename

    def _run(chunk_file):
        resolved = resolve_chunk_file(chunk_file)
        if resolved is None:
            return chunk_file
        file_idx, chunk_file_path, problem_text, output_filename = resolved
        print(f"Processing abstract semantic space tracking for file {chunk_file} (index {file_idx})...")
        process_chunk_file(
            chunk_file_path=chunk_file_path,
            idx=file_idx,
            model=args.annotate_model,
            problem_text=problem_text,
            guidebook=True,
            output_path=output_base,
            without_llm=args.without_llm,
            backend=args.backend,
            hf_client=hf_client,
            output_filename=output_filename,
        )
        return chunk_file

    if args.backend == 'vllm' and not args.without_llm:
        max_workers = 1
        print("Using sequential processing (max_workers=1) for vLLM backend.")
    else:
        max_workers = min(10, len(chunk_files))
        print(f"Processing {len(chunk_files)} files with up to {max_workers} parallel workers...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run, cf): cf for cf in chunk_files}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Semantic Space"):
            chunk_file = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"Error in {chunk_file}: {e}")

    print(f"\nSemantic Space Processing complete! Results saved to: {output_base}")

if __name__ == '__main__':
    main()
