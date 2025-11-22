from pathlib import Path

from smogon_strategies import TierStrategiesJsonSaver, refetch_empty_entries_in_tier_json


def main():
    tier = "OU"
    saver = TierStrategiesJsonSaver(tier)

    data = saver.get_strategies()
    print(f"Fetched strategies for {len(data)} Pokémon in tier {tier}.")

    out_path = saver.save()
    print(f"Saved strategies for tier {tier} to {out_path}.")

    tier_json_path = Path("tier_strategies") / f"{tier}.json"
    refetch_empty_entries_in_tier_json(tier_json_path)
    print(f"Re-fetched structured strategies for Pokémon with empty entries in {tier_json_path}.")


if __name__ == "__main__":
    main()
