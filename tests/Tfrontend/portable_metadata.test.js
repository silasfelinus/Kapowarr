const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const backend = fs.readFileSync(
	path.join(root, 'backend/features/portable_metadata.py'),
	'utf8'
);
const routes = fs.readFileSync(
	path.join(root, 'frontend/portable_metadata.py'),
	'utf8'
);
const ui = fs.readFileSync(
	path.join(root, 'frontend/ui.py'),
	'utf8'
);

test('portable series metadata exposes authenticated preview, write and download routes', () => {
	assert.match(routes, /portable-metadata\/series/);
	assert.match(routes, /portable-metadata\/series\/download/);
	assert.match(routes, /methods=\['GET'\]/);
	assert.match(routes, /methods=\['POST'\]/);
	assert.match(routes, /@auth/);
	assert.match(ui, /import frontend\.portable_metadata/);
});

test('write route defaults to preservation and validates explicit overwrite', () => {
	assert.match(routes, /data\.get\('overwrite', False\)/);
	assert.match(routes, /isinstance\(overwrite, bool\)/);
	assert.match(backend, /existing_preserved/);
	assert.match(backend, /O_EXCL/);
	assert.match(backend, /NamedTemporaryFile/);
	assert.match(backend, /replace\(temp_path, path\)/);
});

test('portable metadata never invents lifecycle or non-ComicVine identity', () => {
	assert.match(backend, /'status': 'Unknown'/);
	assert.match(backend, /'publication_run': None/);
	assert.match(backend, /external_ids\['comicvine'\]/);
	assert.doesNotMatch(backend, /file.*size.*quality|larger.*better/i);
});

test('successful materialization is fed back through the normal file scanner', () => {
	assert.match(backend, /scan_files\(volume_id, filepath_filter=\[path\]\)/);
});
