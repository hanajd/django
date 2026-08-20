from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

LATEX_CMD_PATTERN = re.compile(
    r"\\(?:newcommand|renewcommand)\{(\\[a-zA-Z]+)\}\{([^}]*)\}"
)


@dataclass
class CompileResult:
    success: bool
    pdf_path: Path | None
    log: str
    work_dir: Path | None = None


class LaTeXCompilerError(Exception):
    pass


class LaTeXCompiler:
    """Compile a multi-file LaTeX project directory to PDF."""

    def __init__(
        self,
        engine: str | None = None,
        run_times: int | None = None,
    ):
        self.engine = engine or settings.LATEX_ENGINE
        self.run_times = run_times or settings.LATEX_RUN_TIMES

    def compile_project(
        self,
        project_dir: Path,
        main_file: str | None = None,
        context: dict[str, str] | None = None,
        output_dir: Path | None = None,
        keep_work_dir: bool = False,
    ) -> CompileResult:
        project_dir = Path(project_dir).resolve()
        main_file = main_file or settings.LATEX_MAIN_FILE
        main_path = project_dir / main_file
        if not main_path.is_file():
            raise LaTeXCompilerError(f"Main TeX file not found: {main_path}")

        self._ensure_engine_available()

        work_dir = Path(tempfile.mkdtemp(prefix="latex_build_"))
        try:
            self._copy_project(project_dir, work_dir)
            if context:
                self._apply_context(work_dir / main_file, context)

            log_parts: list[str] = []
            for run in range(1, self.run_times + 1):
                log_parts.append(f"=== {self.engine} run {run}/{self.run_times} ===")
                log_parts.append(self._run_engine(work_dir, main_file))

            pdf_name = Path(main_file).with_suffix(".pdf").name
            built_pdf = work_dir / pdf_name
            if not built_pdf.is_file():
                raise LaTeXCompilerError(
                    "PDF was not generated. Check the compile log for LaTeX errors."
                )

            saved_pdf = self._save_pdf(built_pdf, output_dir, project_dir.name)
            return CompileResult(
                success=True,
                pdf_path=saved_pdf,
                log="\n".join(log_parts),
                work_dir=work_dir if keep_work_dir else None,
            )
        except LaTeXCompilerError:
            if not keep_work_dir and work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
            raise
        finally:
            if not keep_work_dir and work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    def compile_template(
        self,
        template_name: str | None = None,
        context: dict[str, str] | None = None,
        output_dir: Path | None = None,
    ) -> CompileResult:
        template_name = template_name or settings.LATEX_DEFAULT_PROJECT
        project_dir = Path(settings.LATEX_TEMPLATE_ROOT) / template_name
        return self.compile_project(
            project_dir=project_dir,
            context=context,
            output_dir=output_dir,
        )

    def _ensure_engine_available(self) -> None:
        if shutil.which(self.engine) is None:
            raise LaTeXCompilerError(
                f"LaTeX engine '{self.engine}' not found in PATH. "
                "Install TeX Live and ensure lualatex/xelatex is available."
            )

    def _copy_project(self, source: Path, target: Path) -> None:
        for item in source.iterdir():
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    def _apply_context(self, main_tex: Path, context: dict[str, str]) -> None:
        content = main_tex.read_text(encoding="utf-8")
        for command, value in context.items():
            key = command if command.startswith("\\") else f"\\{command}"
            escaped_value = self._escape_latex_value(value)
            pattern = rf"(\\(?:newcommand|renewcommand)\{{{re.escape(key)}\}})\{{[^}}]*\}}"
            new_content, count = re.subn(
                pattern,
                lambda m, v=escaped_value: f"{m.group(1)}{{{v}}}",
                content,
                count=1,
            )
            if count:
                content = new_content
            else:
                content = content.replace(
                    r"\begin{document}",
                    rf"\renewcommand{{{key}}}{{{escaped_value}}}" + "\n\\begin{document}",
                    1,
                )
        main_tex.write_text(content, encoding="utf-8")

    @staticmethod
    def _escape_latex_value(value: str) -> str:
        replacements = {
            "\\": r"\textbackslash{}",
            "&": r"\&",
            "%": r"\%",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
        }
        for char, repl in replacements.items():
            value = value.replace(char, repl)
        return value

    def _run_engine(self, work_dir: Path, main_file: str) -> str:
        cmd = [
            self.engine,
            "-interaction=nonstopmode",
            main_file,
        ]
        completed = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log_path = work_dir / Path(main_file).with_suffix(".log")
        log_text = ""
        if log_path.is_file():
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        combined = "\n".join(part for part in (stdout, stderr, log_text) if part)

        # Some ctex fontset warnings exit non-zero on Windows but still produce PDF.
        pdf_path = work_dir / Path(main_file).with_suffix(".pdf")
        if pdf_path.is_file():
            return combined[-2000:] if len(combined) > 2000 else combined

        if completed.returncode != 0:
            tail = combined[-4000:] if len(combined) > 4000 else combined
            raise LaTeXCompilerError(
                f"{self.engine} failed with exit code {completed.returncode}.\n{tail}"
            )
        return combined[-2000:] if len(combined) > 2000 else combined

    def _save_pdf(
        self,
        built_pdf: Path,
        output_dir: Path | None,
        label: str,
    ) -> Path:
        target_dir = Path(output_dir) if output_dir else Path(settings.MEDIA_ROOT) / "pdfs"
        target_dir.mkdir(parents=True, exist_ok=True)

        from django.utils import timezone

        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{label}_{timestamp}.pdf"
        saved_path = target_dir / filename
        shutil.copy2(built_pdf, saved_path)
        logger.info("Saved PDF to %s", saved_path)
        return saved_path

    def compile_standalone_tex(
        self,
        tex_path: Path,
        *,
        output_dir: Path | None = None,
    ) -> CompileResult:
        """Compile a single standalone .tex file (with sibling images/ etc.)."""
        tex_path = Path(tex_path).resolve()
        if not tex_path.is_file():
            raise LaTeXCompilerError(f"TeX file not found: {tex_path}")

        self._ensure_engine_available()
        work_dir = tex_path.parent
        main_file = tex_path.name

        log_parts: list[str] = []
        for run in range(1, self.run_times + 1):
            log_parts.append(f"=== {self.engine} run {run}/{self.run_times} ===")
            log_parts.append(self._run_engine(work_dir, main_file))

        pdf_path = work_dir / tex_path.with_suffix(".pdf").name
        if not pdf_path.is_file():
            raise LaTeXCompilerError(
                "PDF was not generated. Check the compile log for LaTeX errors."
            )

        saved = self._save_pdf(
            pdf_path,
            output_dir,
            tex_path.stem,
        )
        return CompileResult(
            success=True,
            pdf_path=saved,
            log="\n".join(log_parts),
            work_dir=work_dir,
        )
