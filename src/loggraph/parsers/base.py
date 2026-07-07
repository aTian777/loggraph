from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from loggraph.models import CodeIndex


class SourceParser(ABC):
    @abstractmethod
    def parse_file(self, path: Path, root: Path, index: CodeIndex) -> None:
        raise NotImplementedError
