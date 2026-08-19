const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const quality = fs.readFileSync(
	path.join(root, 'backend/features/file_quality.py'),
	'utf8'
);
const routes = fs.readFileSync(
	path.join(root, 'frontend/provenance.py'),
	'utf8'
);

test('quality explanation is read-only and explicitly non-aggregate', () => {
	assert.match(routes, /\/volumes\/<int:volume_id>\/file-quality/);
	assert.match(routes, /methods=\['GET'\]/);
	assert.match(routes, /@auth/);
	assert.doesNotMatch(quality, /['"]score['"]\s*:/);
	assert.match(quality, /Unknown evidence\s+stays unknown/i);
});

test('quality traits use existing explicit preference systems', () => {
	assert.match(quality, /Settings\(\)\.sv\.format_preference/);
	assert.match(quality, /acquisition_source_preference/);
	assert.match(quality, /getcomics_quality_label/);
});

test('GetComics labels are not generalized to unrelated sources', () => {
	assert.match(quality, /source_type.*GetComics.*GetComics \(torrent\)/s);
	assert.match(quality, /explicit_quality = None/);
});
