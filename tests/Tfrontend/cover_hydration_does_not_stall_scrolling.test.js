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

test('the viewport is read once per batch, not once per image', () => {
	const callback = gallery.slice(
		gallery.indexOf('new IntersectionObserver'),
		gallery.indexOf('rootMargin')
	);
	const reads = callback.match(/window\.innerHeight/g) || [];
	assert.equal(
		reads.length, 1,
		'innerHeight belongs outside the per-entry loop'
	);
	assert.ok(
		callback.indexOf('window.innerHeight') < callback.indexOf('forEach'),
		'the viewport read must be hoisted above the loop'
	);
});

test('a cover still gets a priority hint', () => {
	// The point of measuring at all: something actually on screen should
	// outrank something merely inside the overscan band.
	const hydration = gallery.slice(
		gallery.indexOf('function loadCover'),
		gallery.indexOf('function observeCovers')
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
