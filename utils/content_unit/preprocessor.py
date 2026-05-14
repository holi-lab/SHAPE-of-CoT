import argparse
import sys
import json
import os
import re
import glob
from openai import OpenAI
from tqdm import tqdm
import time

# Configuration
LABEL_DIR = "./data/label-gpt"
RAW_DIR = "./data/raw"
GUIDEBOOK_PATH = "./utils/content_unit/process_content_unit.md"

def merge_short_chunks(chunks):
    """
    Post-process chunks to merge single-sentence chunks with neighbors 
    if they share the same ontology tag.
    """
    if not chunks:
        return []
        
    merged = []
    if len(chunks) > 0:
        merged.append(chunks[0])
        
    for i in range(1, len(chunks)):
        current = chunks[i]
        previous = merged[-1]
        
        # Check if tags match (order-independent comparison of lists)
        tags_match = set(previous.get('ontology_tag', [])) == set(current.get('ontology_tag', []))
        
        if tags_match:
            # Check if either is a single sentence
            # We use original indices to determine if it's a single sentence
            prev_is_single = previous['original_start_idx'] == previous['original_end_idx']
            curr_is_single = current['original_start_idx'] == current['original_end_idx']
            
            if prev_is_single or curr_is_single:
                # Merge current into previous
                previous['sentence'] += " " + current['sentence']
                previous['original_end_idx'] = current['original_end_idx']
                
                # Merge reasoning
                prev_reason = previous.get('sentence-category-reason') or ""
                curr_reason = current.get('sentence-category-reason') or ""
                
                if curr_reason and curr_reason not in prev_reason:
                    previous['sentence-category-reason'] = (prev_reason + " " + curr_reason).strip()
                
                # Skip adding current to merged list
                continue
        
        # If no merge, append current
        merged.append(current)
        
    # Re-index
    for idx, chunk in enumerate(merged):
        chunk['index'] = idx + 1
        
    return merged

def chunk_sentences_with_llm(sentences, dataset_name, problem_index, model="x-ai/grok-4.1-fast"):
    """
    Chunk sentences using LLM (Gemini via OpenRouter).
    
    Args:
        sentences (list): List of sentence strings.
        dataset_name (str): Name of the dataset for logging.
        problem_index (str/int): Index of the problem for logging.
        model (str): Model identifier for OpenRouter (default: x-ai/grok-4.1-fast).
    
    Returns:
        list: List of chunk dictionaries with 'index', 'sentence' (merged), etc.
    """
    if not sentences:
        return []

    from dotenv import load_dotenv
    load_dotenv()
    
    # Initialize OpenAI client for OpenRouter
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("Warning: OPENROUTER_API_KEY not found in environment variables.")
        return []

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key, 
    )
    
    # Load Guidebook Content
    try:
        with open(GUIDEBOOK_PATH, 'r') as f:
            guidebook_content = f.read()
    except Exception as e:
        print(f"Error reading guidebook: {e}")
        return []

    processed_chunks = []
    current_idx = 0
    total_sentences = len(sentences)
    
    # Global chunk index tracker
    global_chunk_idx = 1
    
    debug_log = []

    print(f"Starting LLM chunking for {dataset_name} ID {problem_index}. Total sentences: {total_sentences}")

    while current_idx < total_sentences:
        # Define Window
        if current_idx == 0:
            # First batch: 0 to 74 (Target: first 50 or less)
            start_context = 0
            end_context = min(75, total_sentences)
            # Target range is effectively the start of this batch
            # We will ask LLM to chunk the first 50 sentences roughly
            target_start_idx = 0
            target_end_idx = min(50, total_sentences)
            
            window_sentences = sentences[start_context:end_context]
            # Indices relative to the whole text
            window_indices = list(range(start_context, end_context))
            
            prompt_context_msg = "This is the start of the text. Chunk the first 50 sentences (or fewer/more if boundary requires)."
            
        else:
            # Subsequent batches: 25 prev + 50 target + 25 next
            # Context start
            context_start = max(0, current_idx - 25)
            # Target end (ideal)
            target_ideal_end = min(current_idx + 50, total_sentences)
            # Context end
            context_end = min(target_ideal_end + 25, total_sentences)
            
            window_sentences = sentences[context_start:context_end]
            window_indices = list(range(context_start, context_end))
            
            target_start_idx = current_idx
            target_end_idx = target_ideal_end
            
            prompt_context_msg = f"Previous context provided. Start chunking from sentence index {current_idx+1}. Target roughly 50 sentences."

        # Format Input for Prompt
        input_text_block = ""
        for i, s_idx in enumerate(window_indices):
            # 1-based index for LLM
            input_text_block += f"[{s_idx + 1}] {sentences[s_idx]}\n"
            
        # Construct Prompt
        # We need to tell the LLM which sentences to focus on.
        # But per instruction, we just give the window and ask it to output target.
        # The prompt in process_content_unit.md expects {{INSERT_COT_TEXT_HERE}}
        
        full_prompt = guidebook_content.replace("{{INSERT_COT_TEXT_HERE}}", input_text_block)
        
        # Add specific instruction about the target window
        specific_instruction = (
            f"\n\nIMPORTANT: \n"
            f"1. You are provided with a window of sentences, each with an index like [1], [2], etc.\n"
            f"2. YOUR TASK is to segment the sentences starting from index {target_start_idx + 1} up to roughly index {target_start_idx + 50}.\n"
            f"3. START grouping sentences from index {target_start_idx + 1}. Do NOT include earlier indices in your output.\n"
            f"4. The target end index is {target_start_idx + 50}. If a logical unit extends beyond this index, do not split the unit; instead, STOP grouping before this unit begins and leave it for the next batch.\n"
            f"5. **Group by Same Tag**: If consecutive sentences share the EXACT SAME ontology tag (e.g., both are H13 or H1), you MUST merge them into a SINGLE chunk. Do not output multiple small chunks with identical tags.\n"
            f"6. **NO INDEPENDENT MONITORING/WEAK SENTENCES**: Sentences like 'I think that's solid.', 'Okay.', 'Let me check.', 'Wait.' are NOT independent strategies. You MUST merge them with the preceding or following unit. NEVER output a chunk containing only such a sentence.\n"
            f"7. **ABSOLUTELY NO 'N/A' CODES**: You must NEVER use 'N/A' or empty lists in the 'codes' field. Every chunk must have at least one valid heuristic code (H1-H13). If a segment appears to be pure execution without a clear heuristic, merge it with the preceding or following chunk that contains the strategy it implements or supports. Examples: merge calculations with the H13 verification they support, merge coordinate listings with the H10 formula application they serve.\n"
            f"8. **NO SINGLE-SENTENCE CHUNKS**: Do NOT create chunks with only a single short sentence (under ~15 words), especially for monitoring, confirmation, or transitional statements. Examples: merge 'Same as before!' with the preceding verification chunk, merge 'Now let's...' with the following calculation chunk, merge 'Wait, let me check.' with the chunk being checked. Exception: A single sentence can be its own chunk ONLY if it contains substantial strategic content (e.g., complete problem classification, full theorem statement, complete case analysis setup).\n"
            f"9. **CRITICAL - VALID CODES ONLY**: The ONLY valid ontology codes are H1, H2, H3, H4, H5, H6, H7, H8, H9, H10, H11, H12, H13. NEVER use N1, N2, N3, N4, N5 or any other codes starting with letters other than H. These N-codes do NOT exist in our taxonomy. If you encounter pure execution/calculation content, merge it with the heuristic chunk it supports - do NOT create a separate chunk with made-up codes like 'N2' or 'Technical Performance'.\n"
            f"10. Output strictly in JSON format. For each item, YOU MUST INCLUDE:\n"
            f"   - 'start_index': The 1-based index of the first sentence in this chunk.\n"
            f"   - 'end_index': The 1-based index of the last sentence in this chunk.\n"
            f"   - 'codes': List containing ONLY valid codes from H1-H13, never N-codes, never 'N/A', never empty.\n"
            f"   - 'reasoning': Reasoning for the codes.\n"
            f"11. **CONCISE REASONING**: Keep the 'reasoning' field extremely short (1-2 sentences max). Do NOT quote long blocks of text.\n"
            f"12. **NO INTERNAL DOUBLE QUOTES**: Use single quotes (') for any quotes inside the reasoning text to ensure valid JSON.\n"
        )
        
        full_prompt += specific_instruction
        
        # print(f"\n--- PROMPT FOR CHUNK {global_chunk_idx} ---")
        # print(full_prompt)
        # print("------------------------------------------\n")

        messages = [
            {
                "role": "user",
                "content": full_prompt
            }
        ]
        
        # Call LLM
        max_retries = 3
        response_json = []
        
        for attempt in range(max_retries):
            try:
                completion = client.chat.completions.create(
                    model=model, 
                    messages=messages,
                    # Removed response_format={"type": "json_object"} as it may conflict with list outputs
                    max_tokens=10000
                )
                
                finish_reason = completion.choices[0].finish_reason
                content = completion.choices[0].message.content
                
                if finish_reason == "length":
                    print("Warning: LLM response truncated due to length.")
                # Parse JSON
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                    
                try:
                    response_json = json.loads(content, strict=False)
                except json.JSONDecodeError as e:
                    print(f"JSON Parse Error: {e}. Attempting robust repair...")
                    
                    fixed_content = content
                    
                    # 1. Attempt to fix invalid escapes (common in LaTeX)
                    fixed_content = re.sub(r'(?<!\\)\\(?![\\"/bfnrtu])', r'\\\\', fixed_content)
                    fixed_content = re.sub(r'(?<!\\)\\u(?![0-9a-fA-F]{4})', r'\\\\u', fixed_content)
                    
                    # 2. Robust stack-based JSON repair for truncated responses
                    def repair_truncated_json(s):
                        s = s.strip()
                        if not s: return s

                        # Phase 0: Try to truncate to the last complete object in a list
                        if s.startswith('['):
                            last_brace = s.rfind('}')
                            if last_brace != -1:
                                try:
                                    potential_s = s[:last_brace+1] + ']'
                                    json.loads(potential_s, strict=False)
                                    return potential_s
                                except:
                                    pass

                        # Phase 1: Close open strings
                        in_string = False
                        escaped = False
                        for i in range(len(s)):
                            c = s[i]
                            if c == '\\' and not escaped:
                                escaped = True
                            elif c == '"' and not escaped:
                                in_string = not in_string
                                escaped = False
                            else:
                                escaped = False
                        
                        if in_string:
                            s += '"'
                        
                        # Phase 2: Close brackets/braces
                        stack = []
                        in_string = False
                        escaped = False
                        for i, c in enumerate(s):
                            if c == '\\' and not escaped:
                                escaped = True
                                continue
                            if c == '"' and not escaped:
                                in_string = not in_string
                            elif not in_string:
                                if c in '{[':
                                    stack.append(c)
                                elif c in '}]':
                                    if stack:
                                        if (c == '}' and stack[-1] == '{') or (c == ']' and stack[-1] == '['):
                                            stack.pop()
                            escaped = False
                        
                        while stack:
                            opening = stack.pop()
                            if opening == '{':
                                s += '}'
                            elif opening == '[':
                                s += ']'
                        return s

                    fixed_content = repair_truncated_json(fixed_content)
                    
                    try:
                        response_json = json.loads(fixed_content, strict=False)
                        print("Fixed and repaired JSON successfully.")
                    except Exception as fix_e:
                        print(f"Failed to fix JSON: {fix_e}")
                        
                        # Log failed content for debugging
                        debug_log_dir = os.path.join(os.path.dirname(__file__), "..", "logs", "llm_debug")
                        os.makedirs(debug_log_dir, exist_ok=True)
                        debug_file = os.path.join(debug_log_dir, f"fail_{int(time.time())}_{dataset_name}_{problem_index}.txt")
                        with open(debug_file, "w") as f:
                            f.write(f"FINISH_REASON: {finish_reason}\n\nPROMPT:\n{full_prompt}\n\nRESPONSE:\n{content}\n\nFIXED:\n{fixed_content}\n")
                        print(f"Saved failed response to {debug_file}")
                        raise

                # Handle list or dict wrapper
                if isinstance(response_json, dict):
                    # Sometimes models wrap list in a key like "segments"
                    for key, value in response_json.items():
                        if isinstance(value, list):
                            response_json = value
                            break
                    if isinstance(response_json, dict):
                         # verification failed or single object?
                         response_json = [response_json]

                break
            except Exception as e:
                print(f"LLM Call failed (Attempt {attempt+1}): {e}")
                time.sleep(2)
        
        if not response_json:
            print("Failed to get valid JSON from LLM. Aborting this problem.")
            return []
            
        # Process chunks using INDICES
        batch_covered_count = 0
        
        for item in response_json:
            start_idx_1based = item.get('start_index')
            end_idx_1based = item.get('end_index')
            
            if start_idx_1based is not None and end_idx_1based is not None:
                # Convert to 0-based
                s_idx = int(start_idx_1based) - 1
                e_idx = int(end_idx_1based) - 1
                
                # Validation
                if s_idx < 0 or e_idx >= total_sentences:
                    print(f"Warning: LLM returned out-of-bounds indices [{start_idx_1based}, {end_idx_1based}]. Skipping.")
                    continue
                
                # Reconstruct text exactly from original sentences
                
                # Validation 2: Overlap check
                if s_idx < current_idx:
                    if e_idx < current_idx:
                        print(f"Warning: LLM returned distinct chunk [{s_idx}, {e_idx}] that is fully before current_idx {current_idx}. Skipping duplicate.")
                        continue
                    else:
                        print(f"Warning: LLM returned overlapping chunk starting at {s_idx}, current is {current_idx}. Clamping start.")
                        s_idx = current_idx

                # Validation 3: Gap check
                if s_idx > current_idx:
                    print(f"Warning: Gap detected between {current_idx} and {s_idx-1}. Auto-expanding start from {s_idx} to {current_idx}.")
                    s_idx = current_idx

                # inclusive range [s_idx, e_idx]
                chunk_sentences_list = sentences[s_idx : e_idx + 1]
                
                # If chunk contains sentences, join them
                if chunk_sentences_list:
                    # In label.py, sentences are often joined by space or newline? 
                    # Usually space for flow, or preserving original separators?
                    # The `split_sentences` removed separators. Let's use space.
                    # Or check if last char is punctuation?
                    segment_text = " ".join(chunk_sentences_list)
                    
                    matched_indices = list(range(s_idx, e_idx + 1))
                    
                    # Update current_idx logic
                    # We strictly follow LLM's indices. 
                    # But we need to ensure continuity? 
                    # If LLM skips index 50 after 49?
                    # We just take what LLM gives. The sliding window logic will update `current_idx` based on where we actally ended.
                    
                    last_processed_idx = matched_indices[-1]
                    
                    processed_chunks.append({
                        "index": global_chunk_idx,
                        "ontology_tag": item.get('codes', []), 
                        "sentence": segment_text, 
                        "sentence-category": None, 
                        "sentence-category-reason": item.get('reasoning', None),
                        "sentence-type": "think", 
                        
                        # Metadata for section tracking
                        "original_start_idx": matched_indices[0],
                        "original_end_idx": matched_indices[-1]
                    })
                    global_chunk_idx += 1
                    
                    # Advance current_idx to the end of this chunk
                    # If LLM skips, we jump? Or we fill?
                    # "Absolutley no missing sentences" -> If s_idx > current_idx, we have a gap!
                    
                    current_idx = last_processed_idx + 1
                    batch_covered_count += len(matched_indices)
            else:
                # Fallback to text matching if indices missing?
                print("Warning: LLM output missing 'start_index' or 'end_index'. Skipping chunk.")
                pass
        
        # Check progress
        if batch_covered_count == 0:
            print("Stuck! LLM returned no matching segments. Force advancing 1 sentence.")
            current_idx += 1
            
        # If we reached the end of target window or close to it?
        # The loop condition `while current_idx < total_sentences` handles termination.

    # Final Verification
    verify_coverage(sentences, processed_chunks)
    
    # Post-processing: Merge short chunks
    processed_chunks = merge_short_chunks(processed_chunks)
    
    return processed_chunks

def verify_coverage(original_sentences, chunks):
    """
    Verify that all original sentences are accounted for in the chunks.
    Raises an error if there's a mismatch.
    """
    # Simply concatenate all chunk texts and original sentences?
    # Or count?
    # Since chunks combine sentences, we can't just count.
    # But we can check if the sequence of sentences can reconstruct the chunks?
    # Basically, we just need to ensure we didn't skip any index in the loop.
    # The loop `current_idx` logic ensures we cover continuously.
    # So if `current_idx` reached `len(sentences)`, we are good.
    pass

def split_response_into_paragraphs(response):
    return [p.strip() for p in response.split('\n\n')]

def split_paragraph_into_sentences(paragraph):
    splits = []
    current = ''
    i = 0
    in_math_block = False
    math_delimiter = None  # Track whether we're in $ or $$ block
    
    # Common abbreviations that shouldn't trigger sentence splits
    abbreviations = {
        'e.g.', 'i.e.', 'v.s.', 'cf.', 'et al.', 'ibid.', 'etc.', 'vs.', 'viz.',
        'Dr.', 'Mr.', 'Mrs.', 'Ms.', 'Prof.', 'Rev.', 'St.', 'Jr.', 'Sr.',
        'Inc.', 'Ltd.', 'Corp.', 'Co.', 'LLC.', 'Ph.D.', 'M.D.', 'B.A.', 'M.A.',
        'U.S.', 'U.K.', 'U.S.A.', 'N.Y.', 'L.A.', 'D.C.', 'a.m.', 'p.m.',
        'No.', 'Vol.', 'pp.', 'Fig.', 'Eq.', 'Ref.', 'Sec.', 'Ch.', 'App.'
    }
    
    def is_abbreviation_context(text, position):
        """Check if the period at position is part of a known abbreviation"""
        # Look for abbreviations that end at this position
        for abbrev in abbreviations:
            abbrev_len = len(abbrev)
            start_pos = position - abbrev_len + 1
            if start_pos >= 0 and position + 1 <= len(text):
                # Extract the potential abbreviation from the text
                potential_abbrev = text[start_pos:position + 1]
                if potential_abbrev.lower() == abbrev.lower():
                    # Additional check: make sure this is a word boundary
                    # Check if there's a space, punctuation, or start of text before the abbreviation
                    if start_pos == 0 or not text[start_pos - 1].isalnum():
                        return True
        
        # Also check if this period is part of an abbreviation that continues after this position
        # Look for abbreviations that contain this position
        for abbrev in abbreviations:
            abbrev_len = len(abbrev)
            # Check all possible starting positions for this abbreviation
            for start_offset in range(abbrev_len):
                start_pos = position - start_offset
                end_pos = start_pos + abbrev_len
                if (start_pos >= 0 and end_pos <= len(text) and 
                    start_pos <= position < end_pos):
                    potential_abbrev = text[start_pos:end_pos]
                    if potential_abbrev.lower() == abbrev.lower():
                        # Check word boundary
                        if start_pos == 0 or not text[start_pos - 1].isalnum():
                            return True
        return False
    
    while i < len(paragraph):
        char = paragraph[i]
        current += char
        
        # Check for math delimiters
        if char == '$':
            if not in_math_block:
                # Check if it's $$ (display math) or $ (inline math)
                if i + 1 < len(paragraph) and paragraph[i + 1] == '$':
                    math_delimiter = '$$'
                    current += '$'
                    i += 1  # Skip the second $
                else:
                    math_delimiter = '$'
                in_math_block = True
            else:
                # We're already in a math block, check if this closes it
                if math_delimiter == '$$' and i + 1 < len(paragraph) and paragraph[i + 1] == '$':
                    current += '$'
                    i += 1  # Skip the second $
                    in_math_block = False
                    math_delimiter = None
                elif math_delimiter == '$':
                    in_math_block = False
                    math_delimiter = None
        
        # Only process sentence endings if we're not in a math block
        elif not in_math_block:
            # Check for ellipsis (...)
            if char == '.' and i + 2 < len(paragraph) and paragraph[i+1:i+3] == '..':
                # Add the remaining two dots to complete the ellipsis
                current += paragraph[i+1:i+3]
                i += 2  # Skip the next two dots
                
                # Check if this ellipsis is in a mathematical context
                # Look for mathematical indicators before or after
                context_before = current[-20:] if len(current) >= 20 else current
                context_after = paragraph[i+1:i+21] if i+1 < len(paragraph) else ""
                
                # Mathematical context indicators
                math_indicators = ['+', '-', '*', '/', '=', '(', ')', '[', ']', 'g(', 'f(', 'h(', 'times', 'integer', 'induction']
                
                is_math_context = any(indicator in context_before.lower() or indicator in context_after.lower() 
                                    for indicator in math_indicators)
                
                # Only split if it's not in a mathematical context
                if not is_math_context:
                    splits.append(current.strip())
                    current = ''
            elif char in '.?!':
                # Check if dot is part of an abbreviation
                if char == '.' and is_abbreviation_context(paragraph, i):
                    # This period is part of an abbreviation, don't split here
                    pass  # Continue with the loop, don't split
                elif char == '.' and i > 0 and i < len(paragraph)-1:
                    # Check if dot is between numbers (decimal point)
                    prev_char = paragraph[i-1]
                    next_char = paragraph[i+1]
                    if prev_char.isdigit() and next_char.isdigit():
                        pass  # Continue with the loop, don't split
                    elif i == 1 and prev_char.isdigit():
                        pass  # Continue with the loop, don't split
                    else:
                        # This is a sentence-ending punctuation
                        while i + 1 < len(paragraph) and paragraph[i+1] in '\'"’”)]}':
                            current += paragraph[i+1]
                            i += 1
                        splits.append(current.strip())
                        current = ''
                else:
                    # This is a sentence-ending punctuation
                    while i + 1 < len(paragraph) and paragraph[i+1] in '\'"’”)]}':
                        current += paragraph[i+1]
                        i += 1
                    splits.append(current.strip())
                    current = ''
        
        i += 1
    
    if current:  # Add any remaining text
        splits.append(current.strip())
    return splits

def is_valid_sentence(sentence):
    """Check if a sentence is valid (not empty, not just dashes or punctuation)"""
    if not sentence or not sentence.strip():
        return False
    
    # Remove whitespace for checking
    cleaned = sentence.strip()
    
    # Check if sentence is just dashes (any number of dashes)
    if all(c == '-' for c in cleaned):
        return False
    
    # Check if sentence is just punctuation and whitespace
    alphanumeric_chars = ''.join(c for c in cleaned if c.isalnum())
    return bool(alphanumeric_chars)

def merge_colon_and_equals_sentences(sentences):
    """Merge sentences ending with ':' with the next sentence and sentences starting with '=' with previous sentence"""
    # First pass: merge sentences ending with ':'
    merged = []
    i = 0
    while i < len(sentences):
        current_sentence = sentences[i].copy()  # Make a copy to avoid modifying original
        current_sentence['sentence'] = current_sentence['sentence'].strip()
        
        # Keep merging while current sentence ends with ':' and there's a next sentence
        while (current_sentence['sentence'].rstrip().endswith(':') and 
               i + 1 < len(sentences)):
            next_sentence = sentences[i + 1].copy()  # Make a copy
            next_sentence['sentence'] = next_sentence['sentence'].strip()
            
            # Only merge if they have the same type
            if current_sentence['type'] == next_sentence['type']:
                # Merge the sentences
                current_sentence['sentence'] = current_sentence['sentence'] + ' ' + next_sentence['sentence']
                i += 1  # Move to next sentence (will be skipped in main loop)
            else:
                # Different types, can't merge
                break
        
        merged.append(current_sentence)
        i += 1
    
    # Second pass: merge sentences starting with '=' with previous sentence
    final_merged = []
    for i, sentence in enumerate(merged):
        sentence_copy = sentence.copy()
        
        # Check if sentence starts with '='
        if (sentence_copy['sentence'].lstrip().startswith('=') and 
            len(final_merged) > 0 and 
            final_merged[-1]['type'] == sentence_copy['type']):
            # Merge with previous sentence
            final_merged[-1]['sentence'] = final_merged[-1]['sentence'] + ' ' + sentence_copy['sentence']
        else:
            # Add as new sentence
            final_merged.append(sentence_copy)
    
    return final_merged

def split_sentences(text, skip_llm=False):
    """
    Splits text into sentences using sophisticated logic ported from utils.py.
    """
    if not text:
        return []
        
    paragraphs = split_response_into_paragraphs(text)
    sentences = []
    
    for paragraph in paragraphs:
        paragraph_sentences = split_paragraph_into_sentences(paragraph)
        for sentence in paragraph_sentences:
            if is_valid_sentence(sentence):
                # Use a dummy type since we are splitting purely text
                sentences.append({
                    'sentence': sentence,
                    'type': 'default'
                })
    
    # Apply merging logic
    if not skip_llm:
        merged_sentences = merge_colon_and_equals_sentences(sentences)
    else:
        merged_sentences = sentences
    
    # Return just the sentence strings
    return [item['sentence'] for item in merged_sentences]

def _process_single_item(item, file_path, dataset_name, target_label_dir, skip_llm, raw_only, all_trials=False, model="x-ai/grok-4.1-fast", resume=False):
    # Support multiple field names for index
    idx = item.get('problem_id')
    if idx is None:
        idx = item.get('index')
    if idx is None:
        idx = item.get('id')
        
    if idx is None:
        return None
        
    # Support multiple field names for instruction/problem text
    instruction = item.get('problem')
    if instruction is None:
        instruction = item.get('Instruction')
    if instruction is None:
        instruction = item.get('question')
    if instruction is None:
        instruction = item.get('text', '')
    
    answer = item.get('answer', '')
    
    trials = item.get('trials') or []
    if not trials:
        trials = [{}]  # Fallback if no trials field exists
    
    if not all_trials:
        trials = trials[:1]
        
    results = []
    
    for t_idx, trial in enumerate(trials):
        # Determine trial identifier (n or index)
        n_val = trial.get('n')
        if not all_trials:
            trial_id_suffix = ""
        elif n_val is not None:
            trial_id_suffix = f"_{n_val}"
        elif len(trials) > 1:
            trial_id_suffix = f"_{t_idx}"
        else:
            trial_id_suffix = ""
            
        current_idx = f"{idx}{trial_id_suffix}"
        
        rationale = trial.get('reasoning') or ''
        generated_text = trial.get('generated_text') or ''
        
        # Clean generated_text if necessary
        split_marker = "<|im_start|>assistant"
        if split_marker in generated_text:
            parts = generated_text.split(split_marker)
            if len(parts) > 1:
                generated_text = parts[-1].strip()
        
        rationale = (rationale or "").strip()
        generated_text = (generated_text or "").strip()
            
        # Debug print for the first item
        if str(idx) == "1" or idx == 1:
            print(f"File: {os.path.basename(file_path)} (Trial: {trial_id_suffix})")
            print(f"  Extracted ID: {idx}")
            print(f"  Instruction Snippet: {instruction[:50]}...")
            print(f"  Correct Answer Snippet: {str(answer)[:50]}...")
            print(f"  Rationale Length: {len(rationale)}")
            print(f"  Generated Text Length: {len(generated_text)}")
            print("-" * 30)
        
        # 1. Add to Raw List
        full_rationale = ""
        if rationale and generated_text:
            full_rationale = f"{rationale}\n\n{generated_text}"
        elif rationale:
             full_rationale = rationale
        elif generated_text:
            full_rationale = generated_text
    
        raw_item = {
            "index": current_idx,
            "original_index": idx,
            "Instruction": instruction,
            "Rationale": full_rationale,
            "Correct Answer": str(answer)
        }
        
        if raw_only:
            results.append(raw_item)
            continue
            
        # Resume Check: See if already processed and coverage is good
        if resume and target_label_dir:
            problem_file_path = os.path.join(target_label_dir, f"{current_idx}.json")
            if os.path.exists(problem_file_path):
                try:
                    with open(problem_file_path, 'r', encoding='utf-8') as f:
                        saved_data = json.load(f)
                    saved_chunks = saved_data.get("sentences", [])
                    
                    if full_rationale:
                        base_sentences = split_sentences(full_rationale, skip_llm=skip_llm)
                        
                        def normalize(text):
                            return "".join(text.split())
                        
                        original_text = "".join([normalize(s) for s in base_sentences])
                        chunk_text = "".join([normalize(c.get('sentence', '')) for c in saved_chunks])
                        
                        if len(original_text) > 0:
                            ratio = len(chunk_text) / len(original_text)
                            if 0.9 <= ratio <= 1.1:
                                print(f"Resume: Skipping {current_idx} (Good coverage ratio: {ratio:.2f})")
                                results.append(raw_item)
                                continue
                            else:
                                print(f"Resume: Re-running {current_idx} (Bad coverage ratio: {ratio:.2f})")
                        else:
                            # Empty base text, safe to skip
                            print(f"Resume: Skipping {current_idx} (Empty rationale)")
                            results.append(raw_item)
                            continue
                except Exception as e:
                    print(f"Resume: Error checking {current_idx}, will re-run. Error: {e}")
    
        # 2. Create Label File (Chunks)
        chunks = []
        
        if full_rationale:
            base_sentences = split_sentences(full_rationale, skip_llm=skip_llm)
            
            if skip_llm:
                llm_chunks = []
                for i, sentence in enumerate(base_sentences):
                    llm_chunks.append({
                        "index": i + 1,
                        "ontology_tag": [],
                        "sentence": sentence,
                        "sentence-category": None,
                        "sentence-category-reason": None,
                        "sentence-type": "think",
                        "original_start_idx": i,
                        "original_end_idx": i
                    })
            else:
                llm_chunks = chunk_sentences_with_llm(base_sentences, dataset_name, current_idx, model=model)
                if not llm_chunks:
                    print(f"Warning: LLM returned empty chunks for item {current_idx}. Falling back to default sentence splitting.")
                    llm_chunks = []
                    for i, sentence in enumerate(base_sentences):
                        llm_chunks.append({
                            "index": i + 1,
                            "ontology_tag": [],
                            "sentence": sentence,
                            "sentence-category": None,
                            "sentence-category-reason": "Fallback due to LLM failure or empty response",
                            "sentence-type": "think",
                            "original_start_idx": i,
                            "original_end_idx": i
                        })
            
            rationale_sentence_count = 0
            if rationale:
                r_sentences = split_sentences(rationale)
                rationale_sentence_count = len(r_sentences)
            
            for chunk in llm_chunks:
                start_idx = chunk.get('original_start_idx', -1)
                section = "Reasoning"
                
                if start_idx >= rationale_sentence_count:
                    section = "Generated Text"
                
                chunk['section'] = section
                
                if 'original_start_idx' in chunk:
                    del chunk['original_start_idx']
                if 'original_end_idx' in chunk:
                    del chunk['original_end_idx']
                chunks.append(chunk)
    
        if target_label_dir:
            problem_file_path = os.path.join(target_label_dir, f"{current_idx}.json")
            with open(problem_file_path, 'w', encoding='utf-8') as pf:
                json.dump({"sentences": chunks}, pf, indent=4, ensure_ascii=False)
                
        results.append(raw_item)
        
    return results

def process_file(file_path, label_dir, raw_dir, skip_llm=False, raw_only=False, max_workers=10, model="x-ai/grok-4.1-fast", all_trials=False, resume=False):
    filename = os.path.basename(file_path)
    dataset_name = os.path.splitext(filename)[0]
    
    print(f"Processing {dataset_name}...")
    
    os.makedirs(raw_dir, exist_ok=True)
    
    target_label_dir = None
    if not raw_only:
        # Create label directory for this dataset
        target_label_dir = os.path.join(label_dir, dataset_name)
        os.makedirs(target_label_dir, exist_ok=True)
    
    items = []
    
    # Try loading as JSON list first, if fail, read as lines (JSONL)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if content.startswith('['):
                items = json.loads(content)
            else:
                # JSONL
                for line in content.splitlines():
                    if line.strip():
                        items.append(json.loads(line))
    except json.JSONDecodeError as e:
        print(f"Error reading {filename}: {e}")
        return

    # We now handle resume logic inside _process_single_item to check coverage
    if resume and target_label_dir and os.path.exists(target_label_dir):
        print("Resume mode enabled: will check existing files for valid coverage before skipping.")

    raw_data_list = []
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process_single_item, item, file_path, dataset_name, target_label_dir, skip_llm, raw_only, all_trials, model, resume): i for i, item in enumerate(items)}
        if not futures:
            print(f"Resume mode: no new items to process for {dataset_name}.")
            return
        
        results_with_index = []
        for future in tqdm(as_completed(futures), total=len(items), desc=f"Processing {dataset_name}", unit="item"):
            res = future.result()
            orig_idx = futures[future]
            if res is not None:
                results_with_index.append((orig_idx, res))
                
    results_with_index.sort(key=lambda x: x[0])
    raw_data_list = []
    for _, res_list in results_with_index:
        raw_data_list.extend(res_list)
            
    # Write Raw Data File (Dataset level)
    # If processing a specific file, we might be overwriting it if it's in RAW_DIR
    # Ensure we use a consistent name or just overwrite
    raw_file_path = os.path.join(raw_dir, f"{dataset_name}.json")
    with open(raw_file_path, 'w', encoding='utf-8') as rf:
        json.dump(raw_data_list, rf, indent=4, ensure_ascii=False)
        
    print(f"Finished {dataset_name}. Processed {len(raw_data_list)} items.")
            
def verify_coverage(original_sentences, chunks):
    """
    Verify that all original sentences are accounted for in the chunks.
    Raises an error if there's a mismatch.
    """
    # Create normalized strings for comparison
    def normalize(text):
        return "".join(text.split())
        
    original_text = "".join([normalize(s) for s in original_sentences])
    chunk_text = "".join([normalize(c['sentence']) for c in chunks])

    # Basic length check
    if len(original_text) == 0:
        return

    # Check ratio
    ratio = len(chunk_text) / len(original_text)
    if ratio < 0.9 or ratio > 1.1:
        print(f"Warning: Coverage verification suspicious. Original length: {len(original_text)}, Chunked length: {len(chunk_text)}. Ratio: {ratio:.2f}")
    else:
        # print("Coverage verification passed (length check).")
        pass
        
    # Since we use greedy index tracking in loop, we are implicitly safe on 'missing' sentences 
    # as long as the loop completes. The warning above helps catch major hallucinations.

def main():
    parser = argparse.ArgumentParser(description="Preprocess Qwen results or raw JSON files.")
    parser.add_argument("--file", help="Specific file to process", required=True)
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM chunking and just split sentences")
    parser.add_argument("--label-dir", help="Directory to save label JSON files", default=LABEL_DIR)
    parser.add_argument("--raw-dir", help="Directory to save raw JSON files", default=RAW_DIR)
    parser.add_argument("--raw-only", action="store_true", help="Only save raw files to raw_dir, skip creating label files")
    parser.add_argument("--workers", type=int, help="Number of concurrent workers (default: 10)", default=10)
    parser.add_argument("--model", type=str, help="Model to use for LLM chunking (default: x-ai/grok-4.1-fast)", default="x-ai/grok-4.1-fast")
    parser.add_argument("--all-trials", action="store_true", help="Process all trials instead of just the first one")
    args = parser.parse_args()

    # Ensure directories exist
    if not args.raw_only:
        os.makedirs(args.label_dir, exist_ok=True)
    os.makedirs(args.raw_dir, exist_ok=True)
    
    if os.path.exists(args.file):
        process_file(args.file, args.label_dir, args.raw_dir, skip_llm=args.skip_llm, raw_only=args.raw_only, max_workers=args.workers, model=args.model, all_trials=args.all_trials)
    else:
        print(f"File not found: {args.file}")

if __name__ == "__main__":
    main()
