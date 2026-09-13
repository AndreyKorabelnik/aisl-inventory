from __future__ import annotations

CONTRACT_FORMAT = "repository-inventory/v7"
REDUCED_INVENTORY_FORMAT = "repository-inventory-reduced/v3"
SOURCE_CONTENT_STORE_FORMAT = "repository-source-content-store/v2"
INVENTORY_CONFIGURATION_FORMAT = "repository-inventory-configuration/v4"
SELECTED_SOURCE_EXPORT_FORMAT = "repository-selected-source-export/v3"
SELECTED_SOURCE_EXPORT_REQUEST_FORMAT = "repository-selected-source-export-request/v2"

FORBIDDEN_AISL_IMPORT_PREFIXES = (
    "code_analyzer_core",
    "static_analysis_runner",
    "knowledge_layer_core",
    "knowledge_control_plane",
    "knowledge_api",
    "prepared_knowledge_runtime",
    "technology_extension_runtime",
    "knowledge_integration",
)

FORBIDDEN_AISL_DISTRIBUTIONS = (
    "code-analyzer-core",
    "static-analysis-runner",
    "knowledge-layer-core",
    "knowledge-control-plane",
    "knowledge-api",
    "prepared-knowledge-runtime",
    "technology-extension-runtime",
    "knowledge-integration",
)

LOCALIZATION_KINDS = frozenset({"file", "exact_span", "declaration", "statement", "section"})

# Source-scope policy, not analyzer eligibility. The complete inventory is complete over
# the explicitly declared in-scope repository frontier after these administrative/cache
# directories are pruned. Changing this set changes the configuration fingerprint.
DEFAULT_EXCLUDED_DIRECTORY_NAMES = (
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".gradle",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
    "build",
    "__MACOSX",
)

# Deterministic syntax/language classification only. This is not framework capability.
EXTENSION_LANGUAGE_MAP = {
    ".java": "java",
    ".py": "python",
    ".sql": "sql",
    ".xml": "xml",
    ".xsd": "xml",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".properties": "properties",
    ".proto": "proto",
    ".gradle": "gradle",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".md": "markdown",
    ".csv": "csv",
    ".tsv": "tsv",
    ".conf": "config_text",
    ".avsc": "json",
}

CONCEPT_CANDIDATE_FORMAT = "repository-inventory-concept-candidates/v1"

CONCEPT_INDEX_FORMAT = "repository-inventory-concept-index/v1"
