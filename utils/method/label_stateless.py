from openai import OpenAI
import os
import json
from tqdm import tqdm
import concurrent.futures
try:
    from .utils import process_new_data_chunks_stateless
except ImportError:
    # Handle direct execution (without module context) if attempted
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from utils import process_new_data_chunks_stateless

import argparse
from dotenv import load_dotenv

# Load environment variables from .env file in project root
env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(dotenv_path=env_path)

parser = argparse.ArgumentParser()
parser.add_argument('--annotate_model', type=str, default='gpt-5', help='Model to use for annotation (e.g., google/gemini-2.5-flash-lite)')
parser.add_argument('--use_hf', action='store_true', help='Use local Hugging Face model for inference')
parser.add_argument('--hf_model', type=str, default=None, help='Specific HF model ID if different from annotate_model')
parser.add_argument('--chunk_dir', type=str, required=True, help='Path to directory or a single JSON file')
parser.add_argument('--raw_file', type=str, required=True, help='Path to raw data file for problem text')
parser.add_argument('--output_dir', type=str, default=None, help='Optional custom output directory')

def process_chunk_file(chunk_file_path, idx, model, problem_text, guidebook=True, output_path=None, use_hf=False, hf_client=None):
    """Process a chunked JSON file using STATELESS mode"""
    if use_hf:
        if hf_client is None:
            raise ValueError("hf_client must be provided when use_hf is True")
        client = hf_client
        print(f"✓ Using Local HF (Stateless) with model: {model}")
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
                output_path=output_path
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
    
    # Build dictionary mapping integer index (0-indexed) to problem text
    raw_data = {}
    for item in raw_list:
        if 'index' in item:
            # item['index'] is usually 1-indexed, so we subtract 1
            idx = int(item['index']) - 1
            raw_data[idx] = item.get('Instruction', '')
    
    
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
    if args.use_hf:
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
        hf_client = HFClient(hf_model_id)

    def process_one(chunk_file):
        try:
            file_idx_str = os.path.splitext(chunk_file)[0]
            if not file_idx_str.isdigit():
                print(f"Warning: Skipping file with non-numeric name: {chunk_file}")
                return
            file_idx = int(file_idx_str) - 1
        except Exception:
            print(f"Warning: error parsing filename {chunk_file}")
            return

        if check_exist(file_idx, output_base):
            print(f"Skipping {chunk_file} (already processed)")
            return

        chunk_file_path = os.path.join(args.chunk_dir, chunk_file)

        problem_text = ""
        if file_idx in raw_data:
            problem_text = raw_data[file_idx]

        if not problem_text:
            print(f"Error: Problem text missing for index {file_idx} in raw data file.")
            return

        print(f"Processing chunk file {chunk_file} (index {file_idx})...")
        process_chunk_file(
            chunk_file_path=chunk_file_path,
            idx=file_idx,
            model=args.annotate_model,
            problem_text=problem_text,
            guidebook=True,
            output_path=output_base,
            use_hf=args.use_hf,
            hf_client=hf_client
        )

    if args.use_hf:
        max_workers = 1
        print("Using sequential processing (max_workers=1) for local HF model.")
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
