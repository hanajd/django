#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main() -> None:
    base = Path(__file__).resolve().parent
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
