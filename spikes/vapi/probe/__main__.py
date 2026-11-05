"""Entry point: python3 -m probe."""
import sys

from probe.cli import main

sys.exit(main(sys.argv[1:]))
