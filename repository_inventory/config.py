from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .canonical import fingerprint
from .contracts import DEFAULT_EXCLUDED_DIRECTORY_NAMES, INVENTORY_CONFIGURATION_FORMAT


def _normalize_names(values: Iterable[str]) -> tuple[str, ...]:
    normalized = {str(value).strip() for value in values if str(value).strip()}
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class InventoryConfig:
    excluded_directory_names: tuple[str, ...] = DEFAULT_EXCLUDED_DIRECTORY_NAMES
    capture_readable_content: bool = True
    follow_symlinks: bool = False
    structured_probe_max_bytes: int = 8 * 1024 * 1024
    java_probe_max_bytes: int = 8 * 1024 * 1024
    build_config_probe_max_bytes: int = 8 * 1024 * 1024
    sql_probe_max_bytes: int = 8 * 1024 * 1024

    @classmethod
    def create(
        cls,
        *,
        excluded_directory_names: Iterable[str] | None = None,
        capture_readable_content: bool = True,
        follow_symlinks: bool = False,
        structured_probe_max_bytes: int = 8 * 1024 * 1024,
        java_probe_max_bytes: int = 8 * 1024 * 1024,
        build_config_probe_max_bytes: int = 8 * 1024 * 1024,
        sql_probe_max_bytes: int = 8 * 1024 * 1024,
    ) -> "InventoryConfig":
        if follow_symlinks:
            raise ValueError("follow_symlinks=True is not supported by repository-inventory/v7")
        names = (
            DEFAULT_EXCLUDED_DIRECTORY_NAMES
            if excluded_directory_names is None
            else _normalize_names(excluded_directory_names)
        )
        max_bytes = int(structured_probe_max_bytes)
        if max_bytes <= 0:
            raise ValueError("structured_probe_max_bytes must be > 0")
        java_max_bytes = int(java_probe_max_bytes)
        if java_max_bytes <= 0:
            raise ValueError("java_probe_max_bytes must be > 0")
        build_config_max_bytes = int(build_config_probe_max_bytes)
        if build_config_max_bytes <= 0:
            raise ValueError("build_config_probe_max_bytes must be > 0")
        sql_max_bytes = int(sql_probe_max_bytes)
        if sql_max_bytes <= 0:
            raise ValueError("sql_probe_max_bytes must be > 0")
        return cls(
            excluded_directory_names=tuple(names),
            capture_readable_content=bool(capture_readable_content),
            follow_symlinks=False,
            structured_probe_max_bytes=max_bytes,
            java_probe_max_bytes=java_max_bytes,
            build_config_probe_max_bytes=build_config_max_bytes,
            sql_probe_max_bytes=sql_max_bytes,
        )

    def to_semantic_dict(self) -> dict[str, object]:
        return {
            "schema_version": INVENTORY_CONFIGURATION_FORMAT,
            "excluded_directory_names": list(self.excluded_directory_names),
            "capture_readable_content": self.capture_readable_content,
            "follow_symlinks": self.follow_symlinks,
            "structured_probe_max_bytes": self.structured_probe_max_bytes,
            "java_probe_max_bytes": self.java_probe_max_bytes,
            "build_config_probe_max_bytes": self.build_config_probe_max_bytes,
            "sql_probe_max_bytes": self.sql_probe_max_bytes,
        }

    @property
    def source_scope_fingerprint(self) -> str:
        return fingerprint({
            "excluded_directory_names": list(self.excluded_directory_names),
            "follow_symlinks": self.follow_symlinks,
        })

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_semantic_dict())
