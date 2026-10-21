from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "tgn_voda"


def _leaf_paths(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    return {
        path
        for key, child in value.items()
        for path in _leaf_paths(child, f"{prefix}.{key}" if prefix else key)
    }


def test_project_dependencies_match_home_assistant_manifest() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))

    assert project["version"] == manifest["version"]
    assert project["requires-python"] == ">=3.12"
    assert {
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    } <= set(project["classifiers"])
    # The standalone client needs requests/certifi, but HA supplies those itself.
    assert set(manifest["requirements"]) <= set(project["dependencies"])

    test_requirements = {
        line.strip()
        for line in (ROOT / "requirements-test.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("pytest")
    }
    assert set(project["dependencies"]) <= test_requirements


def test_hacs_metadata_has_repository_display_settings() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert hacs["name"] == "TGN Voda"
    assert hacs["render_readme"] is True
    assert hacs["country"] == "RU"
    assert hacs["homeassistant"] == "2026.8.0"
    assert "domains" not in hacs
    assert hacs["homeassistant"] in readme


def test_translations_have_matching_keys() -> None:
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    russian = json.loads((INTEGRATION / "translations" / "ru.json").read_text(encoding="utf-8"))
    english = json.loads((INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8"))

    assert strings == russian
    assert _leaf_paths(russian) == _leaf_paths(english)


def test_russian_translation_does_not_contain_encoding_replacements() -> None:
    raw = (INTEGRATION / "translations" / "ru.json").read_text(encoding="utf-8")
    russian = json.loads(raw)

    assert "?" not in raw
    assert "\ufffd" not in raw
    assert russian["entity"]["sensor"]["to_pay_now"]["name"] == "К оплате"


def test_icon_table_covers_all_entity_translation_keys() -> None:
    russian = json.loads((INTEGRATION / "translations" / "ru.json").read_text(encoding="utf-8"))
    icons = json.loads((INTEGRATION / "icons.json").read_text(encoding="utf-8"))

    assert set(icons["entity"]) == set(russian["entity"])
    for platform, entities in russian["entity"].items():
        assert set(icons["entity"][platform]) == set(entities)


def test_icon_table_covers_all_services() -> None:
    icons = json.loads((INTEGRATION / "icons.json").read_text(encoding="utf-8"))
    service_names = set(
        re.findall(
            r"^([a-z][a-z0-9_]*):\s*$",
            (INTEGRATION / "services.yaml").read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
    )

    assert set(icons["services"]) == service_names


def test_python_services_match_service_yaml() -> None:
    source = ast.parse((INTEGRATION / "services.py").read_text(encoding="utf-8"))
    constants: dict[str, str] = {}
    described_services: set[str] = set()
    service_schema_fields: dict[str, set[str]] = {}
    for node in source.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if (
                target.id.startswith("SERVICE_")
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                constants[target.id] = value.value
            if target.id == "SERVICE_DESCRIPTIONS" and isinstance(value, ast.Dict):
                described_services = {key.id for key in value.keys if isinstance(key, ast.Name)}
            if target.id == "SERVICE_SCHEMAS" and isinstance(value, ast.Dict):
                for key, schema in zip(value.keys, value.values, strict=True):
                    if not isinstance(key, ast.Name) or not isinstance(schema, ast.Call):
                        continue
                    if isinstance(schema.func, ast.Name) and schema.func.id == "_schema":
                        fields = {"entry_id", "device_id"}
                        for argument in schema.args:
                            if (
                                isinstance(argument, ast.Tuple)
                                and argument.elts
                                and isinstance(argument.elts[0], ast.Constant)
                                and isinstance(argument.elts[0].value, str)
                            ):
                                fields.add(argument.elts[0].value)
                        service_schema_fields[key.id] = fields
                    elif (
                        isinstance(schema.func, ast.Name)
                        and schema.func.id == "_counter_info_schema"
                    ):
                        service_schema_fields[key.id] = {
                            "entry_id",
                            "device_id",
                            "counter_id",
                        }

    yaml_service_fields: dict[str, set[str]] = {}
    current_service: str | None = None
    in_fields = False
    for line in (INTEGRATION / "services.yaml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if indent == 0 and re.fullmatch(r"[a-z][a-z0-9_]*:", stripped):
            current_service = stripped[:-1]
            yaml_service_fields[current_service] = set()
            in_fields = False
        elif current_service is not None and indent == 2 and stripped == "fields:":
            in_fields = True
        elif (
            current_service is not None
            and in_fields
            and indent == 4
            and re.fullmatch(r"[a-z][a-z0-9_]*:", stripped)
        ):
            yaml_service_fields[current_service].add(stripped[:-1])

    assert described_services == set(constants)
    assert {constants[name] for name in described_services} == set(yaml_service_fields)
    assert {
        constants[constant]: fields for constant, fields in service_schema_fields.items()
    } == yaml_service_fields


def test_readme_automation_links_point_to_files() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    examples = (
        "automation/submit_readings.yaml",
        "automation/notify_about_debt.yaml",
    )

    for example in examples:
        assert example in readme
        assert (ROOT / example).is_file()
