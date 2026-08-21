const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const status = fs.readFileSync(
	path.join(root, 'frontend/templates/status.html'),
	'utf8'
);
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/status.js'),
	'utf8'
);
const api = fs.readFileSync(path.join(root, 'frontend/api.py'), 'utf8');
const ui = fs.readFileSync(path.join(root, 'frontend/ui.py'), 'utf8');

// Events re-presented task history, download history and log warnings as one
// timeline. All three already have a page of their own -- System > Tasks,
// Activity > History and System > Logs -- so it was a fourth place to read the
// same evidence, and the one that fed off the log file made the real log
// viewer look redundant while being harder to search.
test('the events page is gone rather than duplicating three other surfaces', () => {
	assert.ok(!fs.existsSync(path.join(root, 'frontend/events.py')));
	assert.ok(!fs.existsSync(path.join(root, 'frontend/templates/events.html')));
	assert.ok(!fs.existsSync(path.join(root, 'frontend/static/js/events.js')));
	assert.doesNotMatch(ui, /import frontend\.events/);
	assert.doesNotMatch(status, /\/system\/events/);
});

test('the counters events uniquely held survive on status', () => {
	// Everything else on that page existed elsewhere; these did not, so they
	// move rather than being dropped with it.
	assert.match(api, /\/system\/comicvine-activity/);
	assert.match(api, /get_comicvine_operation_stats/);
	assert.match(status, /aria-label="ComicVine Activity"/);
	assert.match(status, /id="comicvine-rows"/);
	assert.match(script, /fillComicVineActivity\(api_key\)/);
});

test('operations are labelled as operations, not raw requests', () => {
	// One operation can batch more than one API call, so calling them requests
	// would misreport what the number means.
	assert.match(status, /not raw\s+request counts/i);
});
