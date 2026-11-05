import os
from saphirapy.github_actions_contracts import build_summary


if __name__ == "__main__":
    raw_directories = os.environ.get("RESULTS_DIRECTORIES", "")
    print(build_summary(raw_directories))
