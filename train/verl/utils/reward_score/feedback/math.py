# Copyright 2024 Bytedance Ltd. and/or its affiliates
# Copyright 2022 EleutherAI and the HuggingFace Inc. team. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Adapted from https://github.com/EleutherAI/lm-evaluation-harness/blob/main/lm_eval/tasks/hendrycks_math/utils.py

import json
import random
import re
import signal
from pathlib import Path
from typing import Optional
from math_verify import parse as mv_parse, verify as mv_verify

FORMAT_PENALTY = False
HEURISTICS_PATH = Path(__file__).with_name("heuristics.json")


def _sample_feedback_sentence(sample_size: int = 3) -> str:
    with HEURISTICS_PATH.open("r", encoding="utf-8") as f:
        heuristics = json.load(f)

    sampled = random.sample(heuristics, k=min(sample_size, len(heuristics)))
    strategy_sentences = []
    for i, item in enumerate(sampled):
        name = item.get("name", "")
        description = item.get("description", "").strip()
        examples = item.get("examples", [])
        question = item.get("guiding_question", "").strip()
        sentence = f'\n\nHeuristic {i+1} - "{name}"'
        sentence += f": {description}\n\nExample:"
        for _item in examples:
            sentence += f"\n  - {_item.strip()}"
        sentence += f"\n\nAsk yourself: {question}\n"
        strategy_sentences.append(sentence)

    return "Your answer is incorrect. " "Try using the heuristics below:" + " ".join(
        strategy_sentences
    )



def last_boxed_only_string(string: str) -> Optional[str]:
    """Extract the last LaTeX boxed expression from a string.

    Args:
        string: Input string containing LaTeX code

    Returns:
        The last boxed expression or None if not found
    """
    idx = string.rfind(r"\boxed{")
    if idx < 0:
        return None

    i = idx
    right_brace_idx = None
    num_left_braces_open = 0

    while i < len(string):
        if string[i] == "{":
            num_left_braces_open += 1
        if string[i] == "}":
            num_left_braces_open -= 1
            if num_left_braces_open == 0:
                right_brace_idx = i
                break
        i += 1

    return string[idx : right_brace_idx + 1] if right_brace_idx is not None else ""#None


def remove_boxed(s: str) -> str:
    r"""Remove the LaTeX boxed command from a string.

    Args:
        s: String with format "\boxed{content}"

    Returns:
        The content inside the boxed command
    """
    left = r"\boxed{"
    #assert s[: len(left)] == left, f"box error: {s}"
    #assert s[-1] == "}", f"box error: {s}"
    if s[: len(left)] == left and  s[-1] == "}":
        return s[len(left) : -1]
    else:
        return ""


def normalize_ground_truth_answer(answer: str) -> str:
    """Normalize ground truth into a comparable math answer string.

    Some datasets store full worked solutions in `ground_truth` and include
    the final answer only as a trailing `\\boxed{...}` expression.
    In those cases we should compare against the boxed content, not the full
    rationale text.
    """
    if not isinstance(answer, str):
        return answer

    boxed = last_boxed_only_string(answer)
    if boxed is not None:
        extracted = remove_boxed(boxed)
        if extracted != "":
            return extracted
    return answer


class timeout:
    def __init__(self, seconds=1, error_message="Timeout"):
        self.seconds = seconds
        self.error_message = error_message

    def handle_timeout(self, signum, frame):
        raise TimeoutError(self.error_message)

    def __enter__(self):
        signal.signal(signal.SIGALRM, self.handle_timeout)
        signal.alarm(self.seconds)

    def __exit__(self, type, value, traceback):
        signal.alarm(0)


def is_correct_strict_box(
    pred: str, gt: str, pause_tokens_index: Optional[list[int]] = None
) -> tuple[int, Optional[str]]:
    """Check if the prediction is correct using strict boxed answer criteria.

    Args:
        pred: The prediction string
        gt: The ground truth answer
        pause_tokens_index: Indices of pause tokens

    Returns:
        Tuple of (score, extracted_prediction)
    """
    # Extract and check the boxed answer.
    # NOTE:
    # Older logic only looked at the last 100 chars. Instruct models frequently
    # append long trailing text after the final boxed answer, which causes false
    # "format error" rewards. We search the whole generation (or post-pause
    # segment) and only fall back to the full response if needed.
    search_text = pred
    if pause_tokens_index is not None:
        assert len(pause_tokens_index) == 4
        search_text = pred[pause_tokens_index[-1] :]

    boxed_pred = last_boxed_only_string(search_text)
    if boxed_pred is None and search_text is not pred:
        boxed_pred = last_boxed_only_string(pred)
    extracted_pred = remove_boxed(boxed_pred) if boxed_pred is not None else None

    return extracted_pred == gt, extracted_pred


def verify(
    solution_str: str, answer: str, pause_tokens_index: Optional[list[int]] = None
) -> bool:
    """Verify if the solution is correct.

    Args:
        solution_str: The solution string to verify
        answer: The ground truth answer
        strict_box_verify: Whether to use strict box verification
        pause_tokens_index: Indices of pause tokens

    Returns:
        True if the solution is correct, False otherwise
    """
    normalized_answer = normalize_ground_truth_answer(answer)
    correct, pred = is_correct_strict_box(solution_str, normalized_answer, pause_tokens_index)
    if pred is None:
        pred = ""

    # try Math-Verify equivalence check
    if not correct and pred != "":
        try:
            with timeout(seconds=5):
                gold_expr = mv_parse(normalized_answer)
                pred_expr = mv_parse(pred)
                correct = mv_verify(gold_expr, pred_expr)
        except Exception:  # ignore any parsing/verification errors
            pass
    return correct, pred


def compute_score(
    solution_str: str,
    ground_truth: str,
    extra_info = None,
    pause_tokens_index: Optional[list[int]] = None,
    format_feedback: bool = True,
    correctness_feedback: bool = False,
) -> float:
    """Compute the reward score for a solution.

    Args:
        solution_str: The solution string
        ground_truth: The ground truth answer
        config: Configuration object containing reward model settings
        pause_tokens_index: Indices of pause tokens

    Returns:
        Reward score (1.0 for correct, 0 for incorrect)
    """
    split = extra_info.get("split", "test")
    was_truncated = extra_info.get("truncated", False)

    # Verify the solution
    correct, pred = verify(solution_str, ground_truth, pause_tokens_index)

    reward = 1.0 if correct else 0.0
    score = reward
    incorrect_format = pred is None or pred == ""
    was_truncated = extra_info.get("truncated", False)
    if FORMAT_PENALTY and split == "train" and incorrect_format and (not was_truncated):
        score -= 0.5

    # Generate explicit feedback for format errors (analogous to code feedback)
    # feedback = ""
    # if incorrect_format and not was_truncated and format_feedback:
    #     feedback = "Your answer had the wrong format. The solution must be given in the format: \\boxed{your_answer}."
    # elif was_truncated and format_feedback:
    #     feedback = "Your response was truncated because it exceeded the maximum length."
    # elif not correct and correctness_feedback:
    #     feedback = _sample_feedback_sentence(3) + "The correct answer is {ground_truth}."
    
    feedback = ""
    if incorrect_format and not was_truncated and format_feedback:
        feedback = "Your answer had the wrong format. The solution must be given in the format: \\boxed{your_answer}."
    elif was_truncated and format_feedback:
        feedback = "Your response was truncated because it exceeded the maximum length."
    elif not correct and correctness_feedback:
        feedback = f"Your answer is incorrect. The correct answer is {ground_truth}."

    return {
        "score": score,
        "acc": reward,
        "pred": pred,
        "incorrect_format": 1 if incorrect_format else 0,
        "truncated": 1 if was_truncated else 0,
        "truncated_and_missing_answer": 1 if incorrect_format and was_truncated else 0,
        "feedback": feedback,
    }
