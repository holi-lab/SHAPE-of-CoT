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
    args = parser.parse_args()

    print("Starting batch preprocessing...")
    
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
                process_file(file_path, args.label_dir, args.raw_dir, skip_llm=args.skip_llm, raw_only=args.raw_only, max_workers=args.workers, model=args.model, all_trials=args.all_trials, resume=args.resume)
            except Exception as e:
                print(f"Failed to process {file_path}: {e}")
        else:
            print(f"File not found: {file_path}")
    print("Batch preprocessing completed.")

if __name__ == "__main__":
    main()
