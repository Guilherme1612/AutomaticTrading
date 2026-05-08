"""PMACS CLI entry point."""

import sys


def main() -> None:
    """PMACS command-line interface."""
    if len(sys.argv) < 2:
        print("Usage: pmacs <command>")
        print("Commands: init, start, stop, status, version")
        sys.exit(1)

    command = sys.argv[1]
    if command == "version":
        from pmacs import __version__
        print(f"pmacs {__version__}")
    elif command == "init":
        print("Initialization not yet implemented (Phase 8)")
    elif command == "start":
        print("Start not yet implemented (Phase 4)")
    elif command == "stop":
        print("Stop not yet implemented (Phase 4)")
    elif command == "status":
        print("Status not yet implemented (Phase 4)")
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
