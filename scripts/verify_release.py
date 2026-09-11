"""Read-only integrity check for a source release, independent of its location."""
import hashlib
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    entries = (root / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    failures = []
    for entry in entries:
        expected, name = entry.split("  ", 1)
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failures.append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            failures.append(name)
    if failures:
        print("Checksum failures:", ", ".join(failures))
        return 1
    print(f"Verified {len(entries)} release files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
