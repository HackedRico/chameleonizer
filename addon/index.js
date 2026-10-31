// =============================================================================
// Module Overview
// =============================================================================
// Panel logic for the Chameleonizer viewer add-on. `showSelection` puts the
// bundled source render beside the engine's render for the chosen format, both
// listed in `renders/index.json`. `connectToExpress` loads the UI SDK lazily so
// the previews work without it, and `addToPage` inserts a render with `addImage`.

// Adobe's add-on reference loads the UI SDK from this URL in every example. `index.html` loads
// the same URL in a script element, so the import below reuses that module instance.
const SDK_URL = "https://new.express.adobe.com/static/add-on-sdk/sdk.js";
// After this long without an answer from Express, say that the panel is still waiting.
const SLOW_CONNECT_MS = 10000;
// Outputs wider than this aspect ratio go below the source, because beside it they would be too small to read.
const STACK_ABOVE_ASPECT = 1.2;

const ui = {
    sample: document.getElementById("sample"),
    format: document.getElementById("format"),
    compare: document.getElementById("compare"),
    sourceImage: document.getElementById("source-image"),
    sourceCaption: document.getElementById("source-caption"),
    outputImage: document.getElementById("output-image"),
    outputCaption: document.getElementById("output-caption"),
    addButton: document.getElementById("add-to-page"),
    status: document.getElementById("status"),
    provenance: document.getElementById("provenance"),
};

// =============================================================================
// Previews
// =============================================================================

/** Fetch the catalogue that `build_renders.py` writes next to the PNGs. */
async function loadCatalog() {
    const response = await fetch("renders/index.json");
    if (!response.ok) {
        throw new Error(`renders/index.json returned HTTP ${response.status}; run addon/build_renders.py`);
    }
    return response.json();
}

/** Replace the options of `select` with `{ value, label }` pairs and enable it. */
function fillSelect(select, options) {
    select.replaceChildren(...options.map(({ value, label }) => new Option(label, value)));
    select.disabled = false;
}

function selectedSample(catalog) {
    return catalog.samples.find((sample) => sample.id === ui.sample.value);
}

function selectedFormat(catalog) {
    return catalog.formats.find((format) => format.key === ui.format.value);
}

/** Point `img` at a bundled render and describe it. */
function showImage(img, file, width, height, alt) {
    // Intrinsic size attributes reserve the right aspect ratio before the PNG arrives.
    img.width = width;
    img.height = height;
    img.alt = alt;
    img.src = `renders/${file}`;
}

/** Show the selected sample's source beside its render for the selected format. */
function showSelection(catalog) {
    const sample = selectedSample(catalog);
    const format = selectedFormat(catalog);
    showImage(ui.sourceImage, sample.source, sample.width, sample.height, `Source design of ${sample.label}`);
    showImage(ui.outputImage, sample.renders[format.key], format.width, format.height,
        `Engine output of ${sample.label} for ${format.name}, ${format.key}`);
    ui.sourceCaption.textContent = `Source, ${sample.width}x${sample.height}, ${sample.elements} elements`;
    ui.outputCaption.textContent = format.key === sample.target
        ? `Engine output, ${format.key}, official target`
        : `Engine output, ${format.key}`;
    ui.compare.dataset.layout = format.width / format.height > STACK_ABOVE_ASPECT ? "stacked" : "side-by-side";
    ui.compare.hidden = false;
}

function setStatus(message) {
    ui.status.textContent = message;
}

// =============================================================================
// Adobe Express bridge
// =============================================================================

/** Resolve with the ready UI SDK inside Express, or with `null` in a plain browser or if the SDK fails. */
async function connectToExpress() {
    // Express always hosts add-on panels in an iframe, so a top-level page is a plain browser preview.
    if (window.self === window.top) {
        setStatus("Preview only. Load this add-on in Adobe Express to add renders to a page.");
        return null;
    }
    setStatus("Connecting to Adobe Express...");
    const slow = setTimeout(() => setStatus("Still waiting for Adobe Express..."), SLOW_CONNECT_MS);
    try {
        // A dynamic import, unlike a static one, lets the previews run when the SDK cannot load. It
        // resolves to the instance the script element in `index.html` started, which is already
        // listening for the handshake.
        const { default: addOnUISdk } = await import(SDK_URL);
        await addOnUISdk.ready;
        setStatus("");
        return addOnUISdk;
    } catch (error) {
        console.warn("[viewer] The Adobe Express SDK did not load; previews still work. Reason:", error);
        setStatus("Could not reach Adobe Express, so Add to page is off.");
        return null;
    } finally {
        clearTimeout(slow);
    }
}

/** Keep the panel in the Express theme, which can switch between light and dark at any time. */
function followTheme(addOnUISdk) {
    const apply = (theme) => {
        document.documentElement.dataset.theme = theme;
    };
    apply(addOnUISdk.app.ui.theme);
    addOnUISdk.app.on("themechange", (data) => apply(data.theme));
}

/** Insert the selected render on the current Express page. */
async function addToPage(addOnUISdk, catalog) {
    const sample = selectedSample(catalog);
    const format = selectedFormat(catalog);
    ui.addButton.disabled = true;
    setStatus("Adding the render to the page...");
    try {
        const response = await fetch(`renders/${sample.renders[format.key]}`);
        if (!response.ok) {
            throw new Error(`the render returned HTTP ${response.status}`);
        }
        // Type the blob explicitly: `addImage` rejects unknown mime types, and not every server labels PNGs.
        const blob = new Blob([await response.arrayBuffer()], { type: "image/png" });
        await addOnUISdk.app.document.addImage(blob, { title: `${sample.label}, ${format.name} ${format.key}` });
        setStatus(`Added the ${format.name} render to the page.`);
    } catch (error) {
        console.warn("[viewer] addImage failed. Reason:", error);
        setStatus(`Could not add the render: ${error.message}`);
    } finally {
        ui.addButton.disabled = false;
    }
}

// =============================================================================
// Startup
// =============================================================================

/** Show the previews, then enable Add to page once Express is ready. */
async function main() {
    // Start the SDK import before the catalogue fetch, so Express connects about as early as with a static import.
    const express = connectToExpress();
    let catalog;
    try {
        catalog = await loadCatalog();
    } catch (error) {
        console.warn("[viewer] Could not load the render catalogue. Reason:", error);
        setStatus(`Could not load the previews: ${error.message}`);
        return;
    }
    fillSelect(ui.sample, catalog.samples.map((sample) => ({
        value: sample.id,
        label: `${sample.label}, ${sample.width}x${sample.height}`,
    })));
    fillSelect(ui.format, catalog.formats.map((format) => ({
        value: format.key,
        label: `${format.name}, ${format.key}`,
    })));
    // Open on the first sample at its official target, the resize the organizers ship with it.
    const firstTarget = catalog.samples[0].target;
    if (catalog.formats.some((format) => format.key === firstTarget)) {
        ui.format.value = firstTarget;
    }
    ui.sample.addEventListener("change", () => showSelection(catalog));
    ui.format.addEventListener("change", () => showSelection(catalog));
    showSelection(catalog);
    ui.provenance.textContent = `Stored renders from ${catalog.engine}, drawn by the official renderer `
        + "in the scoring Docker image. The panel does not run the engine.";

    const addOnUISdk = await express;
    if (addOnUISdk === null) {
        return;
    }
    followTheme(addOnUISdk);
    ui.addButton.addEventListener("click", () => addToPage(addOnUISdk, catalog));
    ui.addButton.disabled = false;
}

main();
