"""Module entrypoint for `python -m autoware_system_designer.linter`.

Delegates to the linter CLI implementation.
"""

from .run_lint import main


if __name__ == "__main__":
    main()
