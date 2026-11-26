import json
from pathlib import Path
from typing import Dict, Any, Optional

from .preprocessor import preprocess_scenario_folder
from .synthesizer import generate_with_llm
from .validator import validate_and_enforce_guardrails


def run_context_summary_pipeline(
    scenario_dir: str | Path,
    output_file: Optional[str | Path] = None
) -> Dict[str, Any]:
    """
    3-Layer Hybrid Pipeline:
    Layer 1: Deterministic Python Preprocessor (metadata, timestamps, skew, EVID assignment)
    Layer 2: LLM Synthesizer (summaries, observations, gaps, readiness)
    Layer 3: Python Safety & Guardrails (schema validation, constraints injection, ID verification)
    """
    scenario_path = Path(scenario_dir)

    # Layer 1
    preprocessed = preprocess_scenario_folder(scenario_path)

    # Layer 2
    llm_output = generate_with_llm(preprocessed)

    # Layer 3
    final_summary = validate_and_enforce_guardrails(preprocessed, llm_output)

    # Save to file if specified
    if output_file:
        out_p = Path(output_file)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(final_summary, f, indent=4)

    return final_summary


if __name__ == "__main__":
    import sys
    default_dir = Path(__file__).resolve().parents[3] / "data" / "scenarios" / "scenario_01"
    target_dir = sys.argv[1] if len(sys.argv) > 1 else default_dir
    output_target = sys.argv[2] if len(sys.argv) > 2 else "incident_summary.json"

    print(f"[+] Running 3-Layer Hybrid Pipeline on: {target_dir}")
    summary = run_context_summary_pipeline(target_dir, output_target)
    print(f"[+] Success! Incident Summary generated and saved to: {output_target}")
    print(json.dumps(summary, indent=2))
