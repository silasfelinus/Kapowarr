const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/reader.html'),
	'utf8'
);
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/reader.js'),
	'utf8'
);
const style = fs.readFileSync(
	path.join(root, 'frontend/static/css/reader.css'),
	'utf8'
);

test('comic reader exposes a fullscreen control', () => {
	assert.match(template, /id="reader-fullscreen"/);
	assert.match(template, /reader-fullscreen-label/);
	assert.match(template, /aria-label="Enter fullscreen"/);
});

test('fullscreen control covers enter, exit, unsupported, and WebKit paths', () => {
	assert.match(script, /requestFullscreen/);
	assert.match(script, /exitFullscreen/);
	assert.match(script, /webkitRequestFullscreen/);
	assert.match(script, /webkitExitFullscreen/);
	assert.match(script, /fullscreenchange/);
	assert.match(script, /reader\.fullscreen\.classList\.add\('hidden'\)/);
});

test('fullscreen control stays compact on narrow reader layouts', () => {
	assert.match(style, /@media \(max-width: 700px\)/);
	assert.match(style, /\.reader-fullscreen-label\s*\{\s*display: none;/s);
});
