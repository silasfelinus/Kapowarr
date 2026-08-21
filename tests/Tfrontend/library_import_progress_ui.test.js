const test = require('node:test');
const assert = require('node:assert/strict');

const {parseContinuousWork} = require(
	'../../frontend/static/js/library_import_progress_ui.js'
);

test('title progress exposes work inside the current durable folder', () => {
	const parsed = parseContinuousWork(
		'Continuous import: 60/3720 folders checked · 29 volumes imported · '
		+ '34 need review · 3660 left · review holds: 1 weak · 33 no candidate · '
		+ 'content: matching title 5/132 · black cat'
	);

	assert.equal(parsed.checked, 60);
	assert.equal(parsed.total, 3720);
	assert.equal(parsed.folder_index, 61);
	assert.equal(parsed.folder, 'content');
	assert.equal(parsed.phase, 'matching');
	assert.equal(parsed.current, 5);
	assert.equal(parsed.phase_total, 132);
	assert.equal(parsed.title, 'black cat');
	assert.equal(parsed.cooldown, false);
});

test('volume import progress is represented separately from folder completion', () => {
	const parsed = parseContinuousWork(
		'Continuous import: 60/3720 folders checked · 29 volumes imported · '
		+ '34 need review · 3660 left · content: importing volume 7/18'
	);

	assert.equal(parsed.folder_index, 61);
	assert.equal(parsed.folder, 'content');
	assert.equal(parsed.phase, 'importing');
	assert.equal(parsed.current, 7);
	assert.equal(parsed.phase_total, 18);
});

test('large-folder shared search reports the number of parsed titles', () => {
	const parsed = parseContinuousWork(
		'Continuous import: 60/3720 folders checked · 29 volumes imported · '
		+ '34 need review · 3660 left · content: shared search for 132 parsed titles'
	);

	assert.equal(parsed.folder_index, 61);
	assert.equal(parsed.folder, 'content');
	assert.equal(parsed.phase, 'shared-search');
	assert.equal(parsed.current, 0);
	assert.equal(parsed.phase_total, 132);
});

test('ComicVine cooldown remains visible as current-folder work', () => {
	const parsed = parseContinuousWork(
		'Continuous import: 60/3720 folders checked · 29 volumes imported · '
		+ '34 need review · 3660 left · '
		+ 'ComicVine rate limit reached; cooling down for 15 minutes'
	);

	assert.equal(parsed.folder_index, 61);
	assert.equal(parsed.phase, null);
	assert.equal(parsed.cooldown, true);
});

test('non-progress messages do not invent a current folder', () => {
	assert.equal(parseContinuousWork('Starting the longbox conveyor...'), null);
});
