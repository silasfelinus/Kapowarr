const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');

function read(relative) {
	return fs.readFileSync(path.join(root, relative), 'utf8');
}

const template = read('frontend/templates/settings_indexers.html');
const windowCss = read('frontend/static/css/window.css');

test('indexer API keys are masked in add and edit forms', () => {
	assert.match(
		template,
		/<input type="password" id="add-api-key-input"[^>]*>/
	);
	assert.match(
		template,
		/<input type="password" id="edit-api-key-input"[^>]*>/
	);
	assert.doesNotMatch(
		template,
		/<input type="text" id="(?:add|edit)-api-key-input"/
	);
});

test('modal overlay is bound to the viewport and modal height stays capped', () => {
	const overlay = windowCss
		.split('.window[show-window] {')[1]
		.split('}')[0];
	const modal = windowCss
		.split('.window > section[show-window] {')[1]
		.split('}')[0];

	assert.match(overlay, /position:\s*fixed;/);
	assert.match(overlay, /inset:\s*0 0 0 0;/);
	assert.match(modal, /max-height:\s*90%;/);
	assert.match(modal, /overflow:\s*auto;/);
});
