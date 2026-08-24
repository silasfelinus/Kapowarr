const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const volumes = fs.readFileSync(
	path.join(root, 'frontend/static/js/volumes.js'),
	'utf8'
);

test('large libraries render a bounded runway instead of draining every volume', () => {
	assert.match(volumes, /const LIBRARY_RENDER_BATCH_SIZE = 16;/);
	assert.match(volumes, /const LIBRARY_RENDER_AHEAD_PX = 1200;/);
	assert.match(volumes, /function maybeRenderLibraryMore\(api_key\)/);
	assert.match(
		volumes,
		/library_els\.stats\.footer\.getBoundingClientRect\(\)\.top[\s\S]*LIBRARY_RENDER_AHEAD_PX/
	);
	assert.match(volumes, /window\.addEventListener\(\s*'scroll'/);
	assert.match(volumes, /window\.addEventListener\(\s*'resize'/);

	const build = volumes
		.split('function buildLibraryView')[1]
		.split('function ensureLibraryViewBuilt')[0];
	assert.ok(
		!build.includes('offset < volumes.length'),
		'building a view must not recursively queue batches until the whole library exists in the DOM'
	);
	assert.ok(
		!build.includes('scheduleLibraryRender(renderBatch)'),
		'the old eager background-drain loop would keep mobile browsers busy after first paint'
	);
});

test('filtering or sorting bulk-clears the old gallery DOM', () => {
	const clear = volumes
		.split('function clearLibraryView')[1]
		.split('function viewHasMoreToRender')[0];

	assert.match(clear, /replaceChildren\(space_taker\)/);
	assert.match(clear, /library_els\.views\.table\.replaceChildren\(\)/);
	assert.ok(
		!clear.includes("querySelectorAll('.list-entry').forEach"),
		'removing thousands of poster cards one JS call at a time freezes while applying a filter'
	);
});

test('lazy table rows keep complete mass-edit selection semantics', () => {
	const buildTable = volumes
		.split('function buildTableEntry')[1]
		.split('const view_builders')[0];
	assert.match(buildTable, /selected_volume_ids\.has\(Number\(volume\.id\)\)/);
	assert.match(buildTable, /checkbox\.onchange/);
	assert.match(buildTable, /selected_volume_ids\.add\(volume_id\)/);
	assert.match(buildTable, /selected_volume_ids\.delete\(volume_id\)/);

	const action = volumes
		.split('function runAction')[1]
		.split('function updateLibraryDownloadStatuses')[0];
	assert.match(action, /const volume_ids = \[\.\.\.selected_volume_ids\];/);
	assert.ok(
		!action.includes("querySelectorAll(\n\t\t'input[type=\"checkbox\"]:checked'"),
		'mass edit must not depend on every selected volume having a rendered row'
	);
});

test('queue progress events update queue volumes, not the whole library', () => {
	const update = volumes
		.split('function updateLibraryDownloadStatuses')[1]
		.split('// code run on load')[0];

	assert.match(update, /changed_volume_ids\.forEach/);
	assert.ok(
		!update.includes('library_entries.forEach'),
		'a queue tick must not walk thousands of library entries'
	);

	const listener = volumes
		.split("'kapowarr:download-queue-changed'")[1]
		.split("window.addEventListener")[0];
	assert.match(listener, /updateLibraryDownloadStatuses\(e\.detail \|\| \[\]\)/);
});
