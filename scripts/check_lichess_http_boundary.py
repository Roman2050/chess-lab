"""Fail when production code bypasses the supported Lichess HTTP client."""

import ast
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPOSITORY_ROOT / "app"
ALLOWED_CLIENT = APP_ROOT / "services" / "lichess.py"
LICHESS_URL_LITERAL = "https://lichess.org"
LICHESS_EXPORT_ROUTE_LITERAL = "/api/games/user"


def _forbidden_literal(value: str) -> bool:
    normalized = value.casefold()
    return (
        LICHESS_URL_LITERAL in normalized
        or LICHESS_EXPORT_ROUTE_LITERAL in normalized
    )


def _scan_file(source_path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
    except (OSError, SyntaxError) as exc:
        return [(getattr(exc, "lineno", 0) or 0, "could not parse source")]

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if _forbidden_literal(node.value):
            violations.append((node.lineno, "direct Lichess HTTP literal"))
    return violations


def main() -> int:
    """Check that only the supported service owns Lichess HTTP literals."""
    violations: list[str] = []
    for source_path in sorted(APP_ROOT.rglob("*.py")):
        if source_path.resolve() == ALLOWED_CLIENT.resolve():
            continue
        for line_number, reason in _scan_file(source_path):
            relative_path = source_path.relative_to(REPOSITORY_ROOT)
            violations.append(f"{relative_path}:{line_number}: {reason}")

    if violations:
        print("Lichess HTTP boundary violations detected:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        print(
            "Outbound Lichess exports must go through "
            "app/services/lichess.py.",
            file=sys.stderr,
        )
        return 1

    print(
        "Lichess HTTP boundary check passed: outbound export literals are "
        "owned by app/services/lichess.py."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
