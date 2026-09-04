const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
	path.join(__dirname, '../../frontend/static/js/library_import_match_search.js'),
	'utf8'
);

const seriesFrom = () => {
	const body = source.slice(
		source.indexOf('const RELEASE_TAGS'),
		source.indexOf('function rowSearchQuery')
	);
	assert.notEqual(body.length, 0, 'the query helpers were not found');
	return new Function(body + 'return seriesFrom;')();
};

test('opening the dialog does not search on its own', () => {
	// Silas: "we should kill whatever is auto-searching, it would be better
	// to edit first anyway." Opening it fired a query for the whole release
	// filename -- the worst question available and the most expensive one to
	// ask -- and it had to finish before the field could usefully be edited.
	const open = source.slice(
		source.indexOf('window.openEditCVMatch'),
		source.indexOf('window.buildProposalRow')
	);
	assert.notEqual(open.length, 0, 'openEditCVMatch not found');

	assert.doesNotMatch(open, /searchCV\(\)/);
});

test('the guess is offered ready to replace or accept', () => {
	const open = source.slice(
		source.indexOf('window.openEditCVMatch'),
		source.indexOf('window.buildProposalRow')
	);

	assert.match(open, /rowSearchQuery\(rowid\)/);
	// Selected, so replacing it is one keystroke and keeping it is none.
	assert.match(open, /input\.select\(\)/);
	assert.match(open, /Edit the title if you need to, then search/);
});

test('a release filename is offered as its series', () => {
	const clean = seriesFrom();

	assert.equal(
		clean('Amnesiac 001 (2025) (ADULT) (Digital) (Deluxe) (ASO) (Blue Orchid)'),
		'Amnesiac'
	);
	assert.equal(
		clean('Wonder Woman 033 (2026) (digital) (Son of Ultron-Empire)'),
		'Wonder Woman'
	);
	assert.equal(clean('Crimson After Hours 02'), 'Crimson After Hours');
});

test('a title that is a number keeps it', () => {
	// "2000 AD 1234" is issue 1234 of 2000 AD, not issue 2000 of AD 1234.
	assert.equal(seriesFrom()('2000 AD 1234'), '2000 AD');
});

test('a cover date goes whole rather than in half', () => {
	// Taking the trailing number first left "Strange Adventures, 1950-06-"
	// with a hanging hyphen.
	assert.equal(
		seriesFrom()('Strange Adventures, 1950-06-00 (#01)'),
		'Strange Adventures'
	);
});

test('a decimal issue number goes too', () => {
	assert.equal(seriesFrom()('Fantastic Four 1.5'), 'Fantastic Four');
});

test('a name with nothing to strip is left alone', () => {
	const clean = seriesFrom();

	assert.equal(clean('Batman'), 'Batman');
	assert.equal(
		clean('The Batman & Scooby-Doo Mysteries 02 (of 12) (2023) (digital)'),
		'The Batman & Scooby-Doo Mysteries'
	);
});

test('stripping everything means the guess was wrong', () => {
	// Better to hand back the whole thing to edit than an empty field.
	const clean = seriesFrom();

	assert.equal(clean('(2025) (Digital)'), '(2025) (Digital)');
	assert.equal(clean(''), '');
	assert.equal(clean(null), '');
});


test('a search that never comes back is abandoned by the browser too', () => {
	// A browser opens about six connections per host, and a search that
	// never resolves keeps one of them. Enough of those and every other
	// page of Kapowarr is unreachable from that browser while other sites
	// are fine -- which is what the "freeze" turned out to be.
	const search = source.slice(
		source.indexOf('window.searchCV'),
		source.indexOf('window.openEditCVMatch')
	);

	assert.match(search, /new AbortController\(\)/);
	assert.match(search, /signal: giveUp\.signal/);
	// Cleared when the search finishes, so a slow one is not cut off by a
	// timer left over from the last.
	assert.match(search, /clearTimeout\(abandon\)/);
});

test('the browser waits longer than the server does', () => {
	// Otherwise a search that is merely slow gets abandoned just before it
	// would have succeeded.
	const budget = Number(
		/SEARCH_GIVE_UP_MS = (\d+)/.exec(source)[1]
	);

	// The server allows each provider twenty seconds.
	assert.ok(budget > 20000, 'should outlast one provider timing out');
});

test('being abandoned is explained rather than reported as failure', () => {
	const describe = source.slice(
		source.indexOf('function describeSearchError'),
		source.indexOf('window.searchCV')
	);

	assert.match(describe, /AbortError/);
	assert.match(describe, /switch/);
});

test('fetchAPI can carry the signal', () => {
	const general = fs.readFileSync(
		path.join(__dirname, '../../frontend/static/js/general.js'), 'utf8'
	);
	const fn = general.slice(
		general.indexOf('async function fetchAPI'),
		general.indexOf('async function sendAPI')
	);

	assert.match(fn, /options=\{\}/);
	assert.match(fn, /\n\t\toptions\n\t\)/);
});
