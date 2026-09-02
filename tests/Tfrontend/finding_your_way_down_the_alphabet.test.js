const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(
	path.join(__dirname, '../../frontend/static', name), 'utf8'
);

const source = read('js/volumes.js');
const css = read('css/volumes.css');
const markup = fs.readFileSync(
	path.join(__dirname, '../../frontend/templates/volumes.html'), 'utf8'
);

// The pure part, lifted out and run for real rather than read.
//
// Built here rather than in a vm context on purpose: a context is its own
// realm, so the arrays it returns are not this realm's arrays and every
// strict deep comparison fails on the prototype alone, whatever the values.
const alphabet = () => {
	const body = source.slice(
		source.indexOf('const ALPHABETICAL_SORTS'),
		source.indexOf('let alphabet_index')
	);
	assert.notEqual(body.length, 0, 'the alphabet helpers were not found');
	return new Function(body + `
		return {ALPHABETICAL_SORTS, ALPHABET_RAIL_MINIMUM, buildAlphabetIndex};
	`)();
};

const titled = (...titles) => titles.map(title => ({title: title}));

test('the rail says where each letter starts', () => {
	const {buildAlphabetIndex} = alphabet();

	const index = buildAlphabetIndex(
		titled('Aama', 'AC Annual', 'Batman', 'Catwoman', 'Craniacs'),
		v => v.title
	);

	assert.deepEqual(index, [
		{letter: 'A', index: 0},
		{letter: 'B', index: 2},
		{letter: 'C', index: 3}
	]);
});

test('a letter with nothing under it is not offered', () => {
	const {buildAlphabetIndex} = alphabet();

	const letters = buildAlphabetIndex(
		titled('Aama', 'Zorro'), v => v.title
	).map(e => e.letter);

	// Not twenty-six targets, twenty-four of which go nowhere.
	assert.deepEqual(letters, ['A', 'Z']);
});

test('numbers and punctuation share one bucket', () => {
	const {buildAlphabetIndex} = alphabet();

	const index = buildAlphabetIndex(
		titled('3Keys', '2000 AD', 'Aama'), v => v.title
	);

	assert.deepEqual(index, [{letter: '#', index: 0}, {letter: 'A', index: 2}]);
});

test('the index follows the library rather than the alphabet', () => {
	// The server sorts, and its collation, article handling and punctuation
	// are its business. A rail computed from an idea of alphabetical order
	// would point at the wrong rows the moment the two disagreed.
	const {buildAlphabetIndex} = alphabet();

	const index = buildAlphabetIndex(titled('Batman', 'Aama'), v => v.title);

	assert.deepEqual(index, [
		{letter: 'B', index: 0},
		{letter: 'A', index: 1}
	]);
});

test('case and leading space do not make a second letter', () => {
	const {buildAlphabetIndex} = alphabet();

	const letters = buildAlphabetIndex(
		titled('aama', 'AC Annual', '  Alias'), v => v.title
	).map(e => e.letter);

	assert.deepEqual(letters, ['A']);
});

test('a missing title is not a crash', () => {
	const {buildAlphabetIndex} = alphabet();

	const letters = buildAlphabetIndex(
		[{title: null}, {}, {title: 'Aama'}], v => v.title
	).map(e => e.letter);

	assert.deepEqual(letters, ['#', 'A']);
});

test('only the sorts that are actually alphabetical get a rail', () => {
	const {ALPHABETICAL_SORTS} = alphabet();

	// Under Year or Recently Added a letter says nothing about where
	// anything is, so offering one would be a lie.
	assert.deepEqual(
		Object.keys(ALPHABETICAL_SORTS).sort(), ['publisher', 'title']
	);
	assert.equal(ALPHABETICAL_SORTS.publisher({publisher: 'Marvel'}), 'Marvel');
});

test('the rail is drawn for the field its sort orders by', () => {
	const {buildAlphabetIndex, ALPHABETICAL_SORTS} = alphabet();

	const volumes = [
		{title: 'Zorro', publisher: 'Dynamite'},
		{title: 'Aama', publisher: 'Marvel'}
	];

	assert.deepEqual(
		buildAlphabetIndex(volumes, ALPHABETICAL_SORTS.publisher)
			.map(e => e.letter),
		['D', 'M']
	);
});

test('a short library gets no rail at all', () => {
	const {ALPHABET_RAIL_MINIMUM} = alphabet();

	// Nothing to jump past, and a rail over three volumes is furniture.
	assert.ok(ALPHABET_RAIL_MINIMUM >= 20, 'a rail needs a library to move');
});

test('jumping is computed, not found', () => {
	// The row for a letter almost certainly has no element -- that is what
	// windowing means -- so there is nothing to scrollIntoView. The spacers
	// make the scroll height real, and the same arithmetic that sizes them
	// gives the position.
	const jump = source.slice(
		source.indexOf('scrollToIndex(index) {'),
		source.indexOf('start(sample_end) {')
	);
	assert.notEqual(jump.length, 0, 'scrollToIndex not found');

	assert.doesNotMatch(jump, /scrollIntoView/);
	assert.match(jump, /target_row \* row_height/);
	// And it refuses rather than guessing before anything has been measured.
	assert.match(jump, /row_height <= 0/);
});

test('the drag is tracked by position on the rail, not by letter', () => {
	// A rail two characters wide loses a finger constantly. Reading the
	// proportion down the rail keeps the scrub alive when the pointer
	// wanders off the side of it, which on a phone it always does.
	const at = source.slice(
		source.indexOf('function letterAtPointer'),
		source.indexOf('function setupAlphabetRail')
	);
	assert.notEqual(at.length, 0, 'letterAtPointer not found');

	assert.match(at, /getBoundingClientRect/);
	assert.match(at, /proportion \* alphabet_index\.length/);
	// And it clamps, so a finger dragged past either end stays in bounds.
	assert.match(at, /Math\.min\(alphabet_index\.length - 1, Math\.max\(0,/);
});

test('the pointer is captured so a finger can leave the rail', () => {
	const setup = source.slice(
		source.indexOf('function setupAlphabetRail'),
		source.indexOf('// The live window')
	);

	assert.match(setup, /setPointerCapture/);
	assert.match(setup, /pointercancel/);
});

test('the letters stay buttons for anyone not dragging', () => {
	const setup = source.slice(
		source.indexOf('function setupAlphabetRail'),
		source.indexOf('// The live window')
	);

	assert.match(setup, /addEventListener\('click'/);
	assert.match(markup, /<nav id="alphabet-rail"/);
	assert.match(markup, /aria-label="Jump to a letter"/);
});

test('the rail does not swallow the page scroll under a finger', () => {
	const rule = css.slice(
		css.indexOf('#alphabet-rail {'),
		css.indexOf('#alphabet-letters {')
	);
	assert.notEqual(rule.length, 0, '#alphabet-rail rule not found');

	assert.match(rule, /touch-action:\s*none/);
	// Reaching further left than it looks, because a thumb is wider than
	// two characters.
	assert.match(rule, /padding:.*1\.25rem/);
});

test('mass edit takes the rail away', () => {
	// It hands scrolling to the inner table and covers that edge with its
	// action bar, so the rail would point at a scroller it is not driving.
	assert.match(css, /main:has\(#massedit-toggle:checked\) #alphabet-rail/);
});
