const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');

function read(relative) {
	return fs.readFileSync(path.join(root, relative), 'utf8');
}

const template = read('frontend/templates/pull_list.html');
const script = read('frontend/static/js/pull_list.js');
const statusRoutes = read('frontend/pull_list_status.py');

function block(name) {
	const marker = `{% block ${name} %}`;
	const start = template.indexOf(marker);
	assert.notEqual(start, -1, `missing ${name} block`);
	const rest = template.slice(start + marker.length);
	return rest.slice(0, rest.indexOf('{% endblock %}'));
}

test('publisher rule prototype lives in the generic pre-build container', () => {
	const generic = block('pre_build_els');
	const rows = block('pre_build_rows');

	assert.match(generic, /class="publisher-rule"/);
	assert.doesNotMatch(rows, /class="publisher-rule"/);
	assert.match(rows, /class="pull-list-entry"/);
});

test('pull list guards missing prototypes before cloneNode', () => {
	assert.match(
		script,
		/if \(!PullListEls\.rule\)\s*\n\s*throw new Error\('Publisher rule template is missing from the DOM'\)/
	);
	assert.match(
		script,
		/if \(!PullListEls\.entry\)\s*\n\s*throw new Error\('Pull List row template is missing from the DOM'\)/
	);
});

test('browser-side pull-list failures are reported to the server log', () => {
	assert.match(script, /function reportPullListClientError\(/);
	assert.match(script, /sendAPI\('POST', '\/pulllist\/client-error'/);
	assert.match(script, /reportPullListClientError\(api_key, 'post-refresh reload', error\)/);
	assert.match(statusRoutes, /@api\.route\('\/pulllist\/client-error', methods=\['POST'\]\)/);
	assert.match(statusRoutes, /LOGGER\.error\(/);
	assert.match(statusRoutes, /Pull List client error/);
});
