const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const template = read('frontend/templates/volumes.html');
const gate = read('frontend/static/js/volumes_gallery_gate.js');
const gallery = read('frontend/static/js/volumes_gallery.js');
const css = read('frontend/static/css/volumes.css');

test('the gallery renderer is installed before the Volumes API continuation can run', () => {
	const gateIndex = template.indexOf('volumes_gallery_gate.js');
	const volumesIndex = template.indexOf('volumes.js');
	const galleryIndex = template.indexOf('volumes_gallery.js');
	assert.ok(gateIndex >= 0 && volumesIndex >= 0 && galleryIndex >= 0);
	assert.ok(gateIndex < volumesIndex && volumesIndex < galleryIndex);
	assert.match(gate, /const volumes_gallery_using_api_key = usingApiKey/);
	assert.match(gate, /await volumes_gallery_ready/);
	assert.match(gate, /resolve_volumes_gallery_ready\(\)/);
});

// Covers used to be hydrated by an IntersectionObserver: every card in the
// library existed, and the observer chose which of thousands of images to
// actually request. Windowing removes the choice -- only the cards near the
// viewport exist at all, which is the same band the observer was picking
// out -- so a card that exists is one whose cover is wanted, and asking for
// it directly is both simpler and earlier than waiting for a callback to
// confirm what the window already decided.
test('a poster card asks for its cover, because it is in the window', () => {
	assert.match(gallery, /img\.src = `\$\{url_base\}\/api\/volumes\/\$\{volume\.id\}\/cover/);
	assert.match(gallery, /img\.loading = 'lazy'/);
	assert.match(gallery, /img\.decoding = 'async'/);

	assert.ok(
		// The construction, not the paragraph explaining why there is not one.
		!gallery.includes('new IntersectionObserver'),
		'the window already limits which covers exist to request'
	);
	assert.ok(
		!gallery.includes('buildCompletePosterIndex'),
		'the gallery no longer owns a second rendering path'
	);
});

test('the gallery only supplies the card, not the rendering', () => {
	// It used to replace `buildLibraryView` wholesale, which meant two
	// renderers to keep in step. Now it contributes the one thing a poster
	// card has that a table row does not: an image.
	assert.match(gallery, /view_builders\.list = function/);
	assert.ok(
		!gallery.includes('buildLibraryView = function'),
		'one renderer, two item builders'
	);
});

test('a card reserves its space before the cover arrives', () => {
	// Otherwise every cover that loads reflows the grid under the reader's
	// thumb, and the window\'s row height -- which the spacers are computed
	// from -- would move with it.
	assert.match(css, /aspect-ratio: 2\/3/);
	assert.match(css, /\.list-img \{[^}]*width: 100%/);
});

test('a poster card is the height it says it is', () => {
	// `content-visibility: auto` used to sit on `.list-entry`, from when the
	// grid held a shell for every volume. Windowing replaced that -- about a
	// hundred and thirty cards exist now -- and what it left behind was a
	// card laid out at `contain-intrinsic-size` (15rem) until it neared the
	// viewport and became its real 14rem. Every row that came into view
	// shrank by a rem and slid everything below it up, which is what the
	// spacers, the scrollbar and the alphabet rail were all computed from.
	// Declarations only -- the comment above the rule says both names.
	const declarations = css.replace(/\/\*[\s\S]*?\*\//g, '');
	assert.doesNotMatch(declarations, /content-visibility:\s*auto/);
	assert.doesNotMatch(declarations, /contain-intrinsic-size/);
});
