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
parser.add_argument('--chunk_dir', type=str, required=True, help='Path to directory containing chunked JSON files already tagged with heuristics')
parser.add_argument('--raw_file', type=str, required = True, help='Optional path to raw data file for problem text')
parser.add_argument('--output_dir', type=str, default=None, help='Optional custom output directory')
parser.add_argument('--without_llm', action='store_true', help='Skip LLM for semantic space tagging and use heuristics H1-H4 instead')
parser.add_argument('--max_workers', type=int, default=10, help='Maximum number of parallel workers (default: 10)')

def process_chunk_file(chunk_file_path, idx, model, problem_text, guidebook=True, output_path=None, without_llm=False):
    """Process a chunked JSON file to add Semantic Space tracking"""
    client = None
    if not without_llm:
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
                without_llm=without_llm
            )
            break
        except Exception as e:
            print(f"Error processing semantic space file {idx}: {e}")
            retry_count += 1
            import traceback
            traceback.print_exc()
            continue

def check_exist(idx, output_dir):
    file_path = os.path.join(output_dir, f"{idx + 1}.json")
    return os.path.exists(file_path)

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
    
    raw_data = []
    if args.raw_file:
        if not os.path.exists(args.raw_file):
            print(f"Error: Raw file not found: {args.raw_file}")
            import sys; sys.exit(1)
        if not os.path.isfile(args.raw_file):
            print(f"Error: --raw_file must be a file, but a directory was provided: {args.raw_file}")
            import sys; sys.exit(1)
            
        with open(args.raw_file, 'r') as f:
            raw_data = json.load(f)
        print(f"Loaded {len(raw_data)} problems from raw data file: {args.raw_file}")
    else:
        print("Will use placeholder problem text")
    
    chunk_dir_name = os.path.basename(args.chunk_dir.rstrip('/'))
    sanitized_model = args.annotate_model.replace('/', '-')
    if args.without_llm:
        sanitized_model += "_without_llm"

    if args.output_dir:
        output_base = os.path.join(args.output_dir, chunk_dir_name, sanitized_model)
    else:
        # Default: .../data/llm-label-semantic-space/...
        output_base = f"/home/jongsong/math-skills-llm/data/llm-label-semantic-space/{sanitized_model}/{chunk_dir_name}"
    
    print(f"Output directory: {output_base}")
    os.makedirs(output_base, exist_ok=True)
    
    # Build list of tasks to process (skip already-done files)
    tasks = []
    for chunk_file in chunk_files:
        try:
            # Assumes file logic matches label.py: "1.json" -> idx 0
            file_idx_str = os.path.splitext(chunk_file)[0]
            if not file_idx_str.isdigit():
                print(f"Warning: Skipping file with non-numeric name: {chunk_file}")
                continue
            file_idx = int(file_idx_str) - 1
        except Exception:
            print(f"Warning: error parsing filename {chunk_file}")
            continue

        if check_exist(file_idx, output_base):
            print(f"Skipping {chunk_file} (already processed)")
            continue

        chunk_file_path = os.path.join(args.chunk_dir, chunk_file)

        problem_text = ""
        if file_idx < len(raw_data):
            problem_text = raw_data[file_idx].get('Instruction', '')
        if not problem_text:
            problem_text = f"Problem for {chunk_file} (no raw data available)"

        tasks.append((chunk_file, chunk_file_path, file_idx, problem_text))

    print(f"Processing {len(tasks)} files in parallel (max {args.max_workers} workers)...")

    def _run(task):
        chunk_file, chunk_file_path, file_idx, problem_text = task
        print(f"Processing abstract semantic space tracking for file {chunk_file} (index {file_idx})...")
        process_chunk_file(
            chunk_file_path=chunk_file_path,
            idx=file_idx,
            model=args.annotate_model,
            problem_text=problem_text,
            guidebook=True,
            output_path=output_base,
            without_llm=args.without_llm
        )
        return chunk_file

    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {executor.submit(_run, task): task[0] for task in tasks}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Semantic Space"):
            chunk_file = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"Error in {chunk_file}: {e}")

    print(f"\nSemantic Space Processing complete! Results saved to: {output_base}")

if __name__ == '__main__':
    main()
