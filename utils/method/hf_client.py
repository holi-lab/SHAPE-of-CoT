import json
import os
import inspect
from vllm import LLM, SamplingParams
from vllm.sampling_params import StructuredOutputsParams


def _make_response(content, model_name):
    """Return a minimal OpenAI-compatible response object."""
    class Message:
        def __init__(self, content):
            self.content = content

    class Choice:
        def __init__(self, content):
            self.message = Message(content)
            self.finish_reason = "stop"

    class Response:
        def __init__(self, content, model_name):
            self.choices = [Choice(content)]
            self.model = model_name

    return Response(content, model_name)


class HFClient:
    """
    A minimal wrapper using vLLM to make local models compatible 
    with the OpenAI client interface, providing significantly 
    faster inference than standard transformers.
    """
    def __init__(self, model_id, gpu_memory_utilization=0.9, max_model_len=16384,
                 temperature=0.6, max_tokens=4096, top_p=0.95, top_k=20,
                 min_p=0.0, presence_penalty=0.0, repetition_penalty=1.0):
        print(f"Loading Model with vLLM: {model_id}...")

        self.default_sampling = dict(
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            presence_penalty=presence_penalty,
            repetition_penalty=repetition_penalty,
        )

        attention_backend = os.getenv("VLLM_ATTENTION_BACKEND", "").strip()
        attention_config = {"backend": attention_backend} if attention_backend else None
        gdn_prefill_backend = os.getenv("VLLM_GDN_PREFILL_BACKEND", "triton").strip()

        # vLLM constructor args changed across versions.
        # Build kwargs dynamically to stay compatible with older/newer releases.
        llm_kwargs = dict(
            model=model_id,
            trust_remote_code=True,
            gpu_memory_utilization=gpu_memory_utilization,
            max_model_len=max_model_len,
        )
        try:
            llm_sig = inspect.signature(LLM.__init__)
            llm_params = llm_sig.parameters
            if "attention_config" in llm_params and attention_config is not None:
                llm_kwargs["attention_config"] = attention_config
            if "additional_config" in llm_params:
                llm_kwargs["additional_config"] = {
                    "gdn_prefill_backend": gdn_prefill_backend
                }
        except (TypeError, ValueError):
            # If signature inspection fails, fall back to the stable core args only.
            pass

        self.llm = LLM(**llm_kwargs)
        self.tokenizer = self.llm.get_tokenizer()
        self.chat = self.Chat(self.llm, self.tokenizer, self.default_sampling)

    class Chat:
        def __init__(self, llm, tokenizer, default_sampling):
            self.completions = self.Completions(llm, tokenizer, default_sampling)

        class Completions:
            def __init__(self, llm, tokenizer, default_sampling):
                self.llm = llm
                self.tokenizer = tokenizer
                self.default_sampling = default_sampling

            def create(self, model, messages, response_format=None, **kwargs):
                """
                Mimics openai.chat.completions.create using vLLM.
                Honors response_format={"type": "json_object"} via guided decoding.
                """
                # Apply chat template
                # =False by default to match OpenRouter behavior.
                # Qwen3 chat template with enable_thinking=False still prepends
                # "<think>\n\n</think>\n\n" to the generation prompt, so we strip
                # it out to get a clean "<|im_start|>assistant\n" ending,
                # identical to what OpenRouter sends to the model.
                enable_thinking = kwargs.pop("enable_thinking", True)
                try:
                    prompt = self.tokenizer.apply_chat_template(
                        messages,
                        tokenize=False,
                        add_generation_prompt=True,
                        enable_thinking=enable_thinking,
                    )
                except TypeError:
                    prompt = self.tokenizer.apply_chat_template(
                        messages,
                        tokenize=False,
                        add_generation_prompt=True,
                    )

                # Strip the empty think block that Qwen3 template injects when
                # enable_thinking=False so generation starts without any prefix.
                if not enable_thinking:
                    import re as _re
                    prompt = _re.sub(r'<think>\s*</think>\s*', '', prompt)

                # Honor response_format: enforce JSON output via structured outputs
                structured_outputs = None
                fmt_type = None
                json_schema = None
                if isinstance(response_format, dict):
                    fmt_type = response_format.get("type")
                    json_schema = response_format.get("json_schema")
                elif hasattr(response_format, "type"):
                    fmt_type = response_format.type
                    json_schema = getattr(response_format, "json_schema", None)
                if fmt_type == "json_schema" and json_schema is not None:
                    structured_outputs = StructuredOutputsParams(json=json_schema)
                elif fmt_type == "json_object":
                    structured_outputs = StructuredOutputsParams(json_object=True)

                # Setup sampling parameters (caller kwargs override instance defaults)
                d = self.default_sampling
                sampling_params = SamplingParams(
                    temperature=kwargs.get("temperature", d["temperature"]),
                    max_tokens=kwargs.get("max_tokens", d["max_tokens"]),
                    top_p=kwargs.get("top_p", d["top_p"]),
                    top_k=kwargs.get("top_k", d["top_k"]),
                    min_p=kwargs.get("min_p", d["min_p"]),
                    presence_penalty=kwargs.get("presence_penalty", d["presence_penalty"]),
                    repetition_penalty=kwargs.get("repetition_penalty", d["repetition_penalty"]),
                    stop=kwargs.get("stop", None),
                    structured_outputs=structured_outputs,
                )

                # Generate (single prompt — kept for backward compatibility)
                outputs = self.llm.generate([prompt], sampling_params, use_tqdm=False)
                content = outputs[0].outputs[0].text
                
                return _make_response(content, model)

            def create_batch(self, model, messages_list, response_format=None, **kwargs):
                """
                Batch variant of create(): accepts a list of messages arrays and runs
                a single llm.generate() call for maximum vLLM throughput.

                Returns a list of Response objects in the same order as messages_list.
                """
                enable_thinking = kwargs.pop("enable_thinking", False)

                # Build prompts for all requests
                prompts = []
                for messages in messages_list:
                    try:
                        prompt = self.tokenizer.apply_chat_template(
                            messages,
                            tokenize=False,
                            add_generation_prompt=True,
                            enable_thinking=enable_thinking,
                        )
                    except TypeError:
                        prompt = self.tokenizer.apply_chat_template(
                            messages,
                            tokenize=False,
                            add_generation_prompt=True,
                        )
                    if not enable_thinking:
                        import re as _re
                        prompt = _re.sub(r'<think>\s*</think>\s*', '', prompt)
                    prompts.append(prompt)

                # Build structured output params
                structured_outputs = None
                fmt_type = None
                json_schema = None
                if isinstance(response_format, dict):
                    fmt_type = response_format.get("type")
                    json_schema = response_format.get("json_schema")
                elif hasattr(response_format, "type"):
                    fmt_type = response_format.type
                    json_schema = getattr(response_format, "json_schema", None)
                if fmt_type == "json_schema" and json_schema is not None:
                    structured_outputs = StructuredOutputsParams(json=json_schema)
                elif fmt_type == "json_object":
                    structured_outputs = StructuredOutputsParams(json_object=True)

                d = self.default_sampling
                sampling_params = SamplingParams(
                    temperature=kwargs.get("temperature", d["temperature"]),
                    max_tokens=kwargs.get("max_tokens", d["max_tokens"]),
                    top_p=kwargs.get("top_p", d["top_p"]),
                    top_k=kwargs.get("top_k", d["top_k"]),
                    min_p=kwargs.get("min_p", d["min_p"]),
                    presence_penalty=kwargs.get("presence_penalty", d["presence_penalty"]),
                    repetition_penalty=kwargs.get("repetition_penalty", d["repetition_penalty"]),
                    stop=kwargs.get("stop", None),
                    structured_outputs=structured_outputs,
                )

                outputs = self.llm.generate(prompts, sampling_params, use_tqdm=True)
                return [_make_response(out.outputs[0].text, model) for out in outputs]
