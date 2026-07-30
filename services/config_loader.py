from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List

BASE_DIR = Path(__file__).resolve().parents[1]
SCHEMA_DIR = BASE_DIR / "schemas"


def load_schema(api_key: str) -> Dict[str, Any]:
    path = SCHEMA_DIR / f"{api_key}.json"
    if not path.exists():
        raise FileNotFoundError(f"Schema tidak ditemukan: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def available_schemas() -> List[str]:
    return sorted(p.stem for p in SCHEMA_DIR.glob("*.json"))


def filter_mapping_by_columns(mapping: Dict[str, Any], columns: Iterable[str]) -> Dict[str, Any]:
    selected = set(columns)
    new_mapping = deepcopy(mapping)
    new_mapping["fields"] = [f for f in mapping.get("fields", []) if f.get("excel_column") in selected]
    return new_mapping


def filter_mapping_by_preset(mapping: Dict[str, Any], preset: str) -> Dict[str, Any]:
    new_mapping = deepcopy(mapping)
    preset = preset.lower().strip()
    if preset == "all":
        return new_mapping
    if preset == "minimal":
        new_mapping["fields"] = [f for f in mapping.get("fields", []) if f.get("include_in_minimal")]
    elif preset == "standard":
        new_mapping["fields"] = [f for f in mapping.get("fields", []) if f.get("include_in_standard") or f.get("include_in_minimal")]
    return new_mapping


def fields_by_section(mapping: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    sections: Dict[str, List[Dict[str, Any]]] = {}
    for field in mapping.get("fields", []):
        sections.setdefault(field.get("section", "General"), []).append(field)
    return sections


def schema_title(mapping: Dict[str, Any]) -> str:
    return f"{mapping.get('api_name')} — {mapping.get('method')} {mapping.get('endpoint') or mapping.get('endpoint_template')}"
