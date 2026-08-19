const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const libraryImport = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import.js'),
	'utf8'
);
const persistentImport = fs.readFileSync(
	path.join(root, 'backend/features/library_import_persistent.py'),
	'utf8'
);

function functionBody(source, name, nextName) {
	return source
		.split(`function ${name}`)[1]
		.split(`function ${nextName}`)[0];
}

test('Stop Import sends the cooperative stop before refreshing review details', () => {
	const stop = functionBody(
		libraryImport,
		'stopContinuousImport',
		'pollContinuousTask'
	);
	const deleteIndex = stop.indexOf("sendAPI('DELETE'");
	const snapshotIndex = stop.indexOf('refreshContinuousReviewCache');

	assert.ok(deleteIndex >= 0, 'Stop must send DELETE to the task endpoint');
	assert.ok(snapshotIndex >= 0, 'Stop may refresh the review snapshot afterward');
	assert.ok(
		deleteIndex < snapshotIndex,
		'Stop must not wait for the review snapshot before reaching the backend'
	);
});

test('review snapshot failure after stop acknowledgement is best effort only', () => {
	const stop = functionBody(
		libraryImport,
		'stopContinuousImport',
		'pollContinuousTask'
	);

	assert.match(
		stop,
		/refreshContinuousReviewCache[\s\S]*\.catch\(\(\) => continuousReviewCache\)/
	);
});

test('backend acknowledges stop and slices long metadata waits', () => {
	assert.match(persistentImport, /def request_stop\(self\)/);
	assert.match(persistentImport, /stop requested; pausing safely/);
	assert.match(persistentImport, /def _interruptible_wait\(self, seconds: float\)/);
	assert.match(persistentImport, /step = min\(1\.0, remaining\)/);
	assert.match(
		persistentImport,
		/remaining_delay and not self\._interruptible_wait\(remaining_delay\)/
	);
});
