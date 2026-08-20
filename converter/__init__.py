"""Word/PDF/Markdown/LaTeX conversion and PDF compilation.

Heavy converters are imported lazily so Django can boot without optional
deps until an evaluation-report upload/convert path is actually used.
"""

from __future__ import annotations

__all__ = [
    "CompileResult",
    "LaTeXCompiler",
    "LaTeXCompilerError",
    "convert_docx_to_markdown",
    "convert_pdf_to_latex",
]


def __getattr__(name: str):
    if name in ("CompileResult", "LaTeXCompiler", "LaTeXCompilerError"):
        from converter.latex_compiler import CompileResult, LaTeXCompiler, LaTeXCompilerError

        mapping = {
            "CompileResult": CompileResult,
            "LaTeXCompiler": LaTeXCompiler,
            "LaTeXCompilerError": LaTeXCompilerError,
        }
        return mapping[name]
    if name == "convert_docx_to_markdown":
        from converter.word2md import convert_docx_to_markdown

        return convert_docx_to_markdown
    if name == "convert_pdf_to_latex":
        from converter.pdf2latex import convert_pdf_to_latex

        return convert_pdf_to_latex
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
