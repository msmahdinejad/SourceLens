from __future__ import annotations

import html
import re
from functools import lru_cache
from typing import Any
from xml.etree import ElementTree

from latex2mathml.converter import convert as latex_to_mathml
from markdown_it import MarkdownIt

_MARKDOWN = MarkdownIt(
    "commonmark",
    {"html": False, "linkify": True, "typographer": True},
).enable("table")
_BLOCK_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH = re.compile(r"(?<!\\)\$(?!\$)([^\n$]+?)(?<!\\)\$")
_CURRENCY_PREFIX = re.compile(
    r"^\s*[+-]?\d[\d,.]*(?:\s*(?:k|m|bn|million|billion|trillion|thousand|"
    r"usd|eur|gbp|cad|aud|dollars?|ریال|تومان))?\b",
    re.IGNORECASE,
)
_CODE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]+`)", re.DOTALL)
_BARE_MATH_SYMBOL = re.compile(
    r"(?<![\\$\w])\\(alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|"
    r"vartheta|iota|kappa|lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma|tau|"
    r"upsilon|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|"
    r"Phi|Psi|Omega|infty|pm|mp|times|div|leq|geq|neq|approx)(?![A-Za-z])"
)
_MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
_SAFE_MATHML_TAGS = {
    "math",
    "menclose",
    "mfenced",
    "mfrac",
    "mi",
    "mmultiscripts",
    "mn",
    "mo",
    "mover",
    "mpadded",
    "mphantom",
    "mprescripts",
    "mroot",
    "mrow",
    "mspace",
    "msqrt",
    "mstyle",
    "msub",
    "msubsup",
    "msup",
    "mtable",
    "mtd",
    "mtext",
    "mtr",
    "munder",
    "munderover",
    "none",
}
_SAFE_MATHML_ATTRIBUTES = {
    "accent",
    "accentunder",
    "close",
    "columnalign",
    "columnspacing",
    "columnspan",
    "depth",
    "displaystyle",
    "display",
    "height",
    "linethickness",
    "lspace",
    "mathvariant",
    "movablelimits",
    "notation",
    "open",
    "rowalign",
    "rowspacing",
    "rowspan",
    "rspace",
    "scriptlevel",
    "separators",
    "stretchy",
    "symmetric",
    "width",
}


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _safe_mathml(source: str, *, classes: str) -> str:
    root = ElementTree.fromstring(source)
    for parent in list(root.iter()):
        for child in list(parent):
            if _local_name(child.tag) in _SAFE_MATHML_TAGS:
                continue
            replacement = ElementTree.Element(f"{{{_MATHML_NAMESPACE}}}mtext")
            replacement.text = "".join(child.itertext())
            replacement.tail = child.tail
            position = list(parent).index(child)
            parent.remove(child)
            parent.insert(position, replacement)
    for element in root.iter():
        element.attrib = {
            key: value
            for key, value in element.attrib.items()
            if _local_name(key) in _SAFE_MATHML_ATTRIBUTES
        }
    root.set("class", classes)
    root.set("dir", "ltr")
    ElementTree.register_namespace("", _MATHML_NAMESPACE)
    return ElementTree.tostring(root, encoding="unicode", short_empty_elements=True)


@lru_cache(maxsize=4096)
def _math_markup(formula: str, *, block: bool) -> str:
    display = "block" if block else "inline"
    classes = "math-expression math-block" if block else "math-expression"
    formula = formula.replace(r"\arg\max", r"\argmax").replace(r"\arg\min", r"\argmin")
    try:
        mathml = latex_to_mathml(formula, display=display)
        return _safe_mathml(mathml, classes=classes)
    except Exception:
        tag = "div" if block else "span"
        return f'<{tag} class="{classes}" dir="ltr">{html.escape(formula)}</{tag}>'


@lru_cache(maxsize=4096)
def _render(source: str, *, inline: bool) -> str:
    protected = source or ""
    code_replacements: dict[str, str] = {}
    replacements: dict[str, str] = {}

    def protect_code(match: re.Match[str]) -> str:
        token = f"PAPERLENSCODE{len(code_replacements)}TOKEN"
        code_replacements[token] = match.group(0)
        return token

    protected = _CODE.sub(protect_code, protected)

    def protect_block(match: re.Match[str]) -> str:
        token = f"PAPERLENSBLOCKMATH{len(replacements)}TOKEN"
        replacements[token] = _math_markup(match.group(1).strip(), block=True)
        return f"\n\n{token}\n\n"

    protected = _BLOCK_MATH.sub(protect_block, protected)

    def protect_inline(match: re.Match[str]) -> str:
        formula = match.group(1).strip()
        currency = _CURRENCY_PREFIX.match(formula)
        if currency and not re.search(r"[=+*/^_\\]|\s-\s", formula):
            return match.group(0)
        token = f"PAPERLENSINLINEMATH{len(replacements)}TOKEN"
        replacements[token] = _math_markup(formula, block=False)
        return token

    protected = _INLINE_MATH.sub(protect_inline, protected)

    def protect_bare_symbol(match: re.Match[str]) -> str:
        token = f"PAPERLENSBAREMATH{len(replacements)}TOKEN"
        replacements[token] = _math_markup(match.group(0), block=False)
        return token

    protected = _BARE_MATH_SYMBOL.sub(protect_bare_symbol, protected)
    for token, code in code_replacements.items():
        protected = protected.replace(token, code)

    rendered = _MARKDOWN.renderInline(protected) if inline else _MARKDOWN.render(protected)
    for token, replacement in replacements.items():
        rendered = rendered.replace(f"<p>{token}</p>", replacement)
        rendered = rendered.replace(token, replacement)
    return rendered


def render_markdown(source: str) -> str:
    """Render untrusted model Markdown and typeset supported TeX as native MathML."""
    return _render(source, inline=False)


def render_inline_markdown(source: str) -> str:
    """Render an untrusted Markdown fragment without a wrapping paragraph."""
    return _render(source, inline=True)


def render_markdown_tree(value: Any) -> Any:
    """Mirror structured artifact data with safe inline and block HTML leaves."""
    if isinstance(value, str):
        return {
            "inline": render_inline_markdown(value),
            "block": render_markdown(value),
        }
    if isinstance(value, list):
        return [render_markdown_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: render_markdown_tree(item) for key, item in value.items()}
    return value
