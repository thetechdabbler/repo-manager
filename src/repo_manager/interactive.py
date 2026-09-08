"""Small terminal helpers for interactive CLI flows."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TextIO

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - only used on non-POSIX systems.
    termios = None
    tty = None


class InteractiveTerminalError(RuntimeError):
    """The terminal cannot support an interactive radio selector."""


@dataclass(frozen=True)
class RadioOption:
    value: str
    label: str


def radio_select(
    title: str,
    options: list[RadioOption],
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> str:
    """Select one option with Up/Down and Enter in a POSIX terminal."""
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    if not options:
        raise ValueError("radio selector requires at least one option")
    if not input_stream.isatty() or not output_stream.isatty():
        raise InteractiveTerminalError("interactive mode requires a terminal")

    if termios is None or tty is None:
        raise InteractiveTerminalError("interactive mode requires a POSIX terminal")

    try:
        fd = input_stream.fileno()
        original = termios.tcgetattr(fd)
    except (AttributeError, OSError, termios.error) as exc:
        raise InteractiveTerminalError("interactive mode requires a POSIX terminal") from exc

    selected = 0
    rendered_lines = 0
    try:
        tty.setcbreak(fd)
        while True:
            rendered_lines = _render(
                output_stream, title, options, selected, rendered_lines
            )
            key = _read_key(input_stream)
            if key in {"up", "k"}:
                selected = (selected - 1) % len(options)
            elif key in {"down", "j"}:
                selected = (selected + 1) % len(options)
            elif key in {"enter", "space"}:
                output_stream.write("\n")
                output_stream.flush()
                return options[selected].value
            elif key == "cancel":
                raise KeyboardInterrupt
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def _read_key(input_stream: TextIO) -> str:
    key = input_stream.read(1)
    if key in {"\r", "\n"}:
        return "enter"
    if key == " ":
        return "space"
    if key == "\x03":
        return "cancel"
    if key in {"j", "k"}:
        return key
    if key == "\x1b":
        second = input_stream.read(1)
        if second != "[":
            return ""
        third = input_stream.read(1)
        return {"A": "up", "B": "down"}.get(third, "")
    return ""


def _render(
    output_stream: TextIO,
    title: str,
    options: list[RadioOption],
    selected: int,
    rendered_lines: int,
) -> int:
    if rendered_lines:
        output_stream.write(f"\033[{rendered_lines}A")
    lines = [title, "Use ↑/↓ and Enter to choose:"]
    lines.extend(
        f"  {'●' if i == selected else '○'} {option.label}"
        for i, option in enumerate(options)
    )
    for line in lines:
        output_stream.write("\033[2K\r" + line + "\n")
    output_stream.flush()
    return len(lines)
