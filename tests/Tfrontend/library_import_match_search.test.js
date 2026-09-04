const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const searchUi = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import_match_search.js'),
	'utf8'
);
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/library_import.html'),
	'utf8'
);

function functionBody(source, name, nextName) {
	return source
		.split(`window.${name} = function`)[1]
		.split(`window.${nextName} = function`)[0];
}

test('the Review Holds enhancer is loaded after the base review UI', () => {
	const review = template.indexOf('js/library_import_review_ui.js');
	const search = template.indexOf('js/library_import_match_search.js');
	assert.ok(review >= 0 && search > review);
});

test('opening Edit Match offers the proposed title without searching', () => {
	// It used to search on open. Silas, 2026-09-04: "we should kill whatever
	// is auto-searching, it would be better to edit first anyway." The query
	// it fired was the whole release filename -- the worst question
	// available and the most expensive one to ask -- and it had to finish
	// before the field could usefully be edited.
	const open = functionBody(searchUi, 'openEditCVMatch', 'buildProposalRow');
	assert.match(open, /LIEls\.search\.input\.value = rowSearchQuery\(rowid\)/);
	assert.doesNotMatch(open, /searchCV\(\)/);
});

test('the offered query prefers the existing proposed match', () => {
	assert.match(searchUi, /const proposed = stripResultYear\(row\?\.querySelector\('a'\)\?\.innerText\)/);
	// Falling back to the filename, with the release tags taken off it.
	assert.match(searchUi, /return proposed \|\| seriesFrom\(item\.file_title\) \|\| ''/);
});

test('an in-flight query cannot be duplicated by repeated submits', () => {
	const search = functionBody(searchUi, 'searchCV', 'openEditCVMatch');
	assert.match(search, /if \(searchInFlight !== null\)/);
	assert.match(search, /if \(searchInFlightQuery === query\)\s*\n\s*return searchInFlight/);
	assert.match(search, /setSearchBusy\(true\)/);
	assert.match(search, /setSearchBusy\(false\)/);
});

test('search always tells the user whether it found, missed, or failed', () => {
	const search = functionBody(searchUi, 'searchCV', 'openEditCVMatch');
	assert.match(search, /Searching metadata providers/);
	assert.match(search, /No matches found/);
	assert.match(search, /matches? found/);
	assert.match(search, /describeSearchError/);
});

test('manual selection preserves provider-neutral metadata identity', () => {
	assert.match(searchUi, /provider_id: providerId/);
	assert.match(searchUi, /external_id: externalId/);
	assert.match(searchUi, /item\.provider_id = identity\.provider_id/);
	assert.match(searchUi, /item\.external_id = identity\.external_id/);
});

test('Review Holds import submits provider id and external id', () => {
	const importer = searchUi.split('window.importLibrary = function')[1];
	assert.match(importer, /provider_id: item\.provider_id \|\| 'comicvine'/);
	assert.match(importer, /external_id: item\.external_id/);
	assert.match(importer, /id: item\.cv_id/);
	assert.match(
		importer,
		/item\.external_id !== null && item\.external_id !== undefined/,
		'a native Metron/GCD match must not be rejected just because comicvine_id is null'
	);
});
