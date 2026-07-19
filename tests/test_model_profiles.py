"""v3.7.0 — curated model profiles + 'when to use' guidance helpers.
Pure (no Qt) for the matchers; light offscreen for show_model_guide wiring is
skipped — Settings/AIPanel just call the helpers tested here."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

# ── Registry integrity ───────────────────────────────────────────────────────
check("RECOMMENDED_MODELS == MODEL_PROFILES keys",
      ai.RECOMMENDED_MODELS == list(ai.MODEL_PROFILES.keys()))
check("at least 7 curated models", len(ai.RECOMMENDED_MODELS) >= 7)
check("qwen3:14b is first (primary recommend)",
      ai.RECOMMENDED_MODELS[0] == "qwen3:14b")
for tag, p in ai.MODEL_PROFILES.items():
    check(f"{tag} has badge", bool(p.get("badge")))
    check(f"{tag} has when blurb", len(p.get("when", "")) > 20)
    check(f"{tag} has vram", bool(p.get("vram")))
    check(f"{tag} has disk", bool(p.get("disk")))

# ── model_profile matching ───────────────────────────────────────────────────
check("exact tag", ai.model_profile("qwen3:14b") is ai.MODEL_PROFILES["qwen3:14b"])
check("case insensitive",
      ai.model_profile("QWEN3:14B") is ai.MODEL_PROFILES["qwen3:14b"])
check("quant suffix",
      ai.model_profile("qwen3:14b-q4_K_M") is ai.MODEL_PROFILES["qwen3:14b"])
check("gemma4 family matches :latest",
      ai.model_profile("gemma4:latest") is ai.MODEL_PROFILES["gemma4"])
check("gemma4 family matches :e4b",
      ai.model_profile("gemma4:e4b") is ai.MODEL_PROFILES["gemma4"])
check("mistral exact",
      ai.model_profile("mistral-small3.1:24b") is ai.MODEL_PROFILES["mistral-small3.1:24b"])
check("deepseek family+size",
      ai.model_profile("deepseek-r1:14b") is ai.MODEL_PROFILES["deepseek-r1:14b"])
check("glm flash",
      ai.model_profile("glm-4.7-flash") is ai.MODEL_PROFILES["glm-4.7-flash"])
check("unknown → None", ai.model_profile("llama3.1:8b") is None)
check("empty → None", ai.model_profile("") is None)
check("None-ish → None", ai.model_profile(None) is None)

# qwen2.5 must not steal qwen3 matches (longer / more specific keys first via
# exact path; family names differ so no collision)
check("qwen2.5 does not match qwen3",
      ai.model_profile("qwen2.5:14b") is ai.MODEL_PROFILES["qwen2.5:14b"])
check("qwen3 does not match qwen2.5",
      ai.model_profile("qwen3:14b") is not ai.MODEL_PROFILES["qwen2.5:14b"])

# ── User-facing text ─────────────────────────────────────────────────────────
w = ai.model_when_text("qwen3:14b")
check("when_text includes badge", "Best everyday" in w or "★" in w)
check("when_text includes VRAM", "VRAM" in w)
check("when_text includes blurb body", "daily driver" in w.lower() or "tool" in w.lower())

custom = ai.model_when_text("totally-made-up:7b")
check("custom model gets fallback blurb", "Custom" in custom or "unlisted" in custom.lower())
check("custom mentions tool-calling", "tool" in custom.lower())

guide = ai.model_guide_text()
check("guide mentions qwen3", "qwen3:14b" in guide)
check("guide mentions mistral", "mistral-small3.1:24b" in guide)
check("guide mentions pull", "ollama pull" in guide)
check("guide mentions VRAM picker", "12–16" in guide or "12-16" in guide)
for tag in ai.RECOMMENDED_MODELS:
    check(f"guide lists {tag}", tag in guide)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
