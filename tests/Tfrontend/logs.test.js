const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/logs.html'),
	'utf8'
);
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/logs.js'),
	'utf8'
);
const status = fs.readFileSync(
	path.join(root, 'frontend/templates/status.html'),
	'utf8'
);
const base = fs.readFileSync(
	path.join(root, 'frontend/templates/base.html'),
	'utf8'
);
const css = fs.readFileSync(
	path.join(root, 'frontend/static/css/logs.css'),
	'utf8'
);

test('logs page separates capture granularity from view granularity', () => {
	assert.match(template, /id="log-capture-level"/);
	assert.match(template, /id="log-view-level"/);
	assert.match(template, /Debug \+/);
	assert.match(template, /Errors \+/);
});

test('logs viewer renders log content as text instead of injecting markup', () => {
	assert.match(script, /body\.textContent = details/);
	assert.match(script, /label\.textContent = summary/);
	assert.doesNotMatch(script, /innerHTML\s*=\s*(entry|summary|details)/);
});

test('a stack trace collapses behind its summary line', () => {
	// One log line and one stack trace are read differently. Every entry
	// rendered in full turned a page with a few exceptions into a wall of
	// frames with the sequence of events buried in it.
	assert.match(script, /function splitLogMessage/);
	assert.match(script, /classList\.add\('log-details', 'hidden'\)/);
	assert.match(script, /setAttribute\('aria-expanded', 'false'\)/);
	assert.match(script, /more line/);
});

test('a single-line entry gets no expander', () => {
	assert.match(script, /if \(!details\) \{/);
});

test('levels are rendered as badges the eye can sort on', () => {
	assert.match(script, /classList\.add\('log-level-badge'\)/);
	assert.match(css, /\.log-error \.log-level-badge/);
	assert.match(css, /\.log-warning \.log-level-badge/);
});

test('logs page changes capture level through the existing settings API', () => {
	assert.match(script, /sendAPI\('PUT', '\/settings'/);
	assert.match(script, /log_level: next_level/);
});

test('system status exposes logs as a visible system surface', () => {
	assert.match(status, /aria-label="Logs"/);
	assert.match(status, /href="\{\{url_base\}\}\/system\/logs"/);
	assert.match(status, />View Logs<\/a>/);
});

test('logs are reachable from the system navigation, not only from status', () => {
	// The page existed and was only linked from inside Status, so finding it
	// meant knowing it was there.
	assert.match(base, /href="\{\{url_base\}\}\/system\/logs"/);
	assert.match(base, />Logs<\/a>/);
	assert.match(
		base,
		/request\.path == '\/system\/logs' %\}current-nav/,
		'the nav entry has to mark itself current on the logs page'
	);
});
