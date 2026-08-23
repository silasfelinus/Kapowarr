const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/settings_download.html'),
	'utf8'
);
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/settings_download.js'),
	'utf8'
);

test('download settings expose minimum and maximum grab sizes', () => {
	assert.match(template, /id="minimum-grab-size-input"/);
	assert.match(template, /id="maximum-grab-size-input"/);
	assert.match(template, /default is 1 MiB/);
	assert.match(template, /default is 300 MiB/);
});

test('grab size controls allow zero to disable a limit', () => {
	const minimum = template.match(/<input[^>]+id="minimum-grab-size-input"[^>]*>/)[0];
	const maximum = template.match(/<input[^>]+id="maximum-grab-size-input"[^>]*>/)[0];
	assert.match(minimum, /min="0"/);
	assert.match(maximum, /min="0"/);
});

test('settings load and save both grab size fields through acquisition preferences', () => {
	assert.match(script, /acquisition\.minimum_grab_size_mb/);
	assert.match(script, /acquisition\.maximum_grab_size_mb/);
	assert.match(script, /'minimum_grab_size_mb'/);
	assert.match(script, /'maximum_grab_size_mb'/);
	assert.match(script, /sendAPI\('PUT', '\/settings\/acquisition'/);
});
