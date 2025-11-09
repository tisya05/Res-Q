#!/usr/bin/env python3
"""simulate_emergencies.py

Small CLI/test harness to simulate multiple emergency scenarios and print
the full Gemini prompt produced by `context_manager.build_prompt()` as well as
the assistant fallback (when Gemini client is unavailable).

Usage:
    python simulate_emergencies.py         # run all scenarios
    python simulate_emergencies.py --scenario flood
"""
import argparse
import textwrap
from context_manager import build_prompt, gemini_reply_with_context, clear_session, memory, update_memory


SCENARIOS = {
    "flood": {
        "text": "I'm outside and water is rising near the river. I don't know where to go.",
        "coords": (42.3736, -72.5199),
        "emergency_type": "flood",
    },
    "earthquake": {
        "text": "The ground is shaking and buildings are swaying. I'm outside and scared.",
        "coords": (34.0522, -118.2437),
        "emergency_type": "earthquake",
    },
    "fire": {
        "text": "There's a fire in my apartment building and smoke is coming in.",
        "coords": (40.7128, -74.0060),
        "emergency_type": "fire",
    },
    "medical": {
        "text": "Someone here collapsed and isn't breathing. I think it's serious.",
        "coords": (37.7749, -122.4194),
        "emergency_type": "medical",
    },
    "confused": {
        "text": "I don't know where I am, can you tell me where to go?",
        "coords": None,
        "emergency_type": None,
    }
}


def run_one(name: str):
    clear_session()
    s = SCENARIOS.get(name)
    if not s:
        print(f"Unknown scenario: {name}")
        return

    print("\n" + "=" * 80)
    print(f"Scenario: {name}\n")

    # Inject the user's utterance so update_memory can pick up keywords
    update_memory(s["text"])  # this prints stored memory items

    # override emergency_type and coords for deterministic output
    if s.get("emergency_type"):
        memory["emergency_type"] = s["emergency_type"]
    if s.get("coords"):
        memory["approx_coords"] = s["coords"]

    # Show the prompt that will be sent to Gemini
    prompt = build_prompt()
    print("--- Generated Gemini prompt (truncated to 8000 chars) ---\n")
    print(textwrap.indent(prompt[:8000], "    "))

    # Also show the fallback assistant response when Gemini client is not present
    print("\n--- Assistant reply (gemini_reply_with_context) ---\n")
    reply = gemini_reply_with_context()
    print(textwrap.indent(reply[:4000], "    "))


def main():
    parser = argparse.ArgumentParser(description="Simulate emergency scenarios and print Gemini prompts.")
    parser.add_argument("--scenario", "-s", help="Scenario name to run (default: all)", choices=list(SCENARIOS.keys()), default=None)
    args = parser.parse_args()

    if args.scenario:
        run_one(args.scenario)
    else:
        for name in SCENARIOS:
            run_one(name)


if __name__ == "__main__":
    main()
