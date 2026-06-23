from __future__ import annotations

from src.config import DATA_PATH, OUTPUT_PATH
from src.pipeline import run_pipeline


def main() -> None:
    result_df = run_pipeline(DATA_PATH, OUTPUT_PATH)
    print(f"Saved purchase decision summary: {OUTPUT_PATH}")
    print(
        result_df[
            ["item_name", "primary_action", "follow_up_action", "total_risk_score"]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
