const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');
const css = read('frontend/static/css/volumes.css');
const volumes = read('frontend/static/js/volumes.js');
const template = read('frontend/templates/volumes.html');

// Silas, on the table at 390px: "it's pretty useless since we are showing
// only the first letter of each title, but giving ample space for the rest".
//
// Measured, he was barely exaggerating: the title column was 60px wide of
// which 32px was padding, so 28px of text -- one character and an ellipsis
// -- while "Volume 1" had 112px to say "1" and the progress bar had 136px.
// Cell padding alone was 160px of a 512px table.

test('the title column asks for the whole table', () => {
	// Under `table-layout: fixed` a column left unset gets a *share* of what
	// is spare rather than all of it: at 390px that share came to 90px
	// against 180px actually free. Asking for 100% and being cut down to the
	// remainder is the deterministic way to say "the rest".
	assert.match(
		css,
		/#table-container :where\(th, td\):nth-child\(2\) \{ width: 100%; \}/
	);
});

test('every other column is sized for what it holds', () => {
	// A four-digit year, a volume number, a ratio, an icon. All known.
	for (const n of [3, 4, 5, 6]) {
		assert.match(
			css,
			new RegExp(`nth-child\\(${n}\\) \\{ width: [\\d.]+rem; \\}`),
			`column ${n} needs a width, or it competes with the title`
		);
	};
});

test('the volume column holds a number, not a sentence', () => {
	const build = volumes
		.split('function buildTableEntry')[1]
		.split('const view_builders')[0];

	assert.match(build, /\.table-volume'\)\.innerText =\s*\n?\s*volume\.volume_number;/);
	assert.ok(
		!build.includes('`Volume ${volume.volume_number}`'),
		'"Volume 1" down a column headed "Vol." spends the width of the word to say nothing'
	);
	assert.match(template, /<th>Vol\.<\/th>/);
});

test('a phone gets its padding back', () => {
	const narrow = css.slice(css.indexOf('@media (max-width: 600px)'));

	assert.match(narrow, /padding-inline: \.4rem;/);
	assert.match(narrow, /#table-container th \{\s*font-size: \.68rem;/,
		'a bold heading is wider than its data and was forcing the columns open');
});

test('a header too wide for its column clips instead of overlapping', () => {
	// At 390px "Progress" sat on top of "Monitored", which rendered "Mor".
	assert.match(
		css,
		/#table-container th \{\s*overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;/
	);
});

test('the two longest headings were shortened rather than clipped', () => {
	assert.match(template, /<th>Issues<\/th>/);
	assert.match(template, /<th aria-label="Monitored"><\/th>/,
		'the bookmark column explains itself; its heading only stole width');
	assert.ok(!template.includes('<th>Volume Title</th>'));
});
