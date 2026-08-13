"""sci — science news to verified Italian drafts.

Pipeline stages, in order:

    fetch        publisher RSS only, no authenticated scraping
    bind         infer and score the underlying paper (OpenAlex, Crossref)
    corroborate  other outlets, weighted by editorial independence
    claims       model proposes, deterministic code verifies
    hype         rule-based limitation and overreach detection
    gate         publishability decision, with every blocker recorded
    draft        Italian post, restricted to verified claims

The invariant the whole design serves: a model never decides that its own
output is true.
"""

from .config import Settings, load, version

__all__ = ["Settings", "load", "version"]
