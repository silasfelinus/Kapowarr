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

test('Check Now uses the pull-list parallel lane instead of TaskHandler', () => {
	assert.match(script, /sendAPI\('POST', '\/pulllist\/check'/);
	assert.match(script, /fetchAPI\(`\/pulllist\/check\/\$\{check_id\}`/);
	assert.match(script, /json\.result\.id/);
	assert.doesNotMatch(
		script,
		/sendAPI\('POST', '\/system\/tasks'[^\n]*weekly_pull_list_check/
	);
	assert.doesNotMatch(script, /Queued behind/);
	assert.match(script, /stopCheckSpinner\(\)/);
});

test('Check Now fetches and stays on the displayed week', () => {
	assert.match(
		script,
		/const requested_week = isoDate\(pullListState\.week\);/
	);
	assert.match(
		script,
		/sendAPI\('POST', '\/pulllist\/check', api_key, \{\}, \{\s*week_start: requested_week\s*\}\)/
	);
	assert.match(script, /const check_week = check\.week_start \|\| requested_week;/);
	assert.match(script, /pullListState\.week = check_week/);
	assert.doesNotMatch(
		script,
		/pullListState\.week = startOfWeek\(new Date\(\)\);\s*loadList\(api_key, false\)/
	);
	assert.match(template, /Fetch releases for the displayed week/);
	assert.match(template, /walk\s+backward and deliberately fill historical gaps/);
});

test('empty current week falls back to the newest actually stored week', () => {
	assert.match(script, /fetchAPI\('\/pulllist\/weeks'/);
	assert.match(script, /fallback_to_stored/);
	assert.match(script, /pullListState\.stored_weeks\[0\]\.week_start/);
	assert.match(script, /showing the newest stored week/);
	assert.match(script, /Stored release data runs from/);
});

test('pull-list page has an inline status surface for refresh progress', () => {
	assert.match(
		template,
		/<p id="pull-list-check-status" class="hidden" role="status"><\/p>/
	);
	assert.match(script, /check\.status === 'failed'/);
	assert.match(script, /Release calendar check failed\./);
	assert.match(script, /Release calendar updated/);
});
