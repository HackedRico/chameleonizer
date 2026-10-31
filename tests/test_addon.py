from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image

# =============================================================================
# Module Overview
# =============================================================================
# Checks the viewer add-on in `addon/`: `manifest.json` against the manifest
# version 2 rules Adobe's CLI applies to a developer add-on, how `index.html`
# loads the UI SDK, and every render that `renders/index.json` lists against
# the size the panel says it has.

ADDON = Path(__file__).resolve().parents[1] / "addon"
CATALOG = json.loads((ADDON / "renders" / "index.json").read_text())
# Allowed keys and patterns from `manifestSchemaV2` in github.com/adobe/create-ccweb-add-on,
# packages/add-on-manifest/scripts/AddOnManifestSchema.js and AddOnManifestSchemaTypes.js.
REQUIREMENT_KEYS = {"apps", "experimentalApis", "supportsTouch", "renditionPreview", "privilegedApis",
                    "_blessedPartnerAccess", "trustedPartnerApis"}
APP_KEYS = {"name", "apiVersion", "supportedDeviceClass"}
ENTRY_POINT_KEYS = {"type", "id", "main", "script", "documentSandbox", "hostDomain", "commands", "permissions",
                    "defaultSize", "discoverable"}


def test_manifest_meets_adobe_v2_rules():
    manifest = json.loads((ADDON / "manifest.json").read_text())
    assert manifest["manifestVersion"] == 2
    assert re.search(r"^[0-9]+.[0-9]+.[0-9]+$", manifest["version"])
    assert re.search(r"^[a-zA-Z0-9]+[a-zA-Z0-9 ]{2,44}$", manifest["name"])
    assert manifest["testId"], "the CLI rejects a developer add-on without `testId`"
    requirements = manifest["requirements"]
    assert set(requirements) <= REQUIREMENT_KEYS
    assert not requirements.get("experimentalApis"), "Adobe requires removing `experimentalApis` before submission"
    for app in requirements["apps"]:
        assert set(app) <= APP_KEYS
        assert (app["name"], app["apiVersion"]) == ("Express", 1)
    entry_points = manifest["entryPoints"]
    assert entry_points
    assert len({ep["id"] for ep in entry_points}) == len(entry_points)
    for ep in entry_points:
        assert set(ep) <= ENTRY_POINT_KEYS
        assert ep["type"] == "panel"
        assert (ADDON / ep["main"]).is_file()


class _ModuleScripts(HTMLParser):
    """Collect the `src` of every `<script type="module">` in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "module" and attributes.get("src"):
            self.sources.append(attributes["src"])


def test_panel_loads_the_sdk_from_a_script_element_before_its_own_code():
    # The SDK only listens for the host's `initPort` message, and the iframe load event that can
    # send it waits for script elements but not for `import()`. In headless Chrome a host that
    # posts `initPort` on load never connected to the panel while the SDK came only from `import()`.
    parser = _ModuleScripts()
    parser.feed((ADDON / "index.html").read_text())
    sdk_url = re.search(r'^const SDK_URL = "([^"]+)";$', (ADDON / "index.js").read_text(), re.MULTILINE).group(1)
    assert sdk_url == "https://new.express.adobe.com/static/add-on-sdk/sdk.js"
    assert sdk_url in parser.sources, "index.html must load the SDK in a module script element"
    assert parser.sources.index(sdk_url) < parser.sources.index("index.js")


@pytest.mark.parametrize("sample", CATALOG["samples"], ids=lambda sample: sample["id"])
def test_every_listed_render_exists_at_its_size(sample):
    with Image.open(ADDON / "renders" / sample["source"]) as image:
        assert image.size == (sample["width"], sample["height"])
    for fmt in CATALOG["formats"]:
        with Image.open(ADDON / "renders" / sample["renders"][fmt["key"]]) as image:
            assert image.size == (fmt["width"], fmt["height"])
