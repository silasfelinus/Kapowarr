const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const volumes = fs.readFileSync(
	path.join(root, 'frontend/static/js/volumes.js'),
	'utf8'
);

// Both views build their complete skeleton in one pass. The library used
// to render 16 entries and wait for a scroll event before asking for 16
// more, which bought nothing -- a table row is text -- and made the view
// reachable only as far as a scroll listener happened to fire. When that
// listener was attached to an element that never scrolls, a
// 5480-volume library stopped in the A's.
//
// What is still deferred is the genuinely expensive part, and only the
// poster view has any: covers, hydrated around the viewport by
// volumes_gallery.js.
test('the library builds its whole skeleton rather than gating on scroll', () => {
	for (const gone of [
		'LIBRARY_RENDER_BATCH_SIZE',
		'LIBRARY_RENDER_AHEAD_PX',
		'maybeRenderLibraryMore',
		'scheduleLibraryRenderCheck',
		'scheduleNextLibraryBatch',
		'viewHasMoreToRender',
		'library_render_offsets'
	]) {
		assert.ok(
			!volumes.includes(gone),
			`${gone} gates the view on a scroll event; the skeleton is the whole set`
		);
	};

	// Nothing may re-introduce a partial view by listening for scrolls.
	assert.ok(
		!volumes.includes("'scroll'"),
		'a complete skeleton has nothing to top up on scroll'
	);

	const build = volumes
		.split('function buildLibraryView')[1]
		.split('function ensureLibraryViewBuilt')[0];
	assert.match(
		build,
		/renderLibraryEntries\(view, library_volumes, api_key\)/,
		'the build must lay down every volume, not a slice of them'
	);
	assert.ok(
		!build.includes('scheduleLibraryRender(renderBatch)'),
		'the old eager background-drain loop would keep mobile browsers busy after first paint'
	);

	// Still yielded once, so the loading state paints before the DOM work.
	assert.match(build, /scheduleLibraryPaint/);
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
