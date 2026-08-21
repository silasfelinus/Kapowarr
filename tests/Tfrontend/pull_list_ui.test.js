const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/pull_list.js'), 'utf8'
);
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/pull_list.html'), 'utf8'
);

test('publisher filter shows counts for the displayed week only', () => {
	assert.match(
		script,
		/counts\[isoDate\(pullListState\.week\)\] \|\| 0/
	);
	assert.match(script, /if \(!release_count\)\s*return;/);
	assert.match(
		script,
		/`\$\{obj\.publisher\} \(\$\{release_count\}\)`/
	);
});

test('Check Now tracks its exact task and always exposes a failure path', () => {
	assert.match(script, /response => response\.json\(\)/);
	assert.match(script, /json\.result\.id/);
	assert.match(script, /entry\.id === task_id/);
	assert.match(script, /entry\.action === 'weekly_pull_list_check'/);
	assert.match(script, /\.catch\(error => \{/);
	assert.match(script, /stopCheckSpinner\(\)/);
	assert.match(script, /Queued behind \$\{running\.display_title\}/);
});

test('pull-list page has an inline status surface for refresh progress', () => {
	assert.match(
		template,
		/<p id="pull-list-check-status" class="hidden" role="status"><\/p>/
	);
	assert.match(script, /latestCheckFailure/);
	assert.match(script, /Check failed:/);
	assert.match(script, /Release calendar updated\./);
});
