from paperlens.rendering import render_inline_markdown, render_markdown


def test_markdown_renderer_preserves_structure_and_math() -> None:
    source = """# Study guide

## Value iteration

- First concept
- Second concept

The optimal function is $Q^*(s,a)$ and the policy is $a^* = \\arg\\max_a Q^*(s,a)$.

| Method | Property |
| --- | --- |
| Q-learning | Off-policy |
"""

    html = render_markdown(source)

    assert "<h1>Study guide</h1>" in html
    assert "<ul>" in html
    assert "<table>" in html
    assert html.count('class="math-expression"') == 2
    assert "<msup><mi>Q</mi>" in html
    assert "<em>" not in html


def test_markdown_renderer_does_not_allow_model_supplied_html() -> None:
    html = render_markdown("<script>alert('x')</script>\n\n**Safe**")
    math_html = render_markdown(
        r"$\text{<script>alert(1)</script>} + \href{javascript:alert(1)}{x}$"
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>Safe</strong>" in html
    assert "<script>" not in math_html
    assert "javascript:" not in math_html


def test_markdown_renderer_typesets_delimited_and_bare_latex() -> None:
    source = r"The $\epsilon$-greedy policy can also be written as \epsilon-greedy."

    block = render_markdown(source)
    inline = render_inline_markdown(r"Use **exploration** with \epsilon.")

    assert block.count("<math") == 2
    assert "\\epsilon" not in block
    assert "<p>" not in inline
    assert "<strong>exploration</strong>" in inline
    assert "<math" in inline
    assert "\\epsilon" not in inline


def test_markdown_renderer_does_not_treat_currency_as_math() -> None:
    source = (
        "Reefs prevent over $859 million (2010 USD) in annual losses.\n\n"
        "Benefits range from $1 million to $2 billion, while $5 + 3 = 8$ remains math."
    )

    html = render_markdown(source)

    assert "$859 million (2010 USD)" in html
    assert "$1 million to $2 billion" in html
    assert html.count('class="math-expression"') == 1
