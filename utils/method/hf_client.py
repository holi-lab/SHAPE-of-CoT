import json
import os
from vllm import LLM, SamplingParams

class HFClient:
    """
    A minimal wrapper using vLLM to make local models compatible 
    with the OpenAI client interface, providing significantly 
    faster inference than standard transformers.
    """
    def __init__(self, model_id):
        print(f"Loading Model with vLLM: {model_id}...")
        
        # Initialize vLLM engine
        # trust_remote_code=True is required for many Qwen models
        # Qwen 3.5 9B fits in a single GPU (A100 40GB/80GB)
        self.llm = LLM(
            model=model_id, 
            trust_remote_code=True,
            gpu_memory_utilization=0.9,
            max_model_len=16384
        )
        self.tokenizer = self.llm.get_tokenizer()
        self.chat = self.Chat(self.llm, self.tokenizer)

    class Chat:
        def __init__(self, llm, tokenizer):
            self.completions = self.Completions(llm, tokenizer)

        class Completions:
            def __init__(self, llm, tokenizer):
                self.llm = llm
                self.tokenizer = tokenizer

            def create(self, model, messages, response_format=None, **kwargs):
                """
                Mimics openai.chat.completions.create using vLLM
                """
                # Apply chat template
                prompt = self.tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True
                )
                
                # Setup sampling parameters
                temp = kwargs.get("temperature", 1.0)
                sampling_params = SamplingParams(
                    temperature=temp,
                    max_tokens=kwargs.get("max_tokens", 4096),
                    top_p=kwargs.get("top_p", 0.95),
                    top_k=kwargs.get("top_k", 20),
                    min_p=kwargs.get("min_p", 0.0),
                    presence_penalty=kwargs.get("presence_penalty", 1.5),
                    repetition_penalty=kwargs.get("repetition_penalty", 1.0),
                    stop=kwargs.get("stop", None),
                )
                
                # Generate
                # Note: vLLM is optimized for batches, but for simple swap we handle one by one
                outputs = self.llm.generate([prompt], sampling_params, use_tqdm=False)
                content = outputs[0].outputs[0].text
                
                # Mock OpenAI Response structure for backward compatibility
                class Choice:
                    class Message:
                        def __init__(self, content):
                            self.content = content
                    def __init__(self, content):
                        self.message = self.Message(content)
                        self.finish_reason = "stop"
                
                class Response:
                    def __init__(self, content, model_name):
                        self.choices = [Choice(content)]
                        self.model = model_name
                
                return Response(content, model)
