import os
import re
import json
from tqdm import tqdm
from transformers import AutoTokenizer

# Get the directory of this file
_current_dir = os.path.dirname(os.path.abspath(__file__))
_guidebook_path = os.path.join(os.path.dirname(_current_dir), "guidebook", "heuristics_guide.md")

with open(_guidebook_path, "r") as f:
        guidebook_heuristics_prompt = f.read()


def process_new_data(client, new_data, sample_index, guidebook=True, model="gpt-4.1", output_path='result', answer_key='Response', existing_sentence_list=None):
    new_instruction = new_data['Instruction']
    new_instruction_prompt = f"{new_instruction}"
    new_response = new_data[answer_key]

    if existing_sentence_list:
        new_sentence_list = existing_sentence_list
    else:
        new_sentence_list = process_response_to_sentences(new_response, apply_merging=True)
    
    general_sentence_instruction_prompt = """In this project, we aim to analyze the reasoning process of current large language models (LLMs) with advanced reasoning capabilities, i.e., Large Reasoning Models, LRMs, based on a modified version of Alan Schoenfeld's (1985) "Episode-Timeline" framework for problem-solving. Given the model response you need to annotate the sentence-level behavior of the model response with the eight categories: Read, Analyze, Explore, Plan, Implement, Verify, Monitor, and Answer."""

    if guidebook:
        general_sentence_instruction_prompt += "\n\nThe [Guidebook] - [End of the Guidebook] section provides the detailed introduction and definition of each category."
    

    general_sentence_instruction_prompt += """\nThe [Math Problem] - [End of the Math Problem] section provides a math problem.\nThe [Overall Response] - [End of the Overall Response] section provides the overall response of the model to the math problem.\nThe [Previous Context] - [End of the Previous Context] section provides all the previous context of the response that has been annotated and their corresponding labels.\nThe [Input] - [End of the Input] section provides the sentences that need to be annotated.\nThe [Format] - [End of the Format] section provides the format of the output."""


    format_sentence_prompt = (
        "You should format the output in json format regarding the index, a short reasonale and the fine-grained class of the indexed sentence. "
        "The format is as follows:\n"
        "{\n"
        "  'sentences': [\n"
        "    {'index': 'The index of the sentence', 'reason': 'Explain explicitly which specific part of the text matches the definition of the selected heuristic in the guidebook. If multiple heuristics are involved, explain each one.', 'category': 'The fine-grained class of the sentence'},\n"
        "    {'index': 'The index of the sentence', 'reason': 'Explain explicitly which specific part of the text matches the definition of the selected heuristic in the guidebook. If multiple heuristics are involved, explain each one.', 'category': 'The fine-grained class of the sentence'},\n"
        "    ...\n"
        "  ]\n"
        "}"
        "You should strictly follow the index number of the sentence in the [Input] - [End of the Input] section."
    )

    batch_size = 20
    sen_list = []
    for idx, new_response_sentence in enumerate(tqdm(new_sentence_list)):
        if idx % batch_size == batch_size - 1 or idx == len(new_sentence_list) - 1:
            if idx == batch_size - 1 or len(new_sentence_list) < batch_size:
                new_input_context = ""
                new_input_context_prompt = "There is no previous sentences."
            else:
                new_input_context_list = new_sentence_list[: idx + 1 - batch_size]
                new_input_context_str = "\n".join([f"{item['sentence']}" for item in new_input_context_list])
                new_input_context_prompt = f"The previous sentences are:\n\n {new_input_context_str}"


            if len(new_sentence_list) < batch_size:
                new_input_list = new_sentence_list
            elif idx == len(new_sentence_list) - 1:
                remain = idx % batch_size + 1
                new_input_list = new_sentence_list[-remain:]
                # print(len(new_input_list))
            else:
                new_input_list = new_sentence_list[idx-batch_size + 1: idx + 1]
            indexed_input_list = [f"[{idx+1}] {split['sentence']}" for idx, split in enumerate(new_input_list)]
            new_input_str = "\n".join(indexed_input_list)
            new_input_prompt = f"The following sentences which you need to classify:\n{new_input_str}"

            combined_prompt = f"{general_sentence_instruction_prompt}"

            if guidebook:
                combined_prompt += f"\n\n[Guidebook]\n{guidebook_heuristics_prompt}\n[End of the Guidebook]"

            combined_prompt += f"\n\n[Math Problem]\n{new_instruction_prompt}\n[End of the Math Problem]\n\n[Previous Context]\n{new_input_context_prompt}\n[End of the Previous Context]\n\n[Input]\n{new_input_prompt}\n[End of the Input]\n\n[Format]\n{format_sentence_prompt}\n[End of the Format]\n\nNow, annotate the sentences in the [Input] - [End of the Input] section. Refer to the guidebook to make the decision. Strictly follow the index number of the sentence in the [Input] - [End of the Input] section for labeling. You should output the label for {len(new_input_list)} sentences."

            print('='*20 + ' COMBINED PROMPT ' + '='*20)
            print(combined_prompt[:300] + "...")
            print('='*57)
            messages = [
                {"role": "user", "content": combined_prompt},
            ]
            max_retries = 5
            retry_count = 0
            while retry_count < max_retries:
                try:
                    # Use OpenRouter for all models
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        response_format={"type": "json_object"}
                    )
                    result = response.choices[0].message.content
                    response_json = json.loads(result)['sentences']
                    assert len(response_json) == len(new_input_list), f"Number of sentences in the response ({len(response_json)}) does not match the number of sentences in the input ({len(new_input_list)})"
                    break
                except Exception as e:
                    retry_count += 1
                    print(f"Attempt {retry_count} failed, retrying... Error: {str(e)}")
                    if retry_count == max_retries:
                        print(f"Failed to get response after {max_retries} retries. Using empty response.")
                        response = {'sentences': [{'index': '', 'sentence-category-reason': '', 'sentence-category': ''}]}

            # print(result)

            for item in response_json:
                group_index = int(item['index'].strip('[]'))
                item['sentence'] = new_input_list[int(group_index) - 1]['sentence']
                item['sentence-type'] = new_input_list[int(group_index) - 1]['type']

            response_json = [{
                'index': int(item['index'].strip('[]')) + (idx // batch_size) * batch_size,
                'sentence': item['sentence'],
                'sentence-type': item['sentence-type'],
                'sentence-category-reason': item['reason'],
                'sentence-category': item['category']
            } for item in response_json]

            sen_list.extend(response_json)

    target_path = f"{output_path}"
    # for idx, item in enumerate(sen_list):
    #     item['index'] = idx + 1

    if not os.path.exists(target_path):
        os.makedirs(target_path)
    with open(f"{target_path}/{sample_index + 1}.json", "w") as f:
        json.dump(sen_list, f, indent=2)

    return sen_list


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
                        splits.append(current.strip())
                        current = ''
                else:
                    # This is a sentence-ending punctuation
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

def process_section(section_text, section_type):
    """Process a section (thinking or answer) and return sentences with metadata"""
    paragraphs = split_response_into_paragraphs(section_text)
    sentences = []
    
    for paragraph in paragraphs:
        paragraph_sentences = split_paragraph_into_sentences(paragraph)
        for sentence in paragraph_sentences:
            if is_valid_sentence(sentence):
                sentences.append({
                    'sentence': sentence,
                    'type': section_type
                })
    return sentences

def process_response_to_sentences(response, apply_merging=True):
    """
    Complete pipeline to process a response into structured sentences.
    
    Args:
        response (str): The raw response text
        apply_merging (bool): Whether to apply colon and equals merging
    
    Returns:
        list: List of dictionaries with 'id', 'sentence', and 'type' keys
    """
    # Split response by </think> tag
    if '</think>' in response:
        parts = response.split('</think>', 1)
        thinking_part = parts[0].strip()
        answer_part = parts[1].strip()
        
        # Process thinking section
        thinking_sentences = process_section(thinking_part, 'think')
        
        # Process answer section  
        answer_sentences = process_section(answer_part, 'answer')
        
        # Combine all sentences
        all_sentences = thinking_sentences + answer_sentences
    else:
        # No </think> tag, treat entire response as answer section
        all_sentences = process_section(response, 'answer')
    
    # Apply post-processing if requested
    if apply_merging:
        processed_sentences = merge_colon_and_equals_sentences(all_sentences)
    else:
        processed_sentences = all_sentences
    
    # Create final result with sequential IDs
    result = []
    for i, sentence_data in enumerate(processed_sentences):
        result.append({
            'id': str(i),
            'sentence': sentence_data['sentence'],
            'type': sentence_data['type']
        })
    
    return result

# Post-process: merge sentences ending with ':' with next sentence and sentences starting with '=' with previous sentence
def merge_colon_and_equals_sentences(sentences):
    """Merge sentences ending with ':' with the next sentence and sentences starting with '=' with previous sentence"""
    # First pass: merge sentences ending with ':'
    merged = []
    i = 0
    while i < len(sentences):
        current_sentence = sentences[i].copy()  # Make a copy to avoid modifying original
        current_sentence['sentence'] = current_sentence['sentence'].replace('<think>', '').strip()
        
        # Keep merging while current sentence ends with ':' and there's a next sentence
        while (current_sentence['sentence'].rstrip().endswith(':') and 
               i + 1 < len(sentences)):
            next_sentence = sentences[i + 1].copy()  # Make a copy
            next_sentence['sentence'] = next_sentence['sentence'].replace('<think>', '').strip()
            
            # Only merge if they have the same type (think/answer)
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
        sentence_copy['sentence'] = sentence_copy['sentence'].replace('<think>', '').strip()
        
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


def process_new_data_chunks(client, chunk_list, problem_text, sample_index, guidebook=True, model="gpt-4.1", output_path='result'):
    """
    Process pre-chunked data to annotate each chunk with heuristic codes.
    
    Args:
        client: OpenAI/OpenRouter client
        chunk_list: List of chunk dictionaries (from chunked JSON file)
        problem_text: The original problem/instruction
        sample_index: File index for saving
        guidebook: Whether to include heuristics guidebook
        model: Model name for annotation
        output_path: Where to save results
    
    Returns:
        list: Annotated chunk list
    """
    general_instruction_prompt = """In this project, we analyze the reasoning process of Large Reasoning Models (LRMs) by identifying specific heuristic strategies used during problem solving. Given a chunk of text from the model's response, you need to annotate it with heuristic codes from the guidebook.

The heuristic codes include:
- H1-H13: Various heuristic strategies (e.g., H1: Changing representation, H4: Problem classification, H10: Using theorems)
- N1-N4: Non-heuristic categories (e.g., N1: Literal repetition, N2: Technical performance, N4: Answer)

IMPORTANT ANNOTATION RULES:
1. Use Sub-codes When Available: For categories with sub-codes (e.g., H4a/b, H11a/b, H13a-f), you MUST use the specific sub-code if it applies. ONLY use the parent code (e.g., H13) as a last resort fallback if NO sub-code fits.
2. Multi-Tagging Allowed: A chunk can have multiple heuristic codes if multiple strategies are present.
3. Non-Heuristics Only When No Heuristics Present: Only tag N1-N4 when there are absolutely no heuristic strategies (H1-H13) in the chunk.
4. Mandatory Evidence Citation: You MUST explicitly quote the specific sentence or phrase from the chunk that serves as evidence for your chosen codes in the reasoning field."""

    if guidebook:
        general_instruction_prompt += "\n\nThe [Guidebook] section provides detailed definitions of each heuristic code."
    
    general_instruction_prompt += """\nThe [Math Problem] section provides the math problem.
The [Previous Context] section shows:
  - Recent 4 chunks with their annotations
  - Register Change History: All previous chunks tagged with H1 (if any), showing the complete history of register transformations
The [Current Chunk] section provides the chunk that needs to be annotated.
The [Format] section specifies the expected output format."""

    format_prompt = (
        "You should format the output in JSON format with the following structure:\n"
        "{\n"
        "  'annotations': [\n"
        "    {\n"
        "      'code': 'H4',\n"
        "      'evidence': 'Direct quote from the chunk serving as evidence',\n"
        "      'reasoning': 'Brief explanation of why this code applies'\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "The 'annotations' field should be a list of these objects. Each object must contain exactly one 'code'."
    )

    # Context window size
    CONTEXT_WINDOW = 4
    
    annotated_chunks = []
    
    for idx, chunk in enumerate(tqdm(chunk_list, desc="Annotating chunks")):
        
        # Build context from previous chunks (up to 4)
        context_start = max(0, idx - CONTEXT_WINDOW)
        previous_chunks = annotated_chunks[context_start:idx]
        
        # Track H1 (register change) history from ALL previous chunks
        h1_history_chunks = [
            (i, chunk) for i, chunk in enumerate(annotated_chunks[:idx])
            if 'H1' in chunk.get('ontology_tag', [])
        ]
        
        # Build context prompt
        if len(previous_chunks) == 0:
            context_prompt = "There are no previous chunks."
        else:
            context_lines = []
            for i, prev_chunk in enumerate(previous_chunks):
                chunk_num = context_start + i + 1
                codes = prev_chunk.get('ontology_tag', [])
                text = prev_chunk.get('sentence', '')[:200]  # Truncate for brevity
                context_lines.append(f"Chunk {chunk_num} [{', '.join(codes)}]: {text}...")
            context_prompt = "Recent chunks:\n" + "\n".join(context_lines)
        
        # Add H1 register change history if any exist
        if len(h1_history_chunks) > 0:
            h1_lines = []
            for chunk_idx, h1_chunk in h1_history_chunks:
                chunk_num = chunk_idx + 1
                codes = h1_chunk.get('ontology_tag', [])
                # Extract a brief description of the register change
                text = h1_chunk.get('sentence', '')[:150]
                h1_lines.append(f"  - Chunk {chunk_num} [{', '.join(codes)}]: {text}...")
            
            h1_history_section = "\n\nRegister Change History (H1-tagged chunks):\n" + "\n".join(h1_lines)
            context_prompt += h1_history_section
        
        # Current chunk text
        current_chunk_text = chunk.get('sentence', '')
        current_chunk_prompt = f"Chunk to annotate:\n{current_chunk_text}"
        
        # Build combined prompt
        combined_prompt = general_instruction_prompt
        
        if guidebook:
            combined_prompt += f"\n\n[Guidebook]\n{guidebook_heuristics_prompt}\n[End of the Guidebook]"
        
        combined_prompt += f"\n\n[Math Problem]\n{problem_text}\n[End of the Math Problem]"
        combined_prompt += f"\n\n[Previous Context]\n{context_prompt}\n[End of the Previous Context]"
        combined_prompt += f"\n\n[Current Chunk]\n{current_chunk_prompt}\n[End of the Current Chunk]"
        combined_prompt += f"\n\n[Format]\n{format_prompt}\n[End of the Format]"
        
        # Strong final reminder with emphasis
        combined_prompt += """

⚠️ CRITICAL REMINDER - HEURISTIC-FIRST APPROACH ⚠️

Before annotating, follow this decision tree:
1. ✓ First: Carefully scan for ANY heuristic activity (H1-H13)
2. ✓ If you find EVEN ONE heuristic → Tag with H codes ONLY
3. ✓ ONLY if absolutely NO heuristics → Then use N codes

Common mistakes to avoid:
- ❌ DON'T tag modular arithmetic operations (e.g., "617 mod 18 = 5") as N2 → Use H1 or H10
- ❌ DON'T tag finding inverses (e.g., "find inverse of 5 mod 18") as N2 → Use H10
- ❌ DON'T tag strategic substitutions (e.g., "Let u = x²") as N2 → Use H3
- ❌ DON'T tag problem-to-equation conversions as N2 → Use H1

⚠️ SPECIAL ATTENTION: H1 (Changing Register) ⚠️
Before tagging H1, ALWAYS:
Step 1: Identify the problem's INITIAL register (word problem? algebraic? geometric?)
Step 2: Check the "Register Change History" section in [Previous Context] to see ALL previous H1 tags
Step 3: Determine the CURRENT register (after all previous H1 transformations)
Step 4: Check if THIS chunk crosses to a DIFFERENT register type
- ✓ H1: "Assign coordinates to a geometric square" (Geometry → Algebra)
- ✗ NOT H1: "Let n=2k for gcd(n,28)=2" (Already algebraic → Still algebraic = H3, not H1)
- ✗ NOT H1: "Write as y=mx+b" when already in algebraic register (reformatting, not H1)

N2 is ONLY for pure arithmetic after strategy is set (e.g., "2+3=5", "2x+3x=5x").

Now, annotate the current chunk following the heuristic-first approach."""
        
        # Reduced verbosity - only show chunk number
        if idx % 5 == 0:  # Print every 5th chunk
            print(f"Processing chunk {idx+1}/{len(chunk_list)}...")
        
        messages = [
            {"role": "user", "content": combined_prompt},
        ]
        
        max_retries = 5
        retry_count = 0
        result = None
        while retry_count < max_retries:
            try:
                # Use OpenRouter for all models
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"}
                )
                result = response.choices[0].message.content
                
                # Robust JSON parsing with multiple fallback strategies
                response_json = None
                parse_error = None
                
                # Strategy 1: Try with Pydantic if available (most robust)
                use_pydantic = False
                try:
                    from .schemas import ChunkAnnotation
                    use_pydantic = True
                except ImportError:
                    # Pydantic not available, skip to other strategies
                    pass
                
                if use_pydantic:
                    try:
                        # First parse as JSON, then validate with Pydantic
                        try:
                            raw_json = json.loads(result)
                        except json.JSONDecodeError:
                            # Try fixing LaTeX escapes before Pydantic validation
                            fixed_result = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', result)
                            raw_json = json.loads(fixed_result)
                        
                        # Validate with Pydantic
                        validated = ChunkAnnotation(**raw_json)
                        response_json = validated.model_dump()
                        parse_error = None
                    except Exception as e:
                        parse_error = e
                        response_json = None
                
                # Strategy 2: Standard JSON parsing (if Pydantic failed or unavailable)
                if response_json is None:
                    try:
                        response_json = json.loads(result)
                        parse_error = None
                    except json.JSONDecodeError as e:
                        parse_error = e
                        
                        # Strategy 3: Try to fix common LaTeX escape issues
                        # Replace problematic single backslashes with double backslashes
                        # This regex looks for backslashes not already escaped
                        fixed_result = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', result)
                        try:
                            response_json = json.loads(fixed_result)
                            parse_error = None
                        except json.JSONDecodeError:
                            # Strategy 4: Raw string parsing fallback
                            try:
                                # Look for code, evidence, and reasoning matches in an array
                                code_matches = re.finditer(r'"code"\s*:\s*"(.*?)"', result)
                                evidence_matches = re.finditer(r'"evidence"\s*:\s*"(.*?)"', result, re.DOTALL)
                                reasoning_matches = re.finditer(r'"reasoning"\s*:\s*"(.*?)"', result, re.DOTALL)
                                
                                codes = [m.group(1) for m in code_matches]
                                evidences = [m.group(1) for m in evidence_matches]
                                reasonings = [m.group(1) for m in reasoning_matches]
                                
                                if codes:
                                    annotations = []
                                    for i, code in enumerate(codes):
                                        evidence = evidences[i] if i < len(evidences) else "Extracted from malformed JSON"
                                        reasoning = reasonings[i] if i < len(reasonings) else "Extracted from malformed JSON"
                                        annotations.append({'code': code, 'evidence': evidence, 'reasoning': reasoning})
                                        
                                    has_answer_match = re.search(r'"has_answer"\s*:\s*(true|false)', result, re.IGNORECASE)
                                    extracted_answer_match = re.search(r'"extracted_answer"\s*:\s*"(.*?)"', result, re.DOTALL)
                                    
                                    has_answer = False
                                    if has_answer_match and has_answer_match.group(1).lower() == 'true':
                                        has_answer = True
                                        
                                    extracted_answer = None
                                    if extracted_answer_match and has_answer:
                                        extracted_answer = extracted_answer_match.group(1)
                                        
                                    response_json = {
                                        'has_answer': has_answer,
                                        'extracted_answer': extracted_answer,
                                        'annotations': annotations
                                    }
                                    parse_error = None
                                else:
                                    # Fallback for old 'codes' array
                                    codes_match = re.search(r'"codes"\s*:\s*\[(.*?)\]', result)
                                    reasoning_match = re.search(r'"reasoning"\s*:\s*"(.*?)"', result, re.DOTALL)
                                    
                                    has_answer_match = re.search(r'"has_answer"\s*:\s*(true|false)', result, re.IGNORECASE)
                                    extracted_answer_match = re.search(r'"extracted_answer"\s*:\s*"(.*?)"', result, re.DOTALL)
                                    
                                    has_answer = False
                                    if has_answer_match and has_answer_match.group(1).lower() == 'true':
                                        has_answer = True
                                        
                                    extracted_answer = None
                                    if extracted_answer_match and has_answer:
                                        extracted_answer = extracted_answer_match.group(1)
                                        
                                    if codes_match:
                                        codes_str = codes_match.group(1)
                                        code_list = [c.strip(' "\'') for c in codes_str.split(',')]
                                        reasoning_str = reasoning_match.group(1) if reasoning_match else 'Extracted from malformed JSON'
                                        response_json = {
                                            'has_answer': has_answer,
                                            'extracted_answer': extracted_answer,
                                            'annotations': [{'code': c, 'evidence': '', 'reasoning': reasoning_str} for c in code_list]
                                        }
                                        parse_error = None
                            except:
                                pass
                
                if response_json is None:
                    raise ValueError(f"Failed to parse JSON: {str(parse_error)}")
                
                # Validate response has required fields
                if 'annotations' not in response_json:
                    raise ValueError("Response missing 'annotations' field")
                
                break
            except Exception as e:
                retry_count += 1
                print(f"Attempt {retry_count} failed for chunk {idx+1}, retrying... Error: {str(e)}")
                if retry_count >= max_retries:
                    print(f"Failed to get response after {max_retries} retries. Using default codes.")
                    response_json = {'has_answer': False, 'extracted_answer': None, 'annotations': [{'code': 'E', 'evidence': '', 'reasoning': 'Failed to annotate'}]}
                    break
        
        # Update chunk with annotations
        if isinstance(chunk, dict):
            annotated_chunk = chunk.copy()
        elif isinstance(chunk, list):
            annotated_chunk = {'sentence': str(chunk)}
        else:
            annotated_chunk = {'sentence': str(chunk)}
            
        annotations = response_json.get('annotations', [])
        has_answer = response_json.get('has_answer', False)
        extracted_answer = response_json.get('extracted_answer', None)
        
        annotated_chunk['ontology_tag'] = [ann['code'] for ann in annotations]
        
        if has_answer:
            annotated_chunk['has_answer'] = has_answer
            annotated_chunk['extracted_answer'] = extracted_answer
        
        # Combine evidence and reasoning for each code into a single string for backward compatibility
        reasoning_parts = []
        for ann in annotations:
            code = ann.get('code', '')
            evidence = ann.get('evidence', '')
            reasoning = ann.get('reasoning', '')
            reasoning_parts.append(f"[{code}] Evidence: \"{evidence}\" - {reasoning}")
            
        annotated_chunk['sentence-category-reason'] = "\n".join(reasoning_parts)
        
        # --- TEMPORARY BREAKPOINT ---
        print("\n" + "="*80)
        print("DEBUG: FIRST CHUNK GENERATION RESULT")
        print(f"Sentence: {annotated_chunk.get('sentence')}")
        print(f"Tags: {annotated_chunk.get('ontology_tag')}")
        print(f"Reasoning:\n{annotated_chunk.get('sentence-category-reason')}")
        print("="*80 + "\n")
        import sys
        print("Breakpoint reached. Exiting as requested.")
        sys.exit(0)
        # -----------------------------

        annotated_chunks.append(annotated_chunk)
    
    # Save to output file
    target_path = f"{output_path}"
    if not os.path.exists(target_path):
        os.makedirs(target_path)
    
    with open(f"{target_path}/{sample_index + 1}.json", "w") as f:
        json.dump(annotated_chunks, f, indent=2)

    return annotated_chunks


def process_new_data_chunks_stateless(client, chunk_list, problem_text, sample_index, guidebook=True, model="gpt-4.1", output_path='result'):
    """
    Process chunks statelessly (without looking at previous tags), suitable for Batch API simulation.
    
    Args:
        client: OpenAI/OpenRouter client
        chunk_list: List of chunk dictionaries (from chunked JSON file)
        problem_text: The original problem/instruction
        sample_index: File index for saving
        guidebook: Whether to include heuristics guidebook
        model: Model name for annotation
        output_path: Where to save results
    
    Returns:
        list: Annotated chunk list
    """
    general_instruction_prompt = """In this project, we analyze the reasoning process of Large Reasoning Models (LRMs) by identifying specific heuristic strategies used during problem solving. Given a chunk of text from the model's response, you need to annotate it with heuristic codes from the guidebook.

The heuristic codes include:
- H1-H13: Various heuristic strategies (e.g., H1: Changing representation, H4: Problem classification, H10: Using theorems)
- N1-N4: Non-heuristic categories (e.g., N1: Literal repetition, N2: Technical performance, N4: Answer)

IMPORTANT ANNOTATION RULES:
1. Use Sub-codes When Available: For categories with sub-codes (e.g., H4a/b, H11a/b, H13a-f), you MUST use the specific sub-code if it applies. ONLY use the parent code (e.g., H13) as a last resort fallback if NO sub-code fits.
2. Multi-Tagging Allowed: A chunk can have multiple heuristic codes if multiple strategies are present.
3. Non-Heuristics Only When No Heuristics Present: Only tag N1-N4 when there are absolutely no heuristic strategies (H1-H13) in the chunk.
4. Mandatory Evidence Citation: You MUST explicitly quote the specific sentence or phrase from the chunk that serves as evidence for your chosen codes in the reasoning field."""

    if guidebook:
        general_instruction_prompt += "\\n\\nThe [Guidebook] section provides detailed definitions of each heuristic code."
    
    general_instruction_prompt += """\nThe [Math Problem] section provides the math problem.
The [Previous Context] section shows the text of the recent 4 chunks. NOTE: Previous tags are NOT provided in this mode.
The [Current Chunk] section provides the chunk that needs to be annotated.
The [Format] section specifies the expected output format."""

    format_prompt = (
        "You should format the output in JSON format with the following structure:\\n"
        "{\\n"
        "  'has_answer': true or false,\\n"
        "  'extracted_answer': 'The final answer if has_answer is true, else null',\\n"
        "  'annotations': [\\n"
        "    {\\n"
        "      'code': 'H4',\\n"
        "      'evidence': 'Direct quote from the chunk serving as evidence',\\n"
        "      'reasoning': 'Brief explanation of why this code applies'\\n"
        "    }\\n"
        "  ]\\n"
        "}\\n"
        "The 'annotations' field should be a list of these objects. Each object must contain exactly one 'code'. 'has_answer' is a boolean indicating if the model reached its final answer in this chunk. If true, extract the answer text into 'extracted_answer'."
    )

    # Context window size
    CONTEXT_WINDOW = 4
    
    annotated_chunks = []
    
    # If chunk_list is a dict, check if it's formatted as {"sentences": [...]}
    if isinstance(chunk_list, dict):
        if 'sentences' in chunk_list and isinstance(chunk_list['sentences'], list):
            chunk_list = chunk_list['sentences']
        else:
            # some formats list chunks as a dict with string indices
            chunk_list = list(chunk_list.values())
            
    # Prepare guidebook text
    if guidebook:
        guidebook_text = guidebook_heuristics_prompt
    else:
        guidebook_text = None
        
    for idx, chunk in enumerate(tqdm(chunk_list, desc="Annotating chunks (Stateless)")):
        
        messages = generate_stateless_messages(
            chunk=chunk,
            chunk_idx=idx,
            chunk_list=chunk_list,
            problem_text=problem_text,
            guidebook_text=guidebook_text
        )
        
        # Reduced verbosity
        if idx % 5 == 0:
            print(f"Processing chunk (Stateless) {idx+1}/{len(chunk_list)}...")

        
        max_retries = 5
        retry_count = 0
        response_json = None
        
        while retry_count < max_retries:
            try:
                # Use OpenRouter for all models
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"}
                )
                result = response.choices[0].message.content
                
                # Robust JSON parsing logic
                parse_error = None
                
                # Strategy 1: Try with Pydantic if available
                use_pydantic = False
                try:
                    from .schemas import ChunkAnnotation
                    use_pydantic = True
                except ImportError:
                    pass
                
                if use_pydantic:
                    try:
                        try:
                            raw_json = json.loads(result)
                        except json.JSONDecodeError:
                            fixed_result = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', result)
                            raw_json = json.loads(fixed_result)
                        
                        validated = ChunkAnnotation(**raw_json)
                        response_json = validated.model_dump()
                        parse_error = None
                    except Exception as e:
                        parse_error = e
                        response_json = None
                
                # Strategy 2: Standard JSON parsing
                if response_json is None:
                    try:
                        response_json = json.loads(result)
                        parse_error = None
                    except json.JSONDecodeError as e:
                        parse_error = e
                        # Strategy 3: Try to fix common LaTeX escape issues
                        fixed_result = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', result)
                        try:
                            response_json = json.loads(fixed_result)
                            parse_error = None
                        except json.JSONDecodeError:
                            # Strategy 4: Raw string parsing fallback
                            try:
                                # Look for code, evidence, and reasoning matches in an array
                                code_matches = re.finditer(r'"code"\s*:\s*"(.*?)"', result)
                                evidence_matches = re.finditer(r'"evidence"\s*:\s*"(.*?)"', result, re.DOTALL)
                                reasoning_matches = re.finditer(r'"reasoning"\s*:\s*"(.*?)"', result, re.DOTALL)
                                
                                codes = [m.group(1) for m in code_matches]
                                evidences = [m.group(1) for m in evidence_matches]
                                reasonings = [m.group(1) for m in reasoning_matches]
                                
                                if codes:
                                    annotations = []
                                    for i, code in enumerate(codes):
                                        evidence = evidences[i] if i < len(evidences) else "Extracted from malformed JSON"
                                        reasoning = reasonings[i] if i < len(reasonings) else "Extracted from malformed JSON"
                                        annotations.append({'code': code, 'evidence': evidence, 'reasoning': reasoning})
                                    response_json = {'annotations': annotations}
                                    parse_error = None
                                else:
                                    # Fallback for old 'codes' array
                                    codes_match = re.search(r'"codes"\s*:\s*\[(.*?)\]', result)
                                    reasoning_match = re.search(r'"reasoning"\s*:\s*"(.*?)"', result, re.DOTALL)
                                    if codes_match:
                                        codes_str = codes_match.group(1)
                                        code_list = [c.strip(' "\'') for c in codes_str.split(',')]
                                        reasoning_str = reasoning_match.group(1) if reasoning_match else 'Extracted from malformed JSON'
                                        response_json = {'annotations': [{'code': c, 'evidence': '', 'reasoning': reasoning_str} for c in code_list]}
                                        parse_error = None
                            except:
                                pass
                
                if response_json is None:
                    raise ValueError(f"Failed to parse JSON: {str(parse_error)}")
                
                # Validate response has required fields
                if 'annotations' not in response_json:
                    raise ValueError("Response missing 'annotations' field")
                
                break
            except Exception as e:
                retry_count += 1
                print(f"Attempt {retry_count} failed for chunk {idx+1} (Stateless), retrying... Error: {str(e)}")
                if retry_count >= max_retries:
                    print(f"Failed to get response after {max_retries} retries. Using default codes.")
                    response_json = {'annotations': [{'code': 'E', 'evidence': '', 'reasoning': 'Failed to annotate'}]}
                    break
        
        # Update chunk with annotations
        if isinstance(chunk, dict):
            annotated_chunk = chunk.copy()
        elif isinstance(chunk, list):
            annotated_chunk = {'sentence': str(chunk)}
        else:
            annotated_chunk = {'sentence': str(chunk)}
            
        annotations = response_json.get('annotations', [])
        has_answer = response_json.get('has_answer', False)
        extracted_answer = response_json.get('extracted_answer', None)
        
        annotated_chunk['ontology_tag'] = [ann['code'] for ann in annotations]
        
        if has_answer:
            annotated_chunk['has_answer'] = has_answer
            annotated_chunk['extracted_answer'] = extracted_answer
        
        reasoning_parts = []
        for ann in annotations:
            code = ann.get('code', '')
            evidence = ann.get('evidence', '')
            reasoning = ann.get('reasoning', '')
            reasoning_parts.append(f"[{code}] Evidence: \"{evidence}\" - {reasoning}")
            
        annotated_chunk['sentence-category-reason'] = "\n".join(reasoning_parts)
        
        # --- TEMPORARY BREAKPOINT ---
        print("\n" + "="*80)
        print("DEBUG: FIRST CHUNK GENERATION RESULT")
        print(f"Sentence: {annotated_chunk.get('sentence')}")
        print(f"Tags: {annotated_chunk.get('ontology_tag')}")
        print(f"Reasoning:\n{annotated_chunk.get('sentence-category-reason')}")
        print("="*80 + "\n")
        import sys
        print("Breakpoint reached. Exiting as requested.")
        sys.exit(0)
        # -----------------------------

        annotated_chunks.append(annotated_chunk)
    
    target_path = f"{output_path}"
    if not os.path.exists(target_path):
        os.makedirs(target_path)
        
    with open(f"{target_path}/{sample_index + 1}.json", "w") as f:
        json.dump(annotated_chunks, f, indent=2)
    
    return annotated_chunks


def generate_stateless_messages(chunk, chunk_idx, chunk_list, problem_text, guidebook_text=None):
    """
    Generates the exact messages array for stateless labeling API calls.
    Extracts the prompt building logic from `process_new_data_chunks_stateless`.
    """
    CONTEXT_WINDOW = 4

    general_instruction_prompt = """In this project, we analyze the reasoning process of Large Reasoning Models (LRMs) by identifying specific heuristic strategies used during problem solving. Given a chunk of text from the model's response, you need to annotate it with heuristic codes from the guidebook.

The heuristic codes include:
- H1-H13: Various heuristic strategies (e.g., H1: Changing representation, H4: Problem classification, H10: Using theorems)
- N1-N4: Non-heuristic categories (e.g., N1: Literal repetition, N2: Technical performance, N4: Answer)

IMPORTANT ANNOTATION RULES:
1. Use Sub-codes When Available: For categories with sub-codes (e.g., H4a/b, H11a/b, H13a-f), you MUST use the specific sub-code if it applies. ONLY use the parent code (e.g., H13) as a last resort fallback if NO sub-code fits.
2. Multi-Tagging Allowed: A chunk can have multiple heuristic codes if multiple strategies are present.
3. Non-Heuristics Only When No Heuristics Present: Only tag N1-N4 when there are absolutely no heuristic strategies (H1-H13) in the chunk.
4. Mandatory Evidence Citation: You MUST explicitly quote the specific sentence or phrase from the chunk that serves as evidence for your chosen codes in the reasoning field."""

    if guidebook_text:
        general_instruction_prompt += "\\n\\nThe [Guidebook] section provides detailed definitions of each heuristic code."
    
    general_instruction_prompt += """\nThe [Math Problem] section provides the math problem.
The [Previous Context] section shows the text of the recent 4 chunks. NOTE: Previous tags are NOT provided in this mode.
The [Current Chunk] section provides the chunk that needs to be annotated.
The [Format] section specifies the expected output format."""

    format_prompt = (
        "You should format the output in JSON format with the following structure:\\n"
        "{\\n"
        "  'has_answer': true or false,\\n"
        "  'extracted_answer': 'The final answer if has_answer is true, else null',\\n"
        "  'annotations': [\\n"
        "    {\\n"
        "      'code': 'H4',\\n"
        "      'evidence': 'Direct quote from the chunk serving as evidence',\\n"
        "      'reasoning': 'Brief explanation of why this code applies'\\n"
        "    }\\n"
        "  ]\\n"
        "}\\n"
        "The 'annotations' field should be a list of these objects. Each object must contain exactly one 'code'. 'has_answer' is a boolean indicating if the model reached its final answer in this chunk. If true, extract the answer text into 'extracted_answer'."
    )

    # Build context from previous chunks (TEXT ONLY)
    context_start = max(0, chunk_idx - CONTEXT_WINDOW)
    previous_chunks = chunk_list[context_start:chunk_idx]
    
    # Build context prompt (Text only)
    if len(previous_chunks) == 0:
        context_prompt = "There are no previous chunks."
    else:
        context_lines = []
        for i, prev_chunk in enumerate(previous_chunks):
            chunk_num = context_start + i + 1
            text = prev_chunk.get('sentence', '')[:200] if isinstance(prev_chunk, dict) else str(prev_chunk)[:200]
            context_lines.append(f"Chunk {chunk_num}: {text}...")
        context_prompt = "Recent chunks (Text Only):\\n" + "\\n".join(context_lines)
    
    # Current chunk text
    current_chunk_text = chunk.get('sentence', '') if isinstance(chunk, dict) else str(chunk)
    current_chunk_prompt = f"Chunk to annotate:\\n{current_chunk_text}"
    
    # Build combined prompt
    combined_prompt = general_instruction_prompt
    
    if guidebook_text:
        combined_prompt += f"\\n\\n[Guidebook]\\n{guidebook_text}\\n[End of the Guidebook]"
    
    combined_prompt += f"\\n\\n[Math Problem]\\n{problem_text}\\n[End of the Math Problem]"
    combined_prompt += f"\\n\\n[Previous Context]\\n{context_prompt}\\n[End of the Previous Context]"
    combined_prompt += f"\\n\\n[Current Chunk]\\n{current_chunk_prompt}\\n[End of the Current Chunk]"
    combined_prompt += f"\\n\\n[Format]\\n{format_prompt}\\n[End of the Format]"
    
    # Simplified reminder for stateless mode
    combined_prompt += """

⚠️ CRITICAL REMINDER - HEURISTIC-FIRST APPROACH ⚠️

Before annotating, follow this decision tree:
1. ✓ First: Carefully scan for ANY heuristic activity (H1-H13)
2. ✓ If you find EVEN ONE heuristic → Tag with H codes ONLY
3. ✓ ONLY if absolutely NO heuristics → Then use N codes

Common mistakes to avoid:
- ❌ DON'T tag modular arithmetic operations (e.g., "617 mod 18 = 5") as N2 → Use H1 or H10
- ❌ DON'T tag finding inverses (e.g., "find inverse of 5 mod 18") as N2 → Use H10
- ❌ DON'T tag strategic substitutions (e.g., "Let u = x²") as N2 → Use H3
- ❌ DON'T tag problem-to-equation conversions as N2 → Use H1

N2 is ONLY for pure arithmetic after strategy is set (e.g., "2+3=5", "2x+3x=5x").

Now, annotate the current chunk following the heuristic-first approach."""

    return [{"role": "user", "content": combined_prompt}]


class SemanticSpaceMemory:
    def __init__(self):
        self.spaces = {
            0: {
                "register": "Natural Language Context",
                "constraints": "Original problem constraints",
                "core_tools": "None",
                "summary": "Initial mathematical problem phrasing.",
                "anchor_text": "The original problem instruction."
            }
        }
        self.current_space_id = 0
        self.next_id = 1

    def transition(self, decision, target_space_id=None, new_space_definition=None):
        if decision == "NEW" and new_space_definition:
            space_id = self.next_id
            self.spaces[space_id] = new_space_definition
            self.current_space_id = space_id
            self.next_id += 1
            return space_id
        elif decision == "RETURN" and target_space_id is not None:
            try:
                # Target space id can sometimes be given as string
                tid = int(target_space_id)
                if tid in self.spaces:
                    self.current_space_id = tid
            except (ValueError, TypeError):
                pass
            return self.current_space_id
        elif decision == "MAINTAIN":
            return self.current_space_id
        return self.current_space_id
        
    def get_history_prompt(self):
        prompt = "Memory of Past Spaces:\n"
        for space_id, definition in self.spaces.items():
            prompt += f"[ID: {space_id}]\n"
            prompt += f"  Register: {definition.get('register', '')}\n"
            prompt += f"  Constraints: {definition.get('constraints', '')}\n"
            prompt += f"  Core Tools: {definition.get('core_tools', '')}\n"
            prompt += f"  Summary: {definition.get('summary', '')}\n"
            prompt += f"  Anchor Text: {definition.get('anchor_text', '')}\n\n"
        return prompt


def process_semantic_space_chunks(client, chunk_list, problem_text, sample_index, guidebook=True, model="gpt-4.1", output_path='result', without_llm=False):
    # Note: chunk_list already has heuristic tags
    
    # If chunk_list is a dict, check if it's formatted as {"sentences": [...]}
    if isinstance(chunk_list, dict):
        if 'sentences' in chunk_list and isinstance(chunk_list['sentences'], list):
            chunk_list = chunk_list['sentences']
        else:
            # some formats list chunks as a dict with string indices
            chunk_list = list(chunk_list.values())

    memory = SemanticSpaceMemory()
    
    if guidebook:
        guidebook_path = os.path.join(os.path.dirname(_current_dir), "guidebook", "semantic_space_guide.md")
        try:
            with open(guidebook_path, "r") as f:
                guide_text = f.read()
        except:
            guide_text = ""
    else:
        guide_text = ""

    general_instruction = """You are an expert mathematical cognitive scientist analyzing the reasoning traces of a Large Reasoning Model.
Your task is to track state changes in the "Semantic Space" (the problem's fundamental representation format, constraints, and overarching framework).

CRITICAL RULE FOR "NEW" vs "RETURN": Before deciding to create a "NEW" space, you MUST strictly compare the current chunk against the [Memory of Past Spaces]. If the model is reverting to a previously established equation, constraint, or tool that exists in the memory (even if it's from many steps ago), you MUST output "RETURN" and specify the corresponding target_space_id. Creating a "NEW" space should be your last resort, used ONLY when a fundamentally unprecedented mathematical environment is introduced.

CRITICAL RULE FOR "RETURN" vs "MAINTAIN":
If the model explores and abandons a hypothetical sub-space within the exact same chunk to go back to the currently active space from the previous chunk, you MUST output "MAINTAIN", not "RETURN". "RETURN" should ONLY be used when the active space from the previous chunk is actually different from the space being reverted to. If the space you are "returning" to is already the current active space, it is a "MAINTAIN".

CRITICAL RULE FOR "H13b (Deriving differently)" and alternative methods:
Models often verify a completed solution by deriving it differently (H13b) or presenting an "Alternative Method". 
- This ONLY constitutes a "NEW" space if it introduces a fundamentally different mathematical structural framework (e.g., switching from geometric coordinate-based algebra to a coordinate-free Gram matrix approach, or from direct counting to generating functions). 
- If the alternative method merely uses a different formula or minor variation within the exact SAME overarching structural framework (e.g., using a different trigonometric identity within the same coordinate system, or subtracting from total vs direct counting), it is a "MAINTAIN". Look for a deep structural shift to justify "NEW"."""

    format_instruction = """
Output JSON exactly in this format. Do not use Markdown block syntax around the JSON, just reply with the raw JSON object.
When creating a "NEW" space definition, you must provide both summary and anchor_text.
- summary: A brief 1-2 sentence high-level description of the mathematical strategy and intent of this space.
- anchor_text: The exact mathematical equation, constraint declaration, or pivotal quote from the text chunk that essentially "defines" this space.

{
  "decision": "NEW", 
  "rationale": "Explanation of why this decision was made according to the guidebook.",
  "target_space_id": 1, 
  "new_space_definition": {
      "register": "",
      "constraints": "",
      "core_tools": "",
      "summary": "",
      "anchor_text": ""
  }
}
Note: For "MAINTAIN" or "RETURN", do NOT include the "new_space_definition" field.
"""

    trigger_heuristics = {"H1", "H2", "H3", "H5", "H8", "H11"}
    without_llm_trigger_heuristics = {"H1", "H2"}
    CONTEXT_WINDOW = 10
    
    has_seen_any_heuristic = False
    last_was_h1_h4 = False
    
    for idx, chunk in enumerate(tqdm(chunk_list, desc="Semantic Space Tagging")):
        heuristics = chunk.get("ontology_tag", [])
        
        trigger_semantic_space = False
        
        if heuristics:
            # Check if there is any 'H' tag
            has_h_tag = any(isinstance(h, str) and h.startswith('H') for h in heuristics)
            if has_h_tag and not has_seen_any_heuristic:
                has_seen_any_heuristic = True
                trigger_semantic_space = True
                
        # Check if any explicitly defined trigger heuristic is present
        base_heuristics = [h.split('a')[0].split('b')[0].split('c')[0].split('d')[0].split('e')[0].split('f')[0] if isinstance(h, str) else h for h in heuristics]
        
        if without_llm:
            triggers_found = [h for h in base_heuristics if isinstance(h, str) and h in without_llm_trigger_heuristics]
        else:
            triggers_found = [h for h in base_heuristics if isinstance(h, str) and h in trigger_heuristics]
        
        if triggers_found:
            trigger_semantic_space = True
            
        if without_llm:
            if triggers_found:
                if not last_was_h1_h4:
                    decision = "NEW"
                else:
                    decision = "MAINTAIN"
                last_was_h1_h4 = True
            else:
                last_was_h1_h4 = False
                if trigger_semantic_space: # First H-tag trigger
                    decision = "NEW"
                else:
                    continue
                
            rationale = f"Heuristic trigger found ({', '.join(triggers_found) if triggers_found else 'First H-tag'}) in without_llm mode."
            new_def = {
                "register": "",
                "constraints": "",
                "core_tools": "",
                "summary": "",
                "anchor_text": ""
            }
            active_id = memory.transition(decision, new_space_definition=new_def if decision == "NEW" else None)
            
            semantic_space_record = {
                "decision": decision,
                "rationale": rationale,
                "active_space_id": active_id
            }
            if decision == "NEW":
                semantic_space_record["new_space_definition"] = new_def
                
            chunk["semantic_space"] = semantic_space_record
            continue
        
        # Reset last_was_h1_h4 if not in without_llm mode
        last_was_h1_h4 = False

        # Build context prompt
        context_start = max(0, idx - CONTEXT_WINDOW)
        recent_chunks = chunk_list[context_start:idx]
        context_lines = []
        for i, prev_chunk in enumerate(recent_chunks):
            chunk_num = context_start + i + 1
            text = prev_chunk.get('sentence', '')[:200]
            context_lines.append(f"Chunk {chunk_num}: {text}")
        recent_context_str = "Recent Context (Last 10 chunks):\n" + "\n".join(context_lines)
        
        history_prompt = memory.get_history_prompt()
        
        current_chunk_text = chunk.get("sentence", "")
        current_tags = ", ".join(heuristics)
        current_prompt = f"Current Chunk to Assess:\n{current_chunk_text}\n\nTriggered Heuristics: {current_tags}"
        
        combined_prompt = general_instruction
        if guidebook:
            combined_prompt += f"\n\n[Guidebook]\n{guide_text}\n[End of Guidebook]"
        
        combined_prompt += f"\n\n[Math Problem]\n{problem_text}\n[End of Math Problem]"
        combined_prompt += f"\n\n[Memory of Past Spaces]\n{history_prompt}\n[End of Memory of Past Spaces]"
        combined_prompt += f"\n\n[Recent Context]\n{recent_context_str}\n[End of Recent Context]"
        combined_prompt += f"\n\n[Current Chunk]\n{current_prompt}\n[End of Current Chunk]"
        combined_prompt += f"\n\n[Format Instruction]\n{format_instruction}\n[End of Format Instruction]"
        
        messages = [
            {"role": "user", "content": combined_prompt}
        ]
        
        max_retries = 3
        retry_count = 0
        response_json = None
        
        while retry_count < max_retries:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"}
                )
                result = response.choices[0].message.content
                
                # Cleanup potential JSON wrap
                if result.startswith("```json"):
                    result = result.split("```json", 1)[1]
                if result.endswith("```"):
                    result = result.rsplit("```", 1)[0]
                
                response_json = json.loads(result.strip())
                break
            except Exception as e:
                print(f"Exception during semantic space API call for chunk {idx+1}: {e}")
                retry_count += 1
                if retry_count >= max_retries:
                    raise Exception(f"Failed semantic space tagging for chunk {idx+1} after {max_retries} retries. Raising to file-level retry.")
        
        # Process the decision
        decision = response_json.get("decision", "MAINTAIN")
        target_id = response_json.get("target_space_id", memory.current_space_id)
        new_def = response_json.get("new_space_definition", None)
        rationale = response_json.get("rationale", "")
        
        active_id = memory.transition(decision, target_id, new_def)
        
        semantic_space_record = {
            "decision": decision,
            "rationale": rationale,
            "active_space_id": active_id
        }
        
        if decision == "NEW" and new_def:
            semantic_space_record["new_space_definition"] = new_def
            
        chunk["semantic_space"] = semantic_space_record
        
    target_path = f"{output_path}"
    if not os.path.exists(target_path):
        os.makedirs(target_path)
    
    with open(f"{target_path}/{sample_index + 1}.json", "w") as f:
        json.dump(chunk_list, f, indent=2)

    return chunk_list