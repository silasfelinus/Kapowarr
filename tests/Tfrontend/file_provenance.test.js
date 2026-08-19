const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const provenance = fs.readFileSync(
	path.join(root, 'backend/features/file_provenance.py'),
	'utf8'
);
const routes = fs.readFileSync(
	path.join(root, 'frontend/provenance.py'),
	'utf8'
);
const postProcessing = fs.readFileSync(
	path.join(root, 'backend/features/post_processing.py'),
	'utf8'
);

test('file provenance never persists raw acquisition URLs or magnets', () => {
	const schema = provenance.split('PROVENANCE_SCHEMA = """')[1].split('"""')[0];
	assert.doesNotMatch(schema, /download_link|pure_link|magnet|api_key/i);
	assert.match(schema, /source_type/);
	assert.match(schema, /source_name/);
	assert.match(schema, /release_title/);
	assert.match(schema, /acquired_at/);
});

test('provenance is available through authenticated read-only APIs', () => {
	assert.match(routes, /\/files\/<int:file_id>\/provenance/);
	assert.match(routes, /\/volumes\/<int:volume_id>\/file-provenance/);
	assert.match(routes, /@auth/);
	assert.doesNotMatch(routes, /methods=\['POST'|'PUT'|'DELETE'/);
});

test('successful post-processing records provenance after conversion', () => {
	assert.match(postProcessing, /convert_file,\n\s*record_download_file_provenance,\n\s*set_file_properties/);
	assert.match(postProcessing, /record_download_file_provenance,\n\s*set_file_properties,\n\s*reset_file_link/);
});
