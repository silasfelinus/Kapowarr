const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const libraryImport = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import.js'),
	'utf8'
);
const statusRoute = fs.readFileSync(
	path.join(root, 'frontend/library_import_status.py'),
	'utf8'
);
const importState = fs.readFileSync(
	path.join(root, 'backend/features/library_import_state.py'),
	'utf8'
);

function functionBody(source, name, nextName) {
	return source
		.split(`function ${name}`)[1]
		.split(`function ${nextName}`)[0];
}

test('the review queue is read from the durable job, not the task queue', () => {
	const refresh = functionBody(
		libraryImport,
		'refreshContinuousReviewCache',
		'openContinuousReview'
	);

	assert.ok(
		!/system\/tasks\/\$\{task_id\}/.test(refresh),
		'A task is dropped from the queue the moment it finishes, so its details '
		+ 'endpoint can only answer while a pass happens to be in flight'
	);
	assert.match(
		refresh,
		/fetchContinuousSnapshot\(api_key, true\)/,
		'Opening the review list is the moment that needs the held rows'
	);
});

test('the snapshot fetch targets the durable endpoint', () => {
	const fetchSnapshot = functionBody(
		libraryImport,
		'fetchContinuousSnapshot',
		'applyContinuousSnapshot'
	);

	assert.match(
		fetchSnapshot,
		/fetchAPI\('\/libraryimport\/continuous', api_key, params\)/
	);
	assert.match(fetchSnapshot, /continuousReviewCache = snapshot\.review_items \|\| \[\]/);
	assert.match(
		fetchSnapshot,
		/renderContinuousReviewCount\(snapshot\.review_folders_outstanding/
	);
	// The backlog can be hundreds of folders; the poll only renders a count.
	assert.match(fetchSnapshot, /with_items \? \{\} : \{items: 0\}/);
});

test('the poll asks for counters only, never the whole backlog', () => {
	const poll = functionBody(
		libraryImport,
		'pollContinuousTask',
		'showSavedContinuousState'
	);
	const periodic = poll.split('continuousLastSnapshotAt >= 15000')[1].split('return;')[0];

	assert.match(periodic, /fetchContinuousSnapshot\(api_key\)/);
	assert.ok(
		!periodic.includes('true'),
		'A 15s poll must not ship every held row each time'
	);
});

test('a saved pass is shown when the page opens with nothing running', () => {
	const load = libraryImport.split('// code run on load')[1];
	assert.match(
		load,
		/showSavedContinuousState\(api_key\)/,
		'A finished or interrupted pass leaves nothing in the task queue, so the '
		+ 'page has to ask for it explicitly'
	);

	const saved = functionBody(
		libraryImport,
		'showSavedContinuousState',
		'startContinuousImport'
	);
	assert.match(saved, /fetchContinuousSnapshot\(api_key\)/);
	assert.match(saved, /hide\(\[LIEls\.views\.start\], \[LIEls\.views\.continuous\]\)/);
	// The poll owns the panel whenever a pass is actually running.
	assert.match(saved, /snapshot\.task && snapshot\.task\.id !== undefined/);
});

test('the end of a pass reports the durable job, not in-page memory', () => {
	const poll = functionBody(
		libraryImport,
		'pollContinuousTask',
		'showSavedContinuousState'
	);

	assert.match(poll, /fetchContinuousSnapshot\(api_key\)/);
	assert.match(poll, /describeFinishedJob\(snapshot\.job\)/);
});

test('the review count is never parsed out of the task message', () => {
	const update = functionBody(
		libraryImport,
		'updateContinuousProgress',
		'fetchContinuousSnapshot'
	);

	assert.ok(
		!update.includes('renderContinuousReviewCount'),
		'Holds outlive the pass that produced them, so the running task\'s own '
		+ 'count is not the size of the backlog'
	);
	assert.match(update, /renderContinuousProgress\(/);
});

test('starting a new pass does not blank the existing backlog', () => {
	const start = functionBody(
		libraryImport,
		'startContinuousImport',
		'cancelProposalView'
	);

	assert.ok(
		!/continuousReviewCache = \[\]/.test(start),
		'Nothing imported the held folders, so they are still outstanding'
	);
	assert.ok(!/continuousReviewFolderCount = 0/.test(start));
});

test('the endpoint answers from durable state and reports any live task', () => {
	assert.match(statusRoute, /@api\.route\('\/libraryimport\/continuous', methods=\['GET'\]\)/);
	assert.match(statusRoute, /get_outstanding_review_items\(\)/);
	assert.match(statusRoute, /get_active_job\(\)/);
	assert.match(statusRoute, /'review_folders_outstanding'/);
	assert.match(statusRoute, /'task': _running_task\(\)/);

	// Review reconciliation can retire folders, so counters are read after it.
	const reviewIndex = statusRoute.indexOf('review_items = get_outstanding_review_items()');
	const jobIndex = statusRoute.indexOf('job = get_active_job()');
	assert.ok(reviewIndex >= 0 && jobIndex >= 0);
	assert.ok(reviewIndex < jobIndex);
});

test('active-job lookup falls back past running and paused', () => {
	const active = importState
		.split('def get_active_job()')[1]
		.split('\ndef ')[0];

	assert.match(active, /get_running_job\(\)/);
	assert.match(active, /get_paused_job\(\)/);
	assert.match(active, /get_latest_job\(\(JOB_RUNNING, JOB_PAUSED, JOB_COMPLETE\)\)/);
});

test('the cross-job review queue keeps folders whole and unduplicated', () => {
	const outstanding = importState
		.split('def get_outstanding_review_items()')[1]
		.split('\ndef ')[0];

	assert.match(outstanding, /ORDER BY job_id DESC, position/);
	assert.match(outstanding, /seen_folders/);
	assert.match(outstanding, /_prune_review_rows/);
});

test('a skipped volume leaves the review list instead of being re-offered', () => {
	// The backend skips a volume when there is nothing left to do with it --
	// most often because its files already moved. Re-checking those rows for
	// another attempt just invites the same no-op, and they can never clear.
	assert.match(libraryImport, /const resolved_paths = new Set\(\[/);
	assert.match(libraryImport, /if \(item && resolved_paths\.has\(item\.filepath\)\)\s*\n\s*row\.remove\(\)/);
	assert.match(
		libraryImport,
		/checkbox\.checked = failed_paths\.has\(item\.filepath\)/,
		'only genuine failures stay checked for a retry'
	);
});

test('the import summary counts skipped separately from failed', () => {
	assert.match(libraryImport, /\$\{skipped\.length\} skipped/);
	assert.match(libraryImport, /still need/);
});

test('the review list is built in batches instead of one blocking pass', () => {
	// A continuous pass holds whole folders, so this list is not the handful of
	// rows a manual proposal produces -- a few hundred held folders is several
	// thousand rows. Building them synchronously, appending each to the live
	// list, is a layout per row on the main thread.
	assert.match(libraryImport, /function scheduleProposalRender/);
	assert.match(libraryImport, /requestIdleCallback/);
	assert.match(libraryImport, /PROPOSAL_RENDER_BATCH_SIZE/);
	assert.match(
		libraryImport,
		/fragment\.appendChild\(row\)/,
		'rows are collected in a fragment, not appended to the live list'
	);
});

test('a stale render cannot append rows onto a newer list', () => {
	assert.match(libraryImport, /proposalRenderGeneration/);
	assert.match(
		libraryImport,
		/if \(generation !== proposalRenderGeneration\)\s*\n\s*return/
	);
});

test('a batch respects Select All rather than the template default', () => {
	assert.match(
		libraryImport,
		/row\.querySelector\('input\[type="checkbox"\]'\)\.checked = checked/
	);
});
