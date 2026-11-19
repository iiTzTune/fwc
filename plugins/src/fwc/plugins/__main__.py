import argparse
import asyncio
from pathlib import Path

from .host import run


def main():
    parser = argparse.ArgumentParser(description="Run observation plugins")
    parser.add_argument("manifests", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    asyncio.run(run(args.manifests, args.state, args.port))


if __name__ == "__main__":
    main()
