const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const reviewSource = fs.readFileSync(
	path.join(__dirname, '../../frontend/static/js/library_import_review_ui.js'),
	'utf8'
);
const progressSource = fs.readFileSync(
	path.join(__dirname, '../../frontend/static/js/library_import_progress_ui.js'),
	'utf8'
);

function between(source, startMarker, endMarker) {
	const start = source.indexOf(startMarker);
	const end = source.indexOf(endMarker, start + startMarker.length);
	assert.notEqual(start, -1, `missing ${startMarker}`);
	assert.notEqual(end, -1, `missing ${endMarker}`);
	return source.slice(start, end);
};

test('Review Holds retry and full untracked rescan use different task commands', () => {
	const recheck = between(
		reviewSource,
		'function resetAndRecheckContinuousReview()',
		'function rescanUntrackedLibrary()'
	);
	const rescan = between(
		reviewSource,
		'function rescanUntrackedLibrary()',
		'function ensurePrimaryControls()'
	);

	assert.match(recheck, /command: 'recheck_continuous_library_import'/);
	assert.doesNotMatch(recheck, /rescan_continuous_library_import/);
	assert.match(recheck, /Other untracked folders will not be added to this pass/);

	assert.match(rescan, /command: 'rescan_continuous_library_import'/);
	assert.doesNotMatch(rescan, /command: 'recheck_continuous_library_import'/);
	assert.match(rescan, /much larger than the Review Holds backlog/);
});

test('start, background, and progress surfaces expose the two meanings clearly', () => {
	assert.match(reviewSource, /Reset & Re-evaluate Holds/);
	assert.match(reviewSource, /Rescan Untracked Library/);
	assert.match(reviewSource, /data-rescan-untracked-library/);
	assert.doesNotMatch(reviewSource, /Reset & Re-evaluate All Holds/);

	assert.match(progressSource, /continuous-recheck-button/);
	assert.match(progressSource, /continuous-rescan-button/);
	assert.match(progressSource, /Reset & Re-evaluate Holds/);
	assert.match(progressSource, /Rescan Untracked Library/);
});
