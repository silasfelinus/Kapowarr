// Cancel hid the dialog and nothing else.
//
// `closeWindow()` removes the `show-window` attribute. The metadata search
// the dialog started stayed on one of the browser's six connections to this
// host, and `searchInFlight` stayed set -- so reopening and asking the same
// question handed back the same stuck promise. From the page there was no
// way to start over (2026-09-05, Silas: "can't cancel").

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const search = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import_match_search.js'),
	'utf8'
);
const windowJs = fs.readFileSync(
	path.join(root, 'frontend/static/js/window.js'),
	'utf8'
);

test('the search keeps hold of the controller it created', () => {
	assert.match(search, /let searchGiveUp = null;/);
	assert.match(search, /searchGiveUp = giveUp;/);
});

test('abandoning aborts, and forgets the request was ever in flight', () => {
	const body = search.slice(search.indexOf('window.abandonMatchSearch'));
	assert.match(body, /searchGiveUp\.abort\(\)/);
	// All three, or the next identical query returns the dead promise.
	assert.match(body, /searchInFlight = null/);
	assert.match(body, /searchInFlightQuery = null/);
	assert.match(body, /searchGiveUp = null/);
});

test('abandoning an idle dialog is not an error', () => {
	const body = search.slice(search.indexOf('window.abandonMatchSearch'));
	assert.match(body, /if \(searchGiveUp === null\)\s*\n\s*return false;/);
});

test('the controller is released when a search finishes on its own', () => {
	// Otherwise closing the dialog later aborts a controller for a request
	// that already returned, which is harmless but leaves the flag lying.
	assert.match(search, /if \(searchGiveUp === giveUp\) searchGiveUp = null;/);
});

test('closing any window abandons the search, if that page has one', () => {
	const body = windowJs.slice(
		windowJs.indexOf('function closeWindow'),
		windowJs.indexOf('function closeWindow') + 800
	);
	assert.match(body, /typeof window\.abandonMatchSearch === 'function'/);
	assert.match(body, /window\.abandonMatchSearch\(\)/);
	// And it still closes.
	assert.match(body, /removeAttribute\('show-window'\)/);
});

test('a page with no search of its own still closes cleanly', () => {
	let hidden = false;
	const closeWindow = new Function(
		'window', 'document',
		windowJs.slice(
			windowJs.indexOf('function closeWindow'),
			windowJs.indexOf('};', windowJs.indexOf('function closeWindow')) + 2
		) + '\nreturn closeWindow;'
	)(
		{},   // no abandonMatchSearch on this page
		{ querySelector: () => ({ removeAttribute: () => { hidden = true; } }) }
	);

	assert.doesNotThrow(() => closeWindow());
	assert.ok(hidden, 'the window is still hidden');
});
