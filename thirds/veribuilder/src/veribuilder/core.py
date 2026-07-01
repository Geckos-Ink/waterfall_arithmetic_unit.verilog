# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

"""Project-level helpers for parameterized Verilog generators."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable, Iterable, Mapping

FeatureGate = str | Iterable[str] | Callable[[set[str]], bool] | None


@dataclass(frozen=True)
class VerilogHeader:
    """Header prepended to Verilog source files when missing."""

    lines: tuple[str, ...] = ()

    @classmethod
    def spdx(cls, identifier: str, reference: str | None = None) -> "VerilogHeader":
        lines = (f"// SPDX-License-Identifier: {identifier}",)
        if reference:
            lines = (*lines, f"// {reference}")
        return cls(lines)

    @property
    def text(self) -> str:
        if not self.lines:
            return ""
        return "\n".join(self.lines) + "\n\n"

    def apply(self, content: str) -> str:
        header = self.text
        if not header or content.startswith(header):
            return content
        return header + content


@dataclass(frozen=True)
class GeneratedFile:
    """A generated project file and its emission options."""

    path: str
    content: str
    verilog: bool = False
    when: FeatureGate = None

    def enabled(self, features: set[str]) -> bool:
        if self.when is None:
            return True
        if isinstance(self.when, str):
            return self.when in features
        if callable(self.when):
            return bool(self.when(features))
        return all(feature in features for feature in self.when)


@dataclass
class TemplateRenderer:
    """Render simple `{{ name }}` placeholders from generator parameters."""

    parameters: Mapping[str, object] = field(default_factory=dict)
    missing: str = "error"

    _PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")

    def render(self, template: str, **overrides: object) -> str:
        values = {**self.parameters, **overrides}

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in values:
                return str(values[key])
            if self.missing == "keep":
                return match.group(0)
            if self.missing == "empty":
                return ""
            raise KeyError(f"missing template parameter: {key}")

        return self._PATTERN.sub(replace, template)


@dataclass
class VerilogProject:
    """Manifest and emitter for a generated Verilog project."""

    header: VerilogHeader = field(default_factory=VerilogHeader)
    features: set[str] = field(default_factory=set)
    files: list[GeneratedFile] = field(default_factory=list)

    def enable(self, *features: str) -> None:
        self.features.update(features)

    def add_file(self, path: str, content: str, *, when: FeatureGate = None) -> None:
        self.files.append(GeneratedFile(path=path, content=content, when=when))

    def add_verilog(self, path: str, content: str, *, when: FeatureGate = None) -> None:
        self.files.append(GeneratedFile(path=path, content=content, verilog=True, when=when))

    def selected_files(self) -> list[GeneratedFile]:
        return [file for file in self.files if file.enabled(self.features)]

    def emit(self, out_dir: str | Path) -> list[Path]:
        root = Path(out_dir)
        root.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for file in self.selected_files():
            path = root / file.path
            path.parent.mkdir(parents=True, exist_ok=True)
            content = self.header.apply(file.content) if file.verilog else file.content
            path.write_text(content)
            written.append(path)
        return written
