import json
import logging
import re

import openai
from openai import OpenAI
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ValidationError

from app.ai import AIError
from app.config import settings

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _create_completion(model: str, **kwargs) -> ChatCompletion:
    client = OpenAI(
        base_url=settings.omniroute_base_url,
        api_key=settings.omniroute_api_key or "not-needed",
        max_retries=0,
        timeout=settings.ai_timeout_seconds,
    )
    return client.chat.completions.create(model=model, **kwargs)


def _arguments(completion: ChatCompletion) -> dict:
    """Tool-call arguments, else a JSON object in the content. Raises ValueError if neither."""
    if not completion.choices:
        raise ValueError("no choices returned")
    message = completion.choices[0].message
    if message.tool_calls:
        raw = message.tool_calls[0].function.arguments
    elif message.content:
        raw = _FENCE.sub("", message.content.strip())
    else:
        raise ValueError("no tool call or content")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON is not an object")
    return data


def call_structured[T: BaseModel](
    system: str, user: str, tool_name: str, schema: type[T]
) -> tuple[T, str, dict]:
    """Force a tool call on the first model that returns schema-valid output."""
    tool = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": f"Return the {tool_name} result.",
            "parameters": schema.model_json_schema(),
        },
    }
    attempts: list[tuple[str, str]] = []
    for model in [settings.ai_model, *settings.ai_fallback_models]:
        try:
            completion = _create_completion(
                model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": tool_name}},
                temperature=0.2,
            )
            args = _arguments(completion)
            return schema.model_validate(args), model, args
        except openai.APIError as e:
            reason = f"provider error: {type(e).__name__}"
        except json.JSONDecodeError:
            reason = "invalid JSON"
        except ValidationError as e:
            reason = "schema violation: " + ", ".join(
                sorted({".".join(map(str, err["loc"])) or "root" for err in e.errors()})
            )
        except ValueError as e:
            reason = str(e)
        log.warning("AI model %s failed: %s", model, reason)
        attempts.append((model, reason))
    raise AIError(attempts)
