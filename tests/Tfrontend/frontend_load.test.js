const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');

function read(relative) {
	return fs.readFileSync(path.join(root, relative), 'utf8');
}

const base = read('frontend/templates/base.html');
const general = read('frontend/static/js/general.js');
const volumes = read('frontend/static/js/volumes.js');
const viewVolume = read('frontend/static/js/view_volume.js');

//
// Nothing on the critical path may depend on a third-party host
//
test('socket.io is served from Kapowarr itself, not a CDN', () => {
	const externalScripts = (base.match(/<script[^>]*src="https?:[^"]*"[^>]*>/g) || []);
	assert.deepEqual(
		externalScripts, [],
		'A <script> pointing at an external host stalls the whole page on a LAN '
		+ 'with no working route to the internet'
	);
	assert.match(base, /js\/vendor\/socket\.io\.min\.js/);
	assert.ok(
		fs.existsSync(path.join(root, 'frontend/static/js/vendor/socket.io.min.js')),
		'The vendored copy has to actually be in the repo'
	);
});

test('every script in the document head is deferred', () => {
	const scripts = base.match(/<script[^>]*src=[^>]*>/g) || [];
	assert.ok(scripts.length > 0);
	for (const tag of scripts)
		assert.ok(
			tag.includes('defer') || tag.includes('async'),
			`Parser-blocking script tag: ${tag}`
		);
});

test('the Google Fonts stylesheet does not block first paint', () => {
	// Loaded as a print stylesheet and promoted once it arrives, so an
	// unreachable fonts.googleapis.com costs nothing.
	assert.match(
		base,
		/href="https:\/\/fonts\.googleapis\.com[^"]*"[\s\S]{0,200}?media="print"/,
		'The font stylesheet must not be a render-blocking <link>'
	);
	assert.match(base, /onload="this\.media='all'/);
});

//
// The 900 KB SVG master is not an icon
//
test('no template points a browser at the full-size SVG artwork', () => {
	const templates = fs.readdirSync(path.join(root, 'frontend/templates'))
		.filter(name => name.endsWith('.html'));

	for (const name of templates) {
		const contents = read(`frontend/templates/${name}`);
		const references = contents
			.split('\n')
			.filter(line => line.includes('img/favicon.svg'));
		assert.deepEqual(
			references, [],
			`${name} still loads the SVG master: ${references.join(' | ')}`
		);
	}
});

test('the rendered PNG icons exist and are small', () => {
	for (const [file, limit] of [['icon-192.png', 60 * 1024], ['icon-32.png', 10 * 1024]]) {
		const full = path.join(root, 'frontend/static/img', file);
		assert.ok(fs.existsSync(full), `${file} is missing`);
		assert.ok(
			fs.statSync(full).size < limit,
			`${file} is ${fs.statSync(full).size} bytes, over the ${limit} budget`
		);
	}
});

test('coverless search results fall back to the small icon', () => {
	const addVolume = read('frontend/static/js/add_volume.js');
	assert.ok(!addVolume.includes('img/favicon.svg'));
	assert.match(addVolume, /entry\.dataset\.cover = result\.cover_link \|\| .*icon-192\.png/);
});

//
// Load order
//
test('local storage is set up before anything reads it', () => {
	const tail = general.split('// code run on load')[1];
	assert.ok(tail, 'general.js should still have a load section');

	const setup = tail.indexOf('setupLocalStorage()');
	const firstUse = tail.indexOf('usingApiKey()');

	assert.ok(setup >= 0 && firstUse >= 0);
	assert.ok(
		setup < firstUse,
		'usingApiKey() parses the `kapowarr` storage key, so calling it first '
		+ 'throws on a browser that has never opened Kapowarr before'
	);
});

//
// The library page builds one view, not two
//
test('the library renders only the view that is on screen', () => {
	assert.match(volumes, /function activeLibraryView\(\)/);
	// Mass edit shows the table whatever the view dropdown says.
	assert.match(volumes, /if \(inMassEdit\(\)\)\s*\n\s*return 'table';/);

	const populate = volumes.split('function populateLibrary')[1].split('function fetchLibrary')[0];
	assert.match(
		populate,
		/buildLibraryView\(\s*activeLibraryView\(\)/,
		'populateLibrary must build the active view only'
	);
});

test('switching view builds the missing view instead of refetching', () => {
	const handler = volumes
		.split('library_els.view_options.view.onchange')[1]
		.split('library_els.view_options.filter.onchange')[0];

	assert.match(handler, /ensureLibraryViewBuilt\(api_key\)/);
	assert.ok(
		!handler.includes('fetchLibrary'),
		'The payload is already in hand; a view switch should not hit the API'
	);
});

test('entering and leaving mass edit builds whichever view it lands on', () => {
	const massEdit = volumes
		.split('library_els.mass_edit.button.onclick')[1]
		.split('library_els.mass_edit.bar.querySelectorAll')[0];

	const calls = massEdit.match(/ensureLibraryViewBuilt\(api_key\)/g) || [];
	assert.ok(
		calls.length >= 3,
		'Needed on quit, and on both paths that turn mass edit on'
	);
});

test('per-volume updates are keyed, not searched for in the DOM', () => {
	assert.match(volumes, /const library_entries = new Map\(\)/);
	assert.match(volumes, /library_entries\.get\(data\.volume_id\)/);
	assert.ok(
		!volumes.includes('querySelector(`.vol-${id}`)'),
		'Looking an entry up by class scanned the whole library per event'
	);
});

test('entry state survives a view it has not built yet', () => {
	// Progress and monitored state live on the entry rather than being read back
	// out of the DOM, so the lazily built view can be filled in from them.
	assert.match(volumes, /getProgress\(\) \{\s*\n\s*return \[this\.downloaded_count, this\.total_count\];/);
	assert.match(volumes, /if \(this\.list_entry !== null\)/);
	assert.match(volumes, /if \(this\.table_entry !== null\)/);
});

//
// The volume page
//
test('issue rows are attached in one insert', () => {
	const fillTable = viewVolume.split('function fillTable')[1].split('function fillPage')[0];

	assert.match(fillTable, /createDocumentFragment\(\)/);
	assert.match(fillTable, /fragment\.appendChild\(entry\)/);
	assert.match(fillTable, /ViewEls\.issues_list\.appendChild\(fragment\)/);
	assert.ok(
		!/ViewEls\.issues_list\.appendChild\(entry\)/.test(fillTable),
		'Appending each row to the live list costs a layout pass per issue'
	);
});

test('the match table is built when its window is opened, not on page load', () => {
	const fillPage = viewVolume.split('function fillPage')[1].split('function toggleMonitored')[0];
	assert.ok(
		!fillPage.includes('fillIssueMatchTable('),
		'The match table is a second full copy of the issue list'
	);
	assert.match(fillPage, /issue_match_issues = data\.issues/);

	const showMatch = viewVolume.split('function showMatchIssue')[1].split('function setIssueMatchCheckboxes')[0];
	assert.match(showMatch, /ensureIssueMatchTable\(\)/);

	const ensure = viewVolume.split('function ensureIssueMatchTable')[1].split('function showMatchIssue')[0];
	assert.match(ensure, /if \(issue_match_table_built\)\s*\n\s*return;/);
	assert.match(ensure, /fillIssueMatchTable\(issue_match_issues\)/);
});

//
// The API side of a page load
//
test('covers are sent with a cache validator', () => {
	const api = read('frontend/api.py');
	const cover = api.split("@api.route('/volumes/<int:id>/cover'")[1].split('@api.route')[0];

	assert.match(cover, /if_none_match\.contains\(etag\)/);
	assert.match(cover, /make_response\('', 304\)/);
	assert.match(cover, /response\.set_etag\(etag\)/);
	assert.match(cover, /Cache-Control/);
});

test('the library listing does not carry per-volume descriptions', () => {
	const volumesPy = read('backend/implementations/volumes.py');
	const listing = volumesPy
		.split('def get_public_volumes')[1]
		.split('def search')[0];

	assert.ok(
		!/volume_number, description,/.test(listing),
		'description is the largest column on the table and the library view '
		+ 'never renders it'
	);
	assert.match(listing, /SELECT\s*\n\s*id, comicvine_id,/);
});
