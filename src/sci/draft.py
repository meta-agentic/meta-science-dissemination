"""Italian post generation and markdown rendering.

The model is handed only the claims that passed verification, plus the
limitations it is required to disclose. It cannot reach the headline's
original wording for anything that failed the checks, which is what stops a
debunked number from being quietly reintroduced by a fluent sentence.

Every draft carries its evidence ledger in the frontmatter: the bound DOI, the
corroborating outlets, each claim with its verdict, and the gate decision. A
post is never separated from the reason it was allowed to exist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .llm import LLM, LLMUnavailable
from .store import Analysis, Item
from .textutil import slugify

_SYSTEM = (
    "Sei un giornalista scientifico italiano. Scrivi in italiano corretto e "
    "naturale. Non inventi mai dati. Usi solo le affermazioni verificate che "
    "ti vengono fornite. Se un dato non e' nell'elenco verificato, non lo "
    "scrivi, nemmeno se lo conosci."
)

_PROMPT = """Scrivi un post divulgativo in italiano su questa notizia scientifica.

TITOLO ORIGINALE (inglese): {title}
OCCHIELLO ORIGINALE: {summary}

AFFERMAZIONI VERIFICATE — queste, e solo queste, puoi affermare:
{verified}

AFFERMAZIONI NON CONFERMATE — non usarle, non parafrasarle, non alludervi:
{rejected}

LIMITI DA DICHIARARE ESPLICITAMENTE NEL POST:
{limitations}

FONTE PRIMARIA: {primary}
CONFERME INDIPENDENTI: {corroboration}

REGISTRO RICHIESTO:
{register}

Struttura richiesta:
1. Un titolo italiano su una riga, che non prometta piu' di quanto le
   affermazioni verificate sostengano.
2. Due o tre paragrafi di corpo (massimo {max_words} parole in totale).
3. Un paragrafo finale intitolato "Cosa non sappiamo ancora" che riporta i
   limiti elencati sopra in linguaggio piano.

Non aggiungere metadati, non aggiungere fonti, non usare markdown per il
titolo. Restituisci solo il testo del post."""


def _format_claims(claims: list[dict[str, Any]], status: str) -> str:
    selected = [c for c in claims if c.get("status") == status and c.get("text")]
    if not selected:
        return "(nessuna)"
    return "\n".join(f"- {c['text']}" for c in selected)


def _format_limitations(analysis: Analysis) -> str:
    flags = analysis.hype.get("flags", [])
    hedged = [c for c in analysis.claims if c.get("status") == "hedged"]

    lines = [f"- {f['detail']}" for f in flags]
    lines += [
        f"- Questa affermazione e' solo un'associazione, non una causa: {c['text']}"
        for c in hedged if c.get("text")
    ]
    if analysis.binding.get("status") != "bound":
        lines.append(
            "- Non e' stato possibile identificare con certezza lo studio "
            "originale a cui la notizia si riferisce."
        )
    return "\n".join(lines) if lines else "- Nessun limite rilevato dai controlli automatici."


def _format_primary(analysis: Analysis) -> str:
    best = analysis.binding.get("best") or {}
    if not best:
        return "non identificata"
    doi = best.get("doi") or "senza DOI"
    return f"{best.get('title', '')} — {best.get('venue', 'sede ignota')} (DOI: {doi})"


def _format_corroboration(analysis: Analysis) -> str:
    independent = analysis.corroboration.get("independent", [])
    if not independent:
        return "nessuna testata indipendente ha ripreso la notizia"
    return ", ".join(f"{r['name']}" for r in independent)


def generate(item: Item, analysis: Analysis, llm: LLM, settings: Settings) -> str:
    """Produce the Italian post body. Raises LLMUnavailable if it cannot."""
    prompt = _PROMPT.format(
        title=item.title,
        summary=item.summary or "(nessuno)",
        verified=_format_claims(analysis.claims, "verified"),
        rejected=_format_claims(analysis.claims, "unsupported"),
        limitations=_format_limitations(analysis),
        primary=_format_primary(analysis),
        corroboration=_format_corroboration(analysis),
        register=str(settings.pipeline.get("draft", "register")).strip(),
        max_words=int(settings.pipeline.get("draft", "max_words")),
    )
    return llm.complete(prompt, system=_SYSTEM).strip()


def _yaml_escape(value: Any) -> str:
    text = str(value).replace('"', "'").replace("\n", " ")
    return f'"{text}"'


def render(item: Item, analysis: Analysis, body: str, settings: Settings) -> str:
    """Assemble the markdown file: frontmatter ledger plus the post."""
    best = analysis.binding.get("best") or {}
    independent = analysis.corroboration.get("independent", [])
    echo = analysis.corroboration.get("echo", [])

    lines = [
        "---",
        f"title: {_yaml_escape(item.title)}",
        f"source_title_en: {_yaml_escape(item.title)}",
        f"source_url: {_yaml_escape(item.link)}",
        f"source_outlet: {_yaml_escape(item.raw.get('source_name', item.source_id))}",
        f"published_at: {_yaml_escape(item.published_at or 'unknown')}",
        f"drafted_at: {_yaml_escape(datetime.now(timezone.utc).isoformat(timespec='seconds'))}",
        "lang: it",
        "status: draft",
        "evidence:",
        f"  binding_status: {_yaml_escape(analysis.binding.get('status'))}",
        f"  primary_doi: {_yaml_escape(best.get('doi') or 'none')}",
        f"  primary_title: {_yaml_escape(best.get('title') or 'none')}",
        f"  primary_venue: {_yaml_escape(best.get('venue') or 'none')}",
        f"  binding_score: {best.get('score', 0)}",
        f"  independent_corroborators: {len(independent)}",
        f"  press_release_echoes: {len(echo)}",
        f"  hype_score: {analysis.hype.get('score', 0)}",
        f"  hype_flags: [{', '.join(analysis.hype.get('flag_keys', []))}]",
        f"  claims_verified: {len(analysis.verified_claims)}",
        f"  claims_total: {len(analysis.claims)}",
        "  gate:",
        f"    passes: {str(analysis.passes).lower()}",
    ]
    for reason in analysis.gate.get("passed", []):
        lines.append(f"    - {_yaml_escape(reason)}")
    lines += ["---", "", body, "", "---", "", "## Registro delle verifiche", ""]

    lines.append("### Affermazioni")
    for claim in analysis.claims:
        if not claim.get("text"):
            continue
        mark = {"verified": "OK", "hedged": "CAUTELA", "unsupported": "NON CONFERMATA"}.get(
            str(claim.get("status")), "?"
        )
        lines.append(f"- **[{mark}]** {claim['text']}")
        lines.append(f"  - motivo: {claim.get('reason', '')}")

    if independent or echo:
        lines += ["", "### Altre testate"]
        for record in independent:
            lines.append(f"- indipendente — [{record['name']}]({record['link']}) "
                         f"(somiglianza {record['similarity']})")
        for record in echo:
            lines.append(f"- ripresa da comunicato — [{record['name']}]({record['link']}) "
                         f"(somiglianza {record['similarity']})")

    lines += ["", "### Fonte primaria"]
    if best:
        doi = best.get("doi")
        link = f"https://doi.org/{doi}" if doi else best.get("title", "")
        lines.append(f"- [{best.get('title', 'senza titolo')}]({link}) — "
                     f"{best.get('venue', '')}, punteggio {best.get('score')}")
    else:
        lines.append("- non identificata con sufficiente confidenza")

    lines += ["", f"[Articolo originale]({item.link})", ""]
    return "\n".join(lines)


def write(item: Item, analysis: Analysis, body: str, settings: Settings) -> Path:
    """Write the rendered draft under drafts/YYYY-MM-DD/."""
    day = (item.published_at or datetime.now(timezone.utc).isoformat())[:10]
    directory = settings.drafts_dir / day
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{slugify(item.title)}.md"
    path.write_text(render(item, analysis, body, settings), encoding="utf-8")
    return path


def write_review(item: Item, analysis: Analysis, settings: Settings) -> Path:
    """Record a blocked item so the editorial gap is visible, not invisible."""
    day = (item.published_at or datetime.now(timezone.utc).isoformat())[:10]
    directory = settings.drafts_dir / day / "_review"
    directory.mkdir(parents=True, exist_ok=True)

    lines = [
        "---",
        f"title: {_yaml_escape(item.title)}",
        f"source_url: {_yaml_escape(item.link)}",
        "status: blocked",
        f"binding_status: {_yaml_escape(analysis.binding.get('status'))}",
        f"hype_score: {analysis.hype.get('score', 0)}",
        "---",
        "",
        f"# {item.title}",
        "",
        f"{item.summary}",
        "",
        "## Perche' non e' stato prodotto un post",
        "",
    ]
    lines += [f"- {reason}" for reason in analysis.gate.get("blockers", [])]
    lines += ["", "## Limiti rilevati", ""]
    lines += [f"- {f['detail']}" for f in analysis.hype.get("flags", [])] or ["- nessuno"]
    lines += ["", f"[Articolo originale]({item.link})", ""]

    path = directory / f"{slugify(item.title)}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
