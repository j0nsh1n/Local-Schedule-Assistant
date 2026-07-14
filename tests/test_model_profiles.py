"""v3.7.0 — curated model profiles + 'when to use' guidance helpers.
Pure (no Qt) for the matchers; light offscreen for show_model_guide wiring is
skipped — Settings/AIPanel just call the helpers tested here."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app

results = []
def check(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

# ── Registry integrity ───────────────────────────────────────────────────────
check("RECOMMENDED_MODELS == MODEL_PROFILES keys",
      app.RECOMMENDED_MODELS == list(app.MODEL_PROFILES.keys()))
check("at least 7 curated models", len(app.RECOMMENDED_MODELS) >= 7)
check("qwen3:14b is first (primary recommend)",
      app.RECOMMENDED_MODELS[0] == "qwen3:14b")
for tag, p in app.MODEL_PROFILES.items():
    check(f"{tag} has badge", bool(p.get("badge")))
    check(f"{tag} has when blurb", len(p.get("when", "")) > 20)
    check(f"{tag} has vram", bool(p.get("vram")))
    check(f"{tag} has disk", bool(p.get("disk")))

# ── model_profile matching ───────────────────────────────────────────────────
check("exact tag", app.model_profile("qwen3:14b") is app.MODEL_PROFILES["qwen3:14b"])
check("case insensitive",
      app.model_profile("QWEN3:14B") is app.MODEL_PROFILES["qwen3:14b"])
check("quant suffix",
      app.model_profile("qwen3:14b-q4_K_M") is app.MODEL_PROFILES["qwen3:14b"])
check("gemma4 family matches :latest",
      app.model_profile("gemma4:latest") is app.MODEL_PROFILES["gemma4"])
check("gemma4 family matches :e4b",
      app.model_profile("gemma4:e4b") is app.MODEL_PROFILES["gemma4"])
check("mistral exact",
      app.model_profile("mistral-small3.1:24b") is app.MODEL_PROFILES["mistral-small3.1:24b"])
check("deepseek family+size",
      app.model_profile("deepseek-r1:14b") is app.MODEL_PROFILES["deepseek-r1:14b"])
check("glm flash",
      app.model_profile("glm-4.7-flash") is app.MODEL_PROFILES["glm-4.7-flash"])
check("unknown → None", app.model_profile("llama3.1:8b") is None)
check("empty → None", app.model_profile("") is None)
check("None-ish → None", app.model_profile(None) is None)

# qwen2.5 must not steal qwen3 matches (longer / more specific keys first via
# exact path; family names differ so no collision)
check("qwen2.5 does not match qwen3",
      app.model_profile("qwen2.5:14b") is app.MODEL_PROFILES["qwen2.5:14b"])
check("qwen3 does not match qwen2.5",
      app.model_profile("qwen3:14b") is not app.MODEL_PROFILES["qwen2.5:14b"])

# ── User-facing text ─────────────────────────────────────────────────────────
w = app.model_when_text("qwen3:14b")
check("when_text includes badge", "Best everyday" in w or "★" in w)
check("when_text includes VRAM", "VRAM" in w)
check("when_text includes blurb body", "daily driver" in w.lower() or "tool" in w.lower())

custom = app.model_when_text("totally-made-up:7b")
check("custom model gets fallback blurb", "Custom" in custom or "unlisted" in custom.lower())
check("custom mentions tool-calling", "tool" in custom.lower())

guide = app.model_guide_text()
check("guide mentions qwen3", "qwen3:14b" in guide)
check("guide mentions mistral", "mistral-small3.1:24b" in guide)
check("guide mentions pull", "ollama pull" in guide)
check("guide mentions VRAM picker", "12–16" in guide or "12-16" in guide)
for tag in app.RECOMMENDED_MODELS:
    check(f"guide lists {tag}", tag in guide)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
