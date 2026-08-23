const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/view_volume.js'),
	'utf8'
);
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/view_volume.html'),
	'utf8'
);

function downloadActionFor(result) {
	const body = script.slice(
		script.indexOf('function downloadActionFor'),
		script.indexOf('function addManualSearch')
	);
	const context = vm.createContext({result});
	return vm.runInContext(`${body}\ndownloadActionFor(result);`, context);
}

// There were two buttons here -- Download and Force Download -- side by side
// and near-identical, and on a result that does not match the issue the first
// always failed while the second always worked. One button that never works is
// worse than no button: it reads as the app being broken.
test('a matching result gets a plain download that does not force', () => {
	const action = downloadActionFor({match: true});

	assert.equal(action.force, false);
	assert.equal(action.icon, 'download.svg');
	assert.equal(action.title, 'Download');
});

test('a non-matching result forces, because the plain grab would fail', () => {
	const action = downloadActionFor({
		match: false, match_issue: 'Issue number does not match'
	});

	assert.equal(action.force, true);
	assert.equal(action.icon, 'force_download.svg');
});

test('the tooltip says why the release did not match', () => {
	// The Match column shows the same reason; repeating it on the button is
	// what makes the different icon legible instead of mysterious.
	const action = downloadActionFor({
		match: false, match_issue: 'Issue number does not match'
	});

	assert.match(action.title, /Download anyway/);
	assert.match(action.title, /Issue number does not match/);
});

test('a missing reason still produces a usable tooltip', () => {
	const action = downloadActionFor({match: false, match_issue: null});

	assert.match(action.title, /does not match the issue/);
	assert.doesNotMatch(action.title, /null/);
});

test('the row offers exactly one download button', () => {
	const column = template.slice(
		template.indexOf('class="search-action-column'),
		template.indexOf('</td>', template.indexOf('class="search-action-column'))
	);
	const buttons = column.match(/icon_button\(/g) || [];

	assert.equal(
		buttons.length, 2,
		'one download button and one blocklist button, no second downloader'
	);
	assert.match(column, /"Download", "download\.svg"/);
	assert.match(column, /"Add to blocklist"/);
	assert.doesNotMatch(column, /Force Download/);
});

test('the renderer picks the button rather than the template', () => {
	// Both icons must remain reachable: the template ships the plain one and
	// the renderer swaps it for a row that needs forcing.
	assert.match(script, /const action = downloadActionFor\(result\)/);
	assert.match(script, /setImage\(download_button, action\.icon, action\.title\)/);
	assert.match(script, /addManualSearch\(\s*result\.link, action\.force/);
});

test('blocklist is the second child now that one button is gone', () => {
	// An off-by-one here silently wires the wrong button.
	assert.match(
		script,
		/blocklist_button = entry\.querySelector\('\.search-action-column :nth-child\(2\)'\)/
	);
});
