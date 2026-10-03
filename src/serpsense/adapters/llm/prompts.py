"""Versioned prompt files and how variables are rendered into them (AGENTS §7, ADR-0008).

A prompt lives in `prompts/<task>/v<N>.md`: the system prompt, a line `=== user ===`, then the
user message. `{{name}}` placeholders are filled once, and what fills them is never scanned for
more placeholders. Every value is untrusted data: it is NFKC-normalised (so a fullwidth
closing tag becomes the ASCII it imitates) and stripped of invisible format characters (bidi
controls, tag characters), then XML-escaped; a list of records is rendered as tags named after
the variable (`mentions` → `<mention id="m1">…</mention>`), so a mention can't close its own
delimiter or smuggle in instructions that look like the prompt's.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from serpsense.ports.llm_client import PromptUnavailable, Variables

PROMPTS = Path(__file__).parent / "prompts"
SEPARATOR = "\n=== user ===\n"
PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
VERSION = re.compile(r"([a-z][a-z_]{0,47})/(v[1-9][0-9]{0,3})")
ATTRIBUTE = re.compile(r"[a-z_]{1,32}")


class PromptError(PromptUnavailable):
    """A prompt version with no file, or a placeholder with no variable."""


@dataclass(frozen=True)
class RenderedPrompt:
    system: str  # stable across calls of a version, so it can be cached
    user: str


class PromptLibrary:
    def __init__(self, root: Path = PROMPTS) -> None:
        self._root = root

    def render(self, prompt_version: str, variables: Variables) -> RenderedPrompt:
        match = VERSION.fullmatch(prompt_version)
        path = self._root / match[1] / f"{match[2]}.md" if match else None
        if path is None or not path.is_file():
            raise PromptError(f"no prompt file for {prompt_version}")
        system, separator, user = path.read_text(encoding="utf-8").partition(SEPARATOR)
        if not separator:
            raise PromptError(f"{prompt_version} has no '=== user ===' line")
        return RenderedPrompt(_fill(system.strip(), variables), _fill(user.strip(), variables))


def _fill(template: str, variables: Variables) -> str:
    def value(match: re.Match[str]) -> str:
        name = match[1]
        if name not in variables:
            raise PromptError(f"no variable for {{{{{name}}}}}")
        given = variables[name]
        return _text(given) if isinstance(given, str) else _records(name, given)

    return PLACEHOLDER.sub(value, template)


def _records(name: str, records: Sequence[Mapping[str, str]]) -> str:
    tag = name.removesuffix("s")
    lines = []
    for record in records:
        if not all(ATTRIBUTE.fullmatch(key) for key in record):
            raise PromptError("record keys are lowercase names")
        attributes = "".join(
            f" {key}={quoteattr(_clean(value))}" for key, value in record.items() if key != "text"
        )
        lines.append(f"<{tag}{attributes}>{_text(record.get('text', ''))}</{tag}>")
    return "\n".join(lines)


def _clean(value: str) -> str:
    normal = unicodedata.normalize("NFKC", value)
    return "".join(char for char in normal if unicodedata.category(char) != "Cf")


def _text(value: str) -> str:
    return escape(_clean(value))
