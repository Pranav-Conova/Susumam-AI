"""
ai_chat.py — NVIDIA API integration for codebase-aware AI chat.
Returns responses as unified diffs.
"""

import os
import json
import requests
from dotenv import load_dotenv

import db

load_dotenv()

INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "qwen/qwen3.5-397b-a17b"

_SYSTEM_PROMPT_TEMPLATE = """You are an expert software engineer assistant.
You are working with a specific codebase provided below.

IMPORTANT RULES:
1. When asked to make code changes, ALWAYS respond with a unified diff in the standard git format.
2. Your diff MUST follow this format exactly:
   --- a/<relative/file/path>
   +++ b/<relative/file/path>
   @@ -<old_start>,<old_count> +<new_start>,<new_count> @@
   <context lines starting with space>
   <removed lines starting with ->
   <added lines starting with +>
3. Wrap the diff in a ```diff ... ``` code block.
4. You may include a brief explanation BEFORE the diff block, but NO explanation after.
5. If no code change is needed (e.g., a pure question), answer normally without a diff.
6. Always provide context lines (3 lines before and after changes) for precision.
7. Keep diffs minimal — only change what is necessary.

--- CODEBASE CONTEXT ---
{context}
--- END CONTEXT ---
"""


def _get_api_key() -> str | None:
    return os.environ.get("NVIDIA_API_KEY")


def _build_system_prompt(codebase_id: int) -> str:
    context = db.get_context(codebase_id) or "(No context available)"
    return _SYSTEM_PROMPT_TEMPLATE.format(context=context)


def check_api_key() -> bool:
    return bool(_get_api_key())


def chat_with_ai(
    codebase_id: int,
    user_message: str,
    on_chunk=None,
) -> str:
    """
    Send a message to NVIDIA API, streaming the response.
    """
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "No NVIDIA API key found. Set NVIDIA_API_KEY in your .env file or environment."
        )

    # Build history for multi-turn context
    history = db.get_chat_history(codebase_id)
    messages = [{"role": "system", "content": _build_system_prompt(codebase_id)}]
    
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    
    messages.append({"role": "user", "content": user_message})

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream"
    }

    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 16384,
        "temperature": 0.60,
        "top_p": 0.95,
        "top_k": 20,
        "presence_penalty": 0,
        "repetition_penalty": 1,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": True},
    }

    response = requests.post(INVOKE_URL, headers=headers, json=payload, stream=True)
    response.raise_for_status()

    response_text = ""
    for line in response.iter_lines():
        if line:
            decoded_line = line.decode("utf-8")
            if decoded_line.startswith("data: "):
                data_str = decoded_line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data["choices"][0]["delta"]
                    if "content" in delta:
                        chunk = delta["content"]
                        response_text += chunk
                        if on_chunk:
                            on_chunk(chunk)
                except json.JSONDecodeError:
                    continue

    # Persist messages to DB
    db.add_chat_message(codebase_id, "user", user_message)
    db.add_chat_message(codebase_id, "assistant", response_text)

    return response_text
