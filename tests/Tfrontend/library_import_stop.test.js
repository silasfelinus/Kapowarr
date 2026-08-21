const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const statusRoute = fs.readFileSync(
	path.join(root, 'frontend/library_import_status.py'),
	'utf8'
);
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

test('Stop always reaches the backend, even with no task to delete', () => {
	// Stopping used to DELETE a task-queue entry, so it did nothing at all when
	// the queue had none -- which is exactly what an interrupted pass leaves
	// behind. No request, no message, and the panel still claiming a pass was
	// running.
	const stop = functionBody(
		libraryImport,
		'stopContinuousImport',
		'pollContinuousTask'
	);

	assert.doesNotMatch(
		stop,
		/if \(continuousTaskId === null\)\s*\n\s*return;/,
		'Stop must not bail out silently when no task is queued'
	);
	assert.match(stop, /sendAPI\('POST', '\/libraryimport\/continuous\/stop'/);
});

test('stopping a stalled job repaints from durable state immediately', () => {
	// Nothing is running, so there is no worker to wait for and no later poll
	// that would clear a lingering "Stopping...".
	const stop = functionBody(
		libraryImport,
		'stopContinuousImport',
		'pollContinuousTask'
	);

	assert.match(stop, /stopped === 'stalled_job'/);
	assert.match(stop, /paintContinuousStatus/);
});

test('the stop endpoint handles both a live task and a stalled job', () => {
	assert.match(
		statusRoute,
		/@api\.route\('\/libraryimport\/continuous\/stop', methods=\['POST'\]\)/
	);
	assert.match(statusRoute, /TaskHandler\(\)\.remove\(int\(task\['id'\]\)\)/);
	assert.match(statusRoute, /mark_job_paused\(int\(job\['id'\]\)\)/);
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

test('the control offers Resume when the pass is stopped, not a dead Stop', () => {
	// One button whose label never changed, disabled whenever there was
	// nothing to stop -- so a paused pass showed a button reading "Stop
	// Import" that could not be clicked, with no explanation and no way to
	// pick the pass back up from the panel saying it had stopped.
	const render = functionBody(
		libraryImport,
		'renderContinuousControl',
		'showContinuousTask'
	);

	assert.match(render, /'Resume Import'/);
	assert.match(render, /dataset\.action = 'resume'/);
	assert.match(
		render,
		/job\.remaining_folders > 0/,
		'a pass is resumable when it still has folders left'
	);
});

test('a finished pass hides the control instead of disabling it', () => {
	const render = functionBody(
		libraryImport,
		'renderContinuousControl',
		'showContinuousTask'
	);
	assert.match(render, /button\.classList\.add\('hidden'\)/);
});

test('a stalled job is still stoppable, since stopping is what pauses it', () => {
	const render = functionBody(
		libraryImport,
		'renderContinuousControl',
		'showContinuousTask'
	);
	assert.match(render, /live \|\| \(job && job\.is_stalled\)/);
});

test('the click dispatches on what the button currently offers', () => {
	assert.match(
		libraryImport,
		/dataset\.action === 'resume'\)\s*\n\s*startContinuousImport\(api_key\)/
	);
});
