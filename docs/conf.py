"""Sphinx configuration for the Thea documentation site."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

project = "Thea"
author = "Thea contributors"
copyright = "Thea contributors"

extensions = [
    "myst_parser",
    "sphinx_copybutton",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

myst_enable_extensions = [
    "attrs_block",
    "attrs_inline",
    "colon_fence",
    "deflist",
    "fieldlist",
    "substitution",
]
myst_heading_anchors = 3

html_theme = "furo"
html_title = "Thea Documentation"
html_static_path = ["_static"]
html_extra_path = ["assets/overview.png"]
html_css_files = ["custom.css"]
html_favicon = None

html_theme_options = {
    "navigation_with_keys": True,
    "light_css_variables": {
        "color-brand-primary": "#526f78",
        "color-brand-content": "#526f78",
        "color-background-primary": "#fbfaf7",
        "color-background-secondary": "#f3f1ec",
        "color-foreground-primary": "#303234",
        "color-foreground-secondary": "#696d70",
        "color-sidebar-background": "#f3f1ec",
        "color-sidebar-background-border": "#ddd9d2",
        "color-admonition-background": "#f3f1ec",
        "font-stack": (
            "Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "
            "'Segoe UI', sans-serif"
        ),
        "font-stack--monospace": (
            "'SFMono-Regular', Consolas, 'Liberation Mono', monospace"
        ),
    },
    "dark_css_variables": {
        "color-brand-primary": "#93c0bd",
        "color-brand-content": "#93c0bd",
        "color-background-primary": "#202426",
        "color-background-secondary": "#282d30",
        "color-foreground-primary": "#f0eee8",
        "color-foreground-secondary": "#c6c4be",
        "color-sidebar-background": "#202426",
        "color-sidebar-background-border": "#41484b",
        "color-admonition-background": "#282d30",
    },
}

copybutton_prompt_text = r">>> |\.\.\. |\$ |In \[\d+\]: | {2,5}\.\.\.: "
copybutton_prompt_is_regexp = True

suppress_warnings = ["myst.header"]


def _redirect_home_to_first_guide(
    app,
    page_name: str,
    template_name: str,
    context: dict[str, object],
    doctree,
) -> None:
    """Open the first guide when a reader visits the documentation root."""
    if page_name != "index":
        return

    context["metatags"] = (
        f"{context.get('metatags', '')}"
        '<meta http-equiv="refresh" content="0; url=usage/index.html">'
        '<link rel="canonical" href="usage/index.html">'
    )


def setup(app) -> dict[str, bool]:
    app.connect("html-page-context", _redirect_home_to_first_guide)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
