"""Allow ``python -m sade`` to run the CLI scenario driver (same as ``python -m sade.main``)."""

from sade.main import main as cli_main

if __name__ == "__main__":
    import asyncio
    import sys

    try:
        asyncio.run(cli_main())
    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl+C). Exiting.")
        sys.exit(0)
