"""Shared wikilink syntax and target identity for note validators."""

import re


WIKILINK_RE = re.compile(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]")


def wikilink_target(match: re.Match[str]) -> str:
    """Remove table alias escaping without changing the note/heading identity."""
    target = match.group(1)
    trailing_slashes = len(target) - len(target.rstrip("\\"))
    if match.group(2) is not None and trailing_slashes % 2:
        target = target[:-1]
    return target
