const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const {
	setVolumeFolderSeriesToken,
	detectVolumeFolderSeriesToken
} = require(path.join(root, 'frontend/static/js/settings_volume_folder_title.js'));

const template = fs.readFileSync(
	path.join(root, 'frontend/templates/settings_mediamanagement.html'),
	'utf8'
);

test('media management loads the volume folder title-style helper', () => {
	assert.match(template, /settings_volume_folder_title\.js/);
});

test('volume folder title style can move a leading article to the end token', () => {
	assert.equal(
		setVolumeFolderSeriesToken('{series_name} ({year})', 'sort'),
		'{clean_series_name} ({year})'
	);
	assert.equal(
		detectVolumeFolderSeriesToken('{clean_series_name} ({year})'),
		'sort'
	);
});

test('volume folder title style can restore the canonical series-name token', () => {
	assert.equal(
		setVolumeFolderSeriesToken('{clean_series_name} ({year})', 'original'),
		'{series_name} ({year})'
	);
	assert.equal(
		detectVolumeFolderSeriesToken('{series_name} ({year})'),
		'original'
	);
});

test('custom naming formats stay untouched', () => {
	const custom = '{publisher}/{year}/Volume {volume_number}';
	assert.equal(setVolumeFolderSeriesToken(custom, 'sort'), custom);
	assert.equal(detectVolumeFolderSeriesToken(custom), 'custom');
});
