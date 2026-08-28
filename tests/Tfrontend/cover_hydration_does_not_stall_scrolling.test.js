const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const gallery = fs.readFileSync(
	path.join(__dirname, '../../frontend/static/js/volumes_gallery.js'),
	'utf8'
);
const volumesCss = fs.readFileSync(
	path.join(__dirname, '../../frontend/static/css/volumes.css'),
	'utf8'
);

// The poster gallery materializes a card for every volume in the library
// up front -- that is the deliberate trade the module documents. It means
// any forced layout read is a full-document layout, and the cover
// observer did one per image, inside a single callback carrying every
// image a fling swept through the overscan band.

test('cover hydration never measures the DOM itself', () => {
	const hydration = gallery.slice(
		gallery.indexOf('function loadCover'),
		gallery.indexOf('function buildPosterShell')
	);
	assert.notEqual(hydration.length, 0, 'loadCover not found');
	assert.doesNotMatch(
		hydration,
		/getBoundingClientRect\(\)/,
		'measuring here forces a layout flush per image on the scroll path'
	);
	assert.match(
		hydration,
		/entry\.boundingClientRect/,
		'the observer has already measured this off the critical path'
	);
});

// The observer callback, from the constructor call to the options object
// that follows it. Sliced by structure rather than by the first occurrence
// of a word, which a comment above the function can quietly claim.
const observerCallback = (() => {
	const start = gallery.indexOf('new IntersectionObserver');
	assert.notEqual(start, -1, 'observer not found');
	const end = gallery.indexOf('root: coverObserverRoot()', start);
	assert.notEqual(end, -1, 'observer options not found');
	return gallery.slice(start, end);
})();

test('the root is the element that actually scrolls', () => {
	// `general.css` gives `main > *:not(.tool-bar-container)` its own
	// `overflow-y: auto`, so `#library-container` scrolls and the document
	// behind it does not.
	//
	// An observer on the default root is measured against the viewport, and
	// `rootMargin` grows the viewport rect -- but the intersection is still
	// clipped by every overflow ancestor, and nothing grows those. So the
	// overscan band below the scroller was worth zero pixels: a cover was
	// requested only once its card was literally on screen, one row at a
	// time, which is what a fling actually felt like.
	assert.match(gallery, /root: coverObserverRoot\(\)/);
	assert.match(
		gallery,
		/const container = library_els\.pages\.view;/,
		'the root must be the scrolling container, not the document'
	);
});

test('the visible band is recovered from the band the observer reports', () => {
	// `rootBounds` arrives with the overscan margin already added, so it
	// describes the whole band. Using it as-is would call every card in the
	// band visible and hand them all the same priority.
	assert.match(gallery, /root_bounds\.top \+ COVER_OVERSCAN_PX/);
	assert.match(gallery, /root_bounds\.bottom - COVER_OVERSCAN_PX/);
});

test('the root is read once per batch, not once per image', () => {
	// The property access, not the prose about it.
	const reads = observerCallback.match(/\.rootBounds/g) || [];
	assert.equal(
		reads.length, 1,
		'the root measurement belongs outside the per-entry loop'
	);
	assert.ok(
		observerCallback.indexOf('.rootBounds')
			< observerCallback.indexOf('entries.forEach'),
		'the root measurement must be hoisted above the loop'
	);
	assert.doesNotMatch(
		observerCallback,
		/getBoundingClientRect/,
		'measuring the root here would forfeit what rootBounds is for'
	);
});

test('a cover still gets a priority hint', () => {
	// The point of measuring at all: something actually on screen should
	// outrank something merely inside the overscan band.
	const hydration = gallery.slice(
		gallery.indexOf('function loadCover'),
		gallery.indexOf('function coverObserverRoot')
	);
	assert.match(hydration, /fetchPriority/);
	assert.match(hydration, /'high'/);
	assert.match(hydration, /'low'/);
});

test('poster cards reserve their space before the cover arrives', () => {
	// Why loading a cover does not reflow the grid, and so why the
	// forced reads above were the whole cost rather than half of it.
	const rule = volumesCss.slice(
		volumesCss.indexOf('.list-img {'),
		volumesCss.indexOf('.list-img:not([src])')
	);
	assert.match(rule, /aspect-ratio:\s*2\/3/);
	assert.match(rule, /width:\s*100%/);
});
