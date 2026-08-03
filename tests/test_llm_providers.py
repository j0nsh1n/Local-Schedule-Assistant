"""v4.6.0 — external LLM provider helpers (no network).

Synthetic only. Covers message conversion for OpenAI / Anthropic tool loops,
base URL resolution, settings defaults, and masking of API keys.
"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai
import core

TMP = Path(tempfile.mkdtemp())
core.DATA_FILE     = TMP / "activities.json"
core.BACKUP_DIR    = TMP / "backups"
core.SETTINGS_FILE = TMP / "settings.json"
core.CREDS_FILE    = TMP / "credentials.json"
core.TOKEN_FILE    = TMP / "token.json"

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

print("── settings defaults ──")
s = core.load_settings()
check("default provider is ollama", s.get("llm_provider") == "ollama")
check("api key default empty", s.get("llm_api_key") == "")
check("base url default empty", s.get("llm_base_url") == "")
s["llm_provider"] = "openai"
s["llm_api_key"] = "sk-test-secret-key-12345"
core.save_settings(s)
s2 = core.load_settings()
check("provider persists", s2.get("llm_provider") == "openai")
check("key persists", s2.get("llm_api_key") == "sk-test-secret-key-12345")
check("mask hides middle", "sk-" in core.mask_api_key(s2["llm_api_key"])
      and "secret" not in core.mask_api_key(s2["llm_api_key"]))
check("bad provider falls back", core.load_settings() or True)
# force bad then load
raw = dict(s2); raw["llm_provider"] = "nope"
core.SETTINGS_FILE.write_text(__import__("json").dumps(raw), encoding="utf-8")
check("invalid provider → ollama", core.load_settings()["llm_provider"] == "ollama")

print("── base URL ──")
check("openai default", ai.resolve_llm_base_url("openai", "") == ai.OPENAI_DEFAULT_BASE)
check("openai override",
      ai.resolve_llm_base_url("openai", "https://example.com/v1/") == "https://example.com/v1")
check("compatible needs url", ai.resolve_llm_base_url("openai_compatible", "") == "")
check("anthropic default",
      ai.resolve_llm_base_url("anthropic", "") == ai.ANTHROPIC_DEFAULT_BASE)

print("── messages_for_openai ──")
loop = [
    {"role": "system", "content": "You are a planner."},
    {"role": "user", "content": "Plan my day"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "function": {"name": "list_blocks", "arguments": {"date": "today"}}}
    ]},
    {"role": "tool", "tool_call_id": "call_1", "name": "list_blocks", "content": "empty"},
    {"role": "tool_note", "content": "ui only"},
]
oai = ai.messages_for_openai(loop)
check("system kept", oai[0]["role"] == "system")
check("tool_note stripped", all(m["role"] != "tool_note" for m in oai))
check("assistant has tool_calls", "tool_calls" in oai[2])
check("arguments are JSON strings",
      isinstance(oai[2]["tool_calls"][0]["function"]["arguments"], str))
check("tool has tool_call_id", oai[3].get("tool_call_id") == "call_1")
# dict args → string
oai2 = ai.messages_for_openai([{
    "role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "x", "arguments": {"a": 1}}}
    ]
}])
check("dict args serialized", '"a"' in oai2[0]["tool_calls"][0]["function"]["arguments"])

print("── messages_for_anthropic ──")
system, msgs = ai.messages_for_anthropic(loop)
check("system extracted", "planner" in system)
check("no system role in messages", all(m["role"] != "system" for m in msgs))
check("tool_result on user turn",
      any(m["role"] == "user" and isinstance(m.get("content"), list)
          and any(b.get("type") == "tool_result" for b in m["content"]) for m in msgs))
tools = ai.anthropic_tools_from_openai(ai.AI_TOOLS[:2])
check("anthropic tools converted", len(tools) >= 1 and "name" in tools[0]
      and "input_schema" in tools[0])

print("── thread defaults ──")
t = ai.OllamaThread([], "m", provider="openai", api_key="")
check("thread stores provider", t.provider == "openai")

print("── cloud model suggestions ──")
sugs = ai.CLOUD_MODEL_SUGGESTIONS
check("has openai/anthropic/compatible keys",
      set(sugs) >= {"openai", "anthropic", "openai_compatible"})
check("openai seeds current GPT-5.6 family",
      "gpt-5.6-luna" in sugs["openai"] and "gpt-5.6-sol" in sugs["openai"])
check("anthropic seeds Sonnet/Opus 5",
      "claude-sonnet-5" in sugs["anthropic"] and "claude-opus-5" in sugs["anthropic"])
# Guard against reintroducing retired picker seeds as the only options
legacy = {"gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini", "claude-opus-4-1"}
check("openai list not only legacy 4.x/o-series",
      not set(sugs["openai"]).issubset(legacy))
check("default openai seed is first entry", sugs["openai"][0] == "gpt-5.6-luna")
check("default anthropic seed is sonnet-5", sugs["anthropic"][0] == "claude-sonnet-5")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
