# Chameleonizer viewer add-on

FAQ Q5 lists a viewer add-on as a required, unscored part of the stage-one submission. This folder holds that add-on, an Adobe Express panel that shows how the engine resizes the three official samples and places a chosen result on the open Express page.

## What the panel does

The panel has two pickers. **Sample** lists the three designs in `input/`. **Format** lists six formats from `tools/catalog.py`, including each sample's official target. For the chosen pair, the panel shows the source render beside the engine's render. **Add to page** inserts the engine's render on the current Express page with `addOnUISdk.app.document.addImage`.

The panel does not run the engine. It shows PNGs stored in `renders/`, which `build_renders.py` made with the engine and the official renderer inside the scoring Docker image. `renders/index.json` lists them and records the engine version.

The add-on has no build step and no dependencies. Express loads `manifest.json`, `index.html`, `index.js`, `styles.css`, and `renders/`. The panel requests nothing outside its own folder except the Express UI SDK, which every add-on imports from `express.adobe.com`. In a plain browser the panel skips that import, shows the previews, and keeps **Add to page** turned off.

## Load the add-on in Adobe Express

Express loads a local add-on from the dev server in Adobe's add-on CLI. That server publishes an add-on listing at its root and serves the files under the manifest's `testId`. A plain static server does neither. These steps follow the CLI track of Adobe's Hello World guide, plus one copy step. You need Node.js 18 or later.

1. Turn on add-on development in Express, once per account. Click your avatar, click the gear icon to open **Settings**, and turn on **Add-on Development**. Accept the Developer Terms of Use if Express asks.
2. Create an add-on project from Adobe's no-build `javascript` template. If the CLI offers to set up an SSL certificate for `localhost`, accept.

	```bash
	npx @adobe/create-ccweb-add-on chameleonizer-viewer --template javascript
	```

3. From the repo root, copy the add-on into the project's `src/` folder. The copy replaces the template's `index.html`, `index.js`, and `manifest.json`.

	```bash
	cp -R addon/manifest.json addon/index.html addon/index.js addon/styles.css addon/renders chameleonizer-viewer/src/
	```

4. Build the project and start the dev server.

	```bash
	cd chameleonizer-viewer
	npm run build
	npm run start
	```

	The server prints `Done. Your add-on 'chameleonizer-viewer' is hosted on: https://localhost:5241/`.

5. In Express, open a document and click the **Add-ons** icon in the left rail.
6. Turn on the **Add-on Development** switch and click **Test your local add-on**.
7. In the **Connect to development server** dialog, select the checkbox and click **Connect**.
8. Click **Chameleonizer Viewer** in the add-ons list.

Chrome 142 and later asks whether Express may look for devices on your local network. Click **Allow**. If you click **Block**, Express cannot load a local add-on until you reset the permission, as Adobe's known issues page describes.

## Preview the panel in a plain browser

To check the panel without Express, serve this folder over HTTP from the repo root:

```bash
python3 -m http.server 8000 --directory addon
```

Then open `http://localhost:8000/`. Opening `index.html` from disk fails in Chrome, which blocks module scripts and `fetch` on `file://` pages.

## Regenerate the renders

After an engine change, rebuild the renders so that the panel shows the engine you submit. Run this from the repo root after you build the image with the `docker build` command in the root README.md:

```bash
docker run --rm --platform linux/amd64 --network none --entrypoint python \
	-v "$PWD/engine":/app/engine -v "$PWD/tools":/app/tools \
	-v "$PWD/input":/app/input:ro -v "$PWD/addon":/app/addon \
	my-chameleon-engine -m addon.build_renders --engine-label engine-v1.28
```

`build_renders.py` resizes each sample to every format in its `FORMATS` list with `engine.resize.resize`. It stops if an output breaks a hard rule that `tools.checks.structural` checks. Otherwise it writes the PNGs and `renders/index.json`, and it deletes any PNG that the new catalogue no longer lists.

The Level 3 sample references `solstice_holiday_badge_2025.png`, which the brand kit leaves out on purpose. The source render shows the grey placeholder that the official renderer draws for a missing asset. The engine's outputs shrink that image, set it to opacity 0 and place it behind the background, so no output render shows the box.

Known issue: in the Level 3 render at 300x250, the space in the button's "Summer Line" is narrow. v1.27 drew "SUNSETS ALE" and "Shop the SummerLine" there, and v1.28 draws both spaces in place.

## What has been checked

`tests/test_addon.py` checks `manifest.json` against the manifest version 2 rules that Adobe's CLI applies to a developer add-on, and checks that every render in `renders/index.json` exists at its listed size. It runs with the rest of `tests/`.

These checks ran once, in a Chromium browser:

- At the 320px panel width, a plain browser shows the previews and keeps **Add to page** off.
- Inside an iframe, the panel loads the SDK from `express.adobe.com`. When a content security policy blocks the SDK, the previews still load and the panel reports that **Add to page** is off.
- With a stand-in for the SDK, **Add to page** passes `addImage` an `image/png` blob of the chosen render, and the panel shows the error text when `addImage` rejects.

## What has not been verified

Nobody has loaded this add-on in Adobe Express yet. These parts are untested inside Express:

- Express connecting to the add-on through the CLI dev server.
- The SDK becoming ready after a dynamic `import()`. Adobe's examples use a static import. The panel uses a dynamic one so that the previews survive without the SDK.
- `addImage` placing the render on the page. Adobe's reference does not say where Express puts the image or how it scales it.
- The panel following the Express light and dark themes through `addOnUISdk.app.ui.theme` and the `themechange` event.

## Sources

- [Manifest schema reference](https://developer.adobe.com/express/add-ons/docs/references/manifest/)
- [`addOnUISdk.app.document` reference, with `addImage` and the image requirements](https://developer.adobe.com/express/add-ons/docs/references/addonsdk/app-document)
- [Use images](https://developer.adobe.com/express/add-ons/docs/guides/learn/how-to/use-images)
- [Hello, World!](https://developer.adobe.com/express/add-ons/docs/guides/getting-started/hello-world)
- [Development tools](https://developer.adobe.com/express/add-ons/docs/guides/getting-started/local-development/dev-tooling)
- [Add-on project anatomy](https://developer.adobe.com/express/add-ons/docs/guides/getting-started/addon-project-anatomy)
- [Known issues and limitations](https://developer.adobe.com/express/add-ons/docs/guides/getting-started/local-development/known-issues-limitations)
- [Theme and locale](https://developer.adobe.com/express/add-ons/docs/guides/learn/how-to/theme-locale)
- [UX guidelines for visual elements](https://developer.adobe.com/express/add-ons/docs/guides/build/design/ux-guidelines/visual-elements)
- [Adobe's add-on CLI source](https://github.com/adobe/create-ccweb-add-on), for the manifest schema in `packages/add-on-manifest/scripts/` and the dev server in `packages/wxp-scripts/src/app/ExpressServer.ts`
