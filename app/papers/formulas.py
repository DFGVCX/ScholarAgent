from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_INVALID_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_EQUATION_LABEL_RE = re.compile(
    r"(?<![A-Za-z0-9_])\((?P<label>\d{1,3})\)\s*[,.;:]?\s*$"
)
_GREEK_LATEX = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\epsilon",
    "ζ": r"\zeta",
    "η": r"\eta",
    "θ": r"\theta",
    "κ": r"\kappa",
    "λ": r"\lambda",
    "μ": r"\mu",
    "ξ": r"\xi",
    "π": r"\pi",
    "ρ": r"\rho",
    "σ": r"\sigma",
    "τ": r"\tau",
    "φ": r"\phi",
    "ω": r"\omega",
}
_UNICODE_LATEX = {
    "∑": r"\sum",
    "∏": r"\prod",
    "∼": r"\sim",
    "≈": r"\approx",
    "≤": r"\le",
    "≥": r"\ge",
    "∇": r"\nabla",
    "⊙": r"\odot",
    "·": r"\cdot",
    "∗": r"\times",
    "×": r"\times",
    "→": r"\to",
    "←": r"\leftarrow",
    "↔": r"\leftrightarrow",
    "∞": r"\infty",
    "−": "-",
}


def contains_invalid_controls(value: str) -> bool:
    return bool(_INVALID_CONTROL_RE.search(value or ""))


def sanitize_formula_text(value: str) -> str:
    """Remove database/browser-invalid controls without hiding word boundaries."""
    return re.sub(r"[ \t]+", " ", _INVALID_CONTROL_RE.sub(" ", value or "")).strip()


@dataclass(frozen=True)
class FormulaCandidate:
    label: str
    page_number: int
    bbox: tuple[float, float, float, float]
    raw_text: str
    latex: str
    markdown: str
    recovery_source: str
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "page_number": self.page_number,
            "bbox": list(self.bbox),
            "raw_text": self.raw_text,
            "latex": self.latex,
            "markdown": self.markdown,
            "recovery_source": self.recovery_source,
            "confidence": self.confidence,
        }


def _looks_like_formula_line(line: str) -> bool:
    clean = line.strip()
    if not clean or len(clean) > 120:
        return False
    if re.search(r"[=∑∏∼≈≤≥<>]|\(\d{1,3}\)\s*$", clean):
        return True
    tokens = clean.split()
    if len(tokens) > 4:
        return False
    return bool(tokens) and all(
        re.fullmatch(r"[A-Za-z0-9α-ωΑ-Ω]+", token) for token in tokens
    )


def extract_numbered_formula(page_text: str, label: str, *, occurrence: int = 0) -> str:
    """Return one compact pypdf line group ending in the requested label."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in (page_text or "").splitlines()]
    marker = re.compile(
        rf"(?<![A-Za-z0-9_])\({re.escape(str(label))}\)\s*[,.;:]?\s*$"
    )
    requested = max(0, int(occurrence))
    matched = 0
    for end, line in enumerate(lines):
        if not marker.search(line):
            continue
        if matched != requested:
            matched += 1
            continue
        start = end
        for index in range(end - 1, max(-1, end - 9), -1):
            candidate = lines[index]
            if not _looks_like_formula_line(candidate):
                break
            start = index
        selected = [line for line in lines[start : end + 1] if line]
        if selected:
            return "\n".join(selected)
        matched += 1
    return ""


def _strip_label(value: str, label: str) -> str:
    clean = value.strip()
    marker = re.compile(
        rf"(?<![A-Za-z0-9_])\({re.escape(str(label))}\)"
    )
    matches = list(marker.finditer(clean))
    if not matches:
        return clean.rstrip(" ,")
    return clean[: matches[-1].start()].rstrip(" ,")


def _greek(value: str) -> str:
    return _GREEK_LATEX.get(value, value)


def _weighted_sum_latex(value: str) -> str | None:
    collapsed = re.sub(r"\s+", " ", value).strip()
    pattern = re.compile(
        r"^(?P<lhs>[A-Za-z])\s*(?P<lhs_sub>[A-Za-z0-9]+)\s*=\s*"
        r"∑\s*(?P<upper>[A-Za-z0-9]+)\s*"
        r"(?P<index>[A-Za-z])\s*=\s*(?P<lower>[A-Za-z0-9]+)\s*"
        r"(?P<weight>[A-Za-zα-ωΑ-Ω])\s*(?P<weight_sub>[A-Za-z0-9]+)\s*"
        r"(?P<weight_sup>[A-Za-z0-9]+)\s*"
        r"(?P<term>[A-Za-z])\s*(?P<term_sub>[A-Za-z0-9]+)\s*"
        r"(?P<term_sup>[A-Za-z0-9]+)$"
    )
    match = pattern.match(collapsed)
    if not match:
        return None
    groups = match.groupdict()
    return (
        f"{groups['lhs']}_{groups['lhs_sub']} = "
        rf"\sum_{{{groups['index']}={groups['lower']}}}^{{{groups['upper']}}} "
        f"{_greek(groups['weight'])}_{groups['weight_sub']}^{groups['weight_sup']} "
        f"{groups['term']}_{groups['term_sub']}^{groups['term_sup']}"
    )


def _objective_latex(value: str) -> str | None:
    collapsed = re.sub(r"\s+", " ", value).strip()
    pattern = re.compile(
        r"^(?P<lhs>F\([^=]+\))\s*=\s*min\s*(?P<variable>[A-Za-z])\s*"
        r"E(?P<domain>\([^)]*\))\s*∼\s*[˜~]?\s*D\s*"
        r"(?P<loss>L\(.+\))$"
    )
    match = pattern.match(collapsed)
    if not match:
        return None
    groups = match.groupdict()
    return (
        f"{groups['lhs']} = \\min_{{{groups['variable']}}} "
        rf"\mathbb{{E}}_{{{groups['domain']}\sim\widetilde{{D}}}} {groups['loss']}"
    )


def _piecewise_identifier(value: str) -> str:
    clean = re.sub(r"\s+", "", value)
    if re.fullmatch(r"[A-Z][a-z]{2,}", clean):
        return f"{clean[0]}_{{{clean[1:]}}}"
    return clean


def _piecewise_latex(value: str, label: str) -> str | None:
    """Recover two-branch cases only when both branches are explicit."""
    clean = sanitize_formula_text(value).replace("−", "-")
    clean = re.sub(
        rf"(?<![A-Za-z0-9_])\({re.escape(str(label))}\)",
        " ",
        clean,
    )
    clean = re.sub(r"\bi\s+f\b", "if", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip(" ,")

    probability = list(
        re.finditer(
            r"(?P<value>[+\-]?\s*\d+(?:\.\d+)?)\s+with\s+probability\s+"
            r"(?P<numerator>\d+)\s*/\s*(?P<denominator>\d+)",
            clean,
            flags=re.IGNORECASE,
        )
    )
    lhs_at_end = re.search(r"\b(?P<lhs>[A-Za-z][A-Za-z0-9_]*)\s*=\s*$", clean)
    if len(probability) == 2 and lhs_at_end:
        branches = [
            (
                re.sub(r"\s+", "", match.group("value")),
                match.group("numerator"),
                match.group("denominator"),
            )
            for match in probability
        ]
        lhs = _piecewise_identifier(lhs_at_end.group("lhs"))
        first, second = branches
        return (
            f"{lhs} = \\begin{{cases}}\n"
            f"{first[0]}, & \\text{{with probability }} \\frac{{{first[1]}}}{{{first[2]}}} \\\\\n"
            f"{second[0]}, & \\text{{with probability }} \\frac{{{second[1]}}}{{{second[2]}}}\n"
            r"\end{cases}"
        )

    conditional = re.search(
        r"(?P<lhs>[A-Za-z][A-Za-z0-9_]*\s*\([^)]*\))\s*=\s*"
        r"(?P<first>[A-Za-z0-9_+\-.]+)\s+if\s+(?P<first_condition>.+?)\s+"
        r"(?P<second>[+\-]?\d+(?:\.\d+)?)\.?\s+if\s+(?P<second_condition>.+)$",
        clean,
        flags=re.IGNORECASE,
    )
    if conditional:
        groups = conditional.groupdict()
        lhs = re.sub(r"\s+", "", groups["lhs"])
        first = groups["first"].rstrip(".")
        second = groups["second"].rstrip(".")
        first_condition = _conservative_latex(groups["first_condition"].strip(" .,"))
        second_condition = _conservative_latex(groups["second_condition"].strip(" .,"))
        return (
            f"{lhs} = \\begin{{cases}}\n"
            f"{first}, & \\text{{if }} {first_condition} \\\\\n"
            f"{second}, & \\text{{if }} {second_condition}\n"
            r"\end{cases}"
        )
    return None


def _conservative_latex(value: str) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    clean = re.sub(
        r"([‖∥])([^‖∥]+)[‖∥]",
        lambda match: rf"\lVert {match.group(2).strip()} \rVert",
        clean,
    )
    clean = re.sub(
        r"\|\|\s*([^|]+?)\s*\|\|",
        lambda match: rf"\lVert {match.group(1).strip()} \rVert",
        clean,
    )
    clean = clean.replace("‖", r"\Vert ").replace("∥", r"\Vert ")
    for symbol, command in _UNICODE_LATEX.items():
        clean = clean.replace(symbol, command + " ")
    clean = re.sub(r"[˜~]\s*D\b", r"\\widetilde{D}", clean)
    for symbol, command in _GREEK_LATEX.items():
        clean = clean.replace(symbol, command + " ")
    return re.sub(r"\s+", " ", clean).strip()


def _formula_information_score(value: str) -> int:
    compact = re.sub(r"\s+", "", value)
    operator_count = len(re.findall(r"[=∑∏∼≈≤≥<>/⊙∥]", value))
    return len(compact) + operator_count * 12 + (24 if "=" in value else 0)


def recover_formula(
    *,
    raw_text: str,
    fallback_text: str,
    label: str,
    page_number: int,
    bbox: tuple[float, float, float, float],
) -> FormulaCandidate:
    raw = sanitize_formula_text(raw_text)
    fallback = sanitize_formula_text(fallback_text)
    raw_piecewise = _piecewise_latex(raw, label)
    fallback_piecewise = _piecewise_latex(fallback, label)
    raw_without_label = _strip_label(raw, label)
    fallback_without_label = _strip_label(fallback, label)
    use_fallback = bool(fallback_without_label) and (
        _formula_information_score(fallback_without_label)
        >= _formula_information_score(raw_without_label)
    )
    without_label = fallback_without_label if use_fallback else raw_without_label
    piecewise = fallback_piecewise if use_fallback and fallback_piecewise else raw_piecewise
    latex = piecewise or _weighted_sum_latex(without_label) or _objective_latex(without_label)
    specialized = latex is not None
    if latex is None:
        latex = _conservative_latex(without_label)
    source = "pypdf_page_text" if use_fallback and fallback_piecewise else "pymupdf"
    if piecewise is None:
        source = "pypdf_page_text" if use_fallback else "pymupdf"
    confidence = "high" if specialized else "medium"
    markdown = f"$$\n{latex}\n\\tag{{{label}}}\n$$"
    return FormulaCandidate(
        label=str(label),
        page_number=int(page_number),
        bbox=tuple(float(value) for value in bbox),
        raw_text=raw,
        latex=latex,
        markdown=markdown,
        recovery_source=source,
        confidence=confidence,
    )
