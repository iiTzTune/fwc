"""Build the offline field guide and check generated references."""

import argparse
import importlib.util
import json
import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import asdict, fields
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import markdown
from fwc.capture.worker import Settings
from fwc.plugins.host import PERMISSIONS, Manifest
from jsonschema import Draft202012Validator
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUTPUT = ROOT / "build/docs"
COMPONENTS = DOCS / "components.md"


def read_json(path):
    return json.loads(path.read_text())


def type_text(prop):
    if "$ref" in prop:
        return prop["$ref"].split("/")[-1]
    if "anyOf" in prop:
        return " or ".join(type_text(option) for option in prop["anyOf"])
    if "const" in prop:
        return json.dumps(prop["const"])
    if "enum" in prop:
        return ", ".join(prop["enum"])
    if prop["type"] == "array":
        if "prefixItems" in prop:
            return (
                "[" + ", ".join(type_text(item) for item in prop["prefixItems"]) + "]"
            )
        return "list of " + type_text(prop["items"])
    text = prop["type"]
    if "minimum" in prop and "maximum" in prop:
        return f"{text} ({prop['minimum']}–{prop['maximum']})"
    for key, symbol in [("minimum", "≥"), ("maximum", "≤"), ("exclusiveMinimum", ">")]:
        if key in prop:
            text += f" {symbol} {prop[key]}"
    return text


def section(title, schema):
    lines = [
        f"## {title}",
        "",
        "| Field | Value | Presence or default | Use |",
        "| --- | --- | --- | --- |",
    ]
    for field, prop in schema["properties"].items():
        use = [prop.get("description", "")]
        if "pattern" in prop:
            use.append(f"Pattern `{prop['pattern']}`.")
        for key, label in (
            ("minItems", "Minimum items"),
            ("maxItems", "Maximum items"),
        ):
            if key in prop:
                use.append(f"{label} {prop[key]}.")
        if "default" in prop:
            presence = "`" + json.dumps(prop["default"]) + "`"
        else:
            presence = "Required" if field in schema.get("required", []) else "Optional"
        lines.append(
            f"| `{field}` | {type_text(prop)} | {presence} | {' '.join(use).strip()} |"
        )
    return "\n".join(lines) + "\n"


def configuration():
    schema = read_json(DOCS / "settings.schema.json")
    Draft202012Validator.check_schema(schema)
    models = schema["$defs"]
    api = read_json(ROOT / "web/openapi.json")["components"]["schemas"]["Config"]
    capture = json.loads(json.dumps(models["capture-paths"]))
    capture["properties"].update(json.loads(json.dumps(api["properties"])))
    # The API caps the submitted list length. Capture settings on disk have no count cap.
    capture["properties"]["channels"].pop("maxItems", None)
    descriptions = {
        "channels": "Channel numbers in observation order.",
        "dwell_ms": "Milliseconds spent on each channel.",
        "max_bytes": "Maximum bytes in one pcap file, including its header.",
        "file_count": "Maximum number of observation files retained.",
        "allow_ssids": "Selected network names. An empty list observes all eligible names.",
        "deny_ssids": "Excluded network names. This list takes precedence.",
    }
    defaults = asdict(Settings("phy0", "fwcmon0"))
    assert set(capture["properties"]) == {f.name for f in fields(Settings)}
    for key, description in descriptions.items():
        capture["properties"][key].update(
            default=defaults[key], description=description
        )
    assert (
        set(models["plugin"]["properties"]["permissions"]["items"]["enum"])
        == PERMISSIONS
    )

    def validate(name, value):
        Draft202012Validator(schema | {"$ref": "#/$defs/" + name}).validate(value)

    device = read_json(ROOT / "image/device.json")
    validate("device", device)
    spec = importlib.util.spec_from_file_location(
        "image_builder", ROOT / "image/build.py"
    )
    image_builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(image_builder)
    image_builder.validate(device)
    with tempfile.TemporaryDirectory() as work:
        image_builder.populate_data(Path(work))
        data = Path(work) / "data"
        validate("core", read_json(data / "core.json"))
        validate("service", read_json(data / "capture-service.json"))
        sample = read_json(data / "capture.json")
        Draft202012Validator(capture).validate(sample)
        Settings.parse(sample)
    for path in sorted((ROOT / "plugins/examples").glob("*/manifest.json")):
        validate("plugin", read_json(path))
        Manifest.load(path.parent)

    parts = [
        """# Configuration reference

On the reference image, the writable volume holds `/var/lib/fwc/core.json`,
`/var/lib/fwc/capture.json` and the `plugins/` directory. The core validates
service and driver selections at startup. Capture settings saved through the
dashboard take effect the next time the worker starts. A plugin manifest is
read at host startup. The device description is used when building an image.

This reference is generated from the [configuration schemas](settings.schema.json),
the [published API schema](../web/openapi.json) and the worker defaults.
Driver names resolve through installed package entry points. The option
sections below cover the included packages. [Driver setup](../drivers/README.md)
"""
    ]
    for title, model in [
        ("Core settings", "core"),
        ("Service entries", "service"),
        ("Driver registry", "registry"),
        ("Driver selection", "display"),
        ("IIO voltage options", "iio"),
        ("Headless display options", "dummy-display"),
        ("Headless power options", "dummy-power"),
        ("Headless input options", "dummy-input"),
    ]:
        parts.append(section(title, models[model]))
    parts.append(
        "The Waveshare V4 display and headless indicator accept an empty options object.\n"
    )
    parts.append(section("Capture worker", capture))
    parts.append(
        "The HTTP settings object includes all six editable fields. The API accepts at most 233 channel entries. Each SSID is at most 32 UTF-8 bytes.\n"
    )
    for title, model in [
        ("Plugin manifest", "plugin"),
        ("Plugin budget", "budget"),
        ("Image description", "device"),
        ("Image file mapping", "mapping"),
    ]:
        parts.append(section(title, models[model]))
    return "\n".join(parts)


def versions():
    def manifest(path, table):
        with (ROOT / path).open("rb") as stream:
            return tomllib.load(stream)[table]

    workspace = manifest("pyproject.toml", "project")
    core = manifest("core/Cargo.toml", "package")
    api = read_json(ROOT / "web/openapi.json")
    rows = [
        ("fwc-workspace and device image", workspace["version"]),
        ("fwc-core, supervisor, bus, HAL and fwc-update", core["version"]),
        ("Dashboard and HTTP API", api["info"]["version"]),
        ("OpenAPI format", api["openapi"]),
    ]
    members = manifest("pyproject.toml", "tool")["uv"]["workspace"]["members"]
    for pattern in members:
        for directory in sorted(ROOT.glob(pattern)):
            pkg = manifest(directory / "pyproject.toml", "project")
            rows.append((pkg["name"], pkg["version"]))
    migration = max(int(p.stem) for p in (ROOT / "core/migrations").glob("*.sql"))
    rows.append(("SQLite activity store schema", str(migration)))
    for path in sorted((ROOT / "plugins/examples").glob("*/manifest.json")):
        plugin = read_json(path)
        rows.append(
            (
                plugin["name"],
                f"{manifest('plugins/pyproject.toml', 'project')['version']} (plugin API {plugin['api_version']})",
            )
        )
    locked = (ROOT / "image/alpine.lock").read_text()
    for title, pattern in [
        ("Alpine root filesystem", r"/alpine-minirootfs-([0-9.]+)-x86_64"),
        ("Linux kernel package", r"/linux-lts-([\d.r-]+)\.apk"),
        ("Board Python package", r"/python3-([\d.r-]+)\.apk"),
    ]:
        rows.append((title, re.search(pattern, locked).group(1)))
    return "\n".join(
        [
            "| Component | Version |",
            "| --- | --- |",
            *(f"| {k} | {v} |" for k, v in rows),
        ]
    )


class LocalLinks(Treeprocessor):
    """Resolve references relative to the source document and preserve offline links."""

    def __init__(self, md, page, pages):
        super().__init__(md)
        self.page = page
        self.pages = pages

    def run(self, root):
        for node in root.iter("a"):
            link = urlsplit(node.get("href", ""))
            if link.scheme or link.netloc or not link.path:
                continue
            target = (self.page.parent / unquote(link.path)).resolve()
            if not target.is_relative_to(ROOT) or not target.is_file():
                raise ValueError(f"Broken local link in {self.page.name}: {link.path}")
            if target.suffix == ".md":
                dest = self.pages[target]
            else:
                dest = OUTPUT / target.relative_to(ROOT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, dest)
            relative = os.path.relpath(dest, self.pages[self.page].parent)
            node.set("href", urlunsplit(("", "", relative, link.query, link.fragment)))


class LinksExtension(Extension):
    def __init__(self, page, pages):
        super().__init__()
        self.page, self.pages = page, pages

    def extendMarkdown(self, md):
        md.treeprocessors.register(
            LocalLinks(md, self.page, self.pages), "local-links", 0
        )


def build(refresh):
    reference = DOCS / "configuration.md"
    for path, content in [
        (reference, configuration()),
        (COMPONENTS, "# Current component versions\n\n" + versions() + "\n"),
    ]:
        if refresh:
            path.write_text(content)
        elif not path.is_file() or path.read_text() != content:
            raise ValueError(
                f"{path.name} is stale. Run uv run python docs/build.py --refresh"
            )
    docs = [ROOT / "README.md", *DOCS.rglob("*.md")]
    for lane in ["web", "drivers", "capture", "plugins", "image"]:
        docs.extend((ROOT / lane).glob("*.md"))
    pages = {src: OUTPUT / src.relative_to(ROOT).with_suffix(".html") for src in docs}
    pages[ROOT / "README.md"] = OUTPUT / "index.html"
    for src, dest in pages.items():
        home = os.path.relpath(OUTPUT / "index.html", dest.parent)
        title = src.read_text().splitlines()[0].lstrip("# ")
        body = markdown.markdown(
            src.read_text(),
            extensions=["tables", "fenced_code", "toc", LinksExtension(src, pages)],
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"""<!DOCTYPE html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · FWC</title>
<style>body{{max-width:75ch;margin:auto;padding:1.5rem;font:16px/1.6 system-ui,sans-serif}}
pre,table{{overflow:auto;display:block}}pre{{padding:1rem;background:#f3f4f2}}
table{{border-collapse:collapse}}td,th{{padding:.45rem;text-align:left;border-bottom:1px solid #aaa}}
a{{color:#124c72}}nav{{margin-bottom:1rem}}code{{overflow-wrap:anywhere}}</style>
<nav><a href="{home}">Field Wireless Companion guide</a></nav><main>{body}</main></html>
""")
    print(
        f"Built {len(pages)} pages. Configuration reference, current component versions and local links passed."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="rewrite generated Markdown"
    )
    build(parser.parse_args().refresh)
