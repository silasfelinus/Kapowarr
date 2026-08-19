const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/events.html'),
	'utf8'
);
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/events.js'),
	'utf8'
);
const status = fs.readFileSync(
	path.join(root, 'frontend/templates/status.html'),
	'utf8'
);
const route = fs.readFileSync(
	path.join(root, 'frontend/events.py'),
	'utf8'
);
const parity = fs.readFileSync(
	path.join(root, 'docs/ARR_PARITY.md'),
	'utf8'
);

test('events page exposes unified operational filters and ComicVine activity', () => {
	assert.match(template, /id="event-kind"/);
	assert.match(template, /id="event-level"/);
	assert.match(template, /id="event-search"/);
	assert.match(template, /ComicVine metadata activity/);
	assert.match(template, /bulk operation can contain more than one raw HTTP request/i);
});

test('events API reuses existing histories instead of creating another store', () => {
	assert.match(route, /get_task_history/);
	assert.match(route, /get_download_history/);
	assert.match(route, /parse_log_entries/);
	assert.match(route, /get_comicvine_operation_stats/);
	assert.doesNotMatch(route, /CREATE TABLE|INSERT INTO/i);
});

test('event rendering uses textContent rather than injecting event HTML', () => {
	assert.match(script, /title\.textContent = entry\.title/);
	assert.match(script, /message\.textContent = entry\.message/);
	assert.doesNotMatch(script, /innerHTML\s*=\s*entry\./);
});

test('events page supports refresh, auto refresh, and client-side filtering', () => {
	assert.match(script, /fetchAPI\('\/system\/events'/);
	assert.match(script, /setInterval\(refreshEvents, 10000\)/);
	assert.match(script, /eventMatchesFilters/);
});

test('system status exposes Events and parity marks the surface supported', () => {
	assert.match(status, /aria-label="Events"/);
	assert.match(status, /href="\{\{url_base\}\}\/system\/events"/);
	assert.match(status, />View Events<\/a>/);
	assert.match(parity, /\| Events \| Supported \|/);
});
