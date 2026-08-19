const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/settings_importlists.html'),
	'utf8'
);
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/settings_importlists.js'),
	'utf8'
);
const routes = fs.readFileSync(
	path.join(root, 'frontend/import_lists.py'),
	'utf8'
);
const metadataSettings = fs.readFileSync(
	path.join(root, 'frontend/templates/settings_metadata.html'),
	'utf8'
);

test('Import Lists exposes familiar Arr automatic-add controls', () => {
	assert.match(template, />Import Lists</);
	assert.match(template, /Remote CBL/);
	assert.match(template, /Automatic Add/);
	assert.match(template, /Root Folder/);
	assert.match(template, /Monitor added volumes/);
	assert.match(template, /Monitor new issues/);
	assert.match(template, /Search on add/);
});

test('Remote CBL explains exact identity and never implies fuzzy matching', () => {
	assert.match(template, /embedded ComicVine volume IDs only/i);
	assert.match(template, /title-only entries are never guessed/i);
});

test('Import List UI loads root folders and CRUD APIs', () => {
	assert.match(script, /fetchAPI\('\/rootfolder'/);
	assert.match(script, /fetchAPI\('\/importlists'/);
	assert.match(script, /\/importlists\/\$\{definition\.id\}\/sync/);
	assert.match(script, /cmd: 'import_list_sync'/);
	assert.match(routes, /ensure_import_list_interval/);
});

test('Import List rendering treats remote data as text', () => {
	assert.match(script, /url\.textContent = definition\.source_url/);
	assert.match(script, /error\.textContent = definition\.last_error/);
	assert.match(script, /note\.textContent = exclusion\.note/);
	assert.doesNotMatch(script, /innerHTML\s*=\s*definition\./);
});

test('Metadata settings makes Import Lists discoverable without sidebar churn', () => {
	assert.match(metadataSettings, /href="\{\{url_base\}\}\/settings\/importlists"/);
	assert.match(metadataSettings, />Manage Import Lists</);
});
