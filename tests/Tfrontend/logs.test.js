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
const nav = fs.readFileSync(
	path.join(root, 'frontend/templates/base.html'),
	'utf8'
);

test('logs page separates capture granularity from view granularity', () => {
	assert.match(template, /id="log-capture-level"/);
	assert.match(template, /id="log-view-level"/);
	assert.match(template, /Debug \+/);
	assert.match(template, /Errors \+/);
});

test('logs viewer renders log content as text instead of injecting markup', () => {
	assert.match(script, /pre\.textContent = entry\.message/);
	assert.doesNotMatch(script, /innerHTML = entry\.message/);
});

test('logs page changes capture level through the existing settings API', () => {
	assert.match(script, /sendAPI\('PUT', '\/settings'/);
	assert.match(script, /log_level: next_level/);
});

test('system navigation exposes logs as a first-class surface', () => {
	assert.match(nav, /href="\{\{url_base\}\}\/system\/logs"/);
	assert.match(nav, />Logs<\/a>/);
});
