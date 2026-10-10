import json
import os
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from scoring.load import REPO

DEFAULT_BASE_URL = "https://factchat-cloud.mindlogic.ai/v1/gateway"
MODEL = "gemini-3.8-flash"


def configured_base_url():
    base_url = os.environ.get("BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment:
        raise RuntimeError("BASE_URL must be an http(s) API base URL without query or fragment")
    return base_url


def request_json(base_url, path, api_key, payload):
    request = Request(base_url + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
                      headers={"Authorization": "Bearer %s" % api_key, "Content-Type": "application/json",
                               "User-Agent": "hyena-claim-judge/1.0"})
    try:
        with urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError("Gateway HTTP %s" % error.code) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError("Gateway request failed: %s" % error) from error


def ask_json(system, user, schema):
    load_dotenv(REPO / ".env")
    api_key = os.environ.get("API_KEY", "")
    if not api_key:
        raise RuntimeError("API_KEY is missing (.env)")
    base_url = configured_base_url()
    payload = {"model": MODEL, "temperature": 0, "reasoning_effort": "low", "max_tokens": 16000,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "response_format": {"type": "json_schema", "json_schema": schema}}
    response = request_json(base_url, "/chat/completions/", api_key, payload)
    try:
        return json.loads(response["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Gateway did not return valid JSON") from error
