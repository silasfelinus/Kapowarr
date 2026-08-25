const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = p => fs.readFileSync(path.join(root, p), 'utf8');

const libraryImport = read('frontend/static/js/library_import.js');
const template = read('frontend/templates/library_import.html');
const backend = read('backend/features/library_import.py');

test('the import POST reports progress while it is still in flight', () => {
	// The import runs inside the request, so the page has no other way to
	// tell "working" from "wedged" -- a rotating loading line looks the
	// same either way, and a user who gives up mid-import leaves half the
	// rows imported and half still held.
	assert.match(
		libraryImport,
		/socket\.on\(\s*'library_import_status'/,
		'the page must subscribe to per-entry import progress'
	);
	assert.match(
		backend,
		/LibraryImportStatusEvent\(/,
		'the import loop must emit that progress'
	);
});

test('progress has somewhere to render inside the loading view', () => {
	assert.match(template, /id="import-progress"/);
	assert.match(
		libraryImport,
		/progress: document\.querySelector\('#import-progress'\)/
	);
});

test('a socket that never arrived does not break the import', () => {
	assert.match(
		libraryImport,
		/typeof socket !== 'undefined' && socket/,
		'progress is decoration; the import must not depend on it'
	);
	assert.match(
		backend,
		/except Exception:\s*\n\s*LOGGER\.debug\(\s*\n?\s*'Could not emit library import progress'/,
		'a failing emit must not take the import down with it'
	);
});

test('the progress line is cleared on every way the import can end', () => {
	const importLibrary = libraryImport
		.split('function importLibrary')[1]
		.split('function showImportProgress')[0];

	const clears = importLibrary.match(/hideImportProgress\(\)/g) || [];
	assert.ok(
		clears.length >= 3,
		'success, partial-failure and error all have to clear it, '
		+ `found ${clears.length}`
	);
	assert.match(
		importLibrary,
		/\.catch\(e => \{\s*\n\s*hideImportProgress\(\);/,
		'a thrown import must not leave a stale progress line on screen'
	);
});
