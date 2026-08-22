const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/pull_list.js'), 'utf8'
);

test('releases without ComicVine ids remain actionable', () => {
	assert.doesNotMatch(script, /This release has no ComicVine series ID/);
	assert.match(script, /configured metadata providers/);
	assert.doesNotMatch(
		script,
		/entry\.volume_id === null && entry\.comicvine_volume_id === null\) \{\s*button\.disabled = true/
	);
});

test('auto-add failures are visible beside Not added', () => {
	assert.match(script, /Auto-add retry pending/);
	assert.match(script, /Auto-grab retry pending/);
	assert.match(script, /automation_action === 'auto_search'/);
	assert.match(script, /obj\.automation_message/);
});
