const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(
	path.join(__dirname, '../../frontend/static', name), 'utf8'
);

const volumes = read('js/volumes.js');
const gallery = read('js/volumes_gallery.js');
const css = read('css/volumes.css');

// Both library views materialize an element for every volume in the result
// set on purpose: that is what makes the list whole, so scrolling reaches Z
// instead of stopping wherever a scroll listener last fired. The price of
// that decision is that anything expensive per row is multiplied by the
// whole library, and a 5,000-volume library on a phone has no headroom to
// spare. Table view scrolled a little way and then took the tab with it.

test('the monitored toggle costs no elements', () => {
	const render = volumes.slice(
		volumes.indexOf('renderMonitored() {'),
		volumes.indexOf('getProgress()')
	);
	assert.notEqual(render.length, 0, 'renderMonitored not found');

	// `setIcon` assigns `innerHTML`. Per row that is an HTML-fragment parse
	// during the build and a live SVG element tree for as long as the row
	// exists -- around four nodes each, with their own layout and paint
	// objects, in a row that is otherwise text.
	assert.doesNotMatch(
		render,
		/setIcon\(/,
		'an inline SVG per row is the most expensive thing in the table'
	);
	assert.match(render, /monitored_button\.dataset\.monitored =/);
});

test('the icon is drawn by CSS instead', () => {
	assert.match(css, /--icon-monitored:\s*url\("data:image\/svg\+xml,/);
	assert.match(css, /--icon-unmonitored:\s*url\("data:image\/svg\+xml,/);

	const rule = css.slice(
		css.indexOf('.table-monitored {'),
		css.indexOf('/*  */\n/* Lib stats */')
	);
	assert.match(rule, /mask-image: var\(--icon-unmonitored\)/);
	assert.match(rule, /\[data-monitored="true"\]/);
	assert.match(rule, /mask-image: var\(--icon-monitored\)/);
	// The colour the SVG used to carry in its own `fill`.
	assert.match(rule, /background-color: var\(--dimmed-text-color\)/);
	// Safari still wants the prefixed properties.
	assert.match(rule, /-webkit-mask-image:/);
});

test('the toggle still says which state it is in', () => {
	// The SVG was the only thing distinguishing the two states visually, and
	// `setIcon` was also what set the button's title. Dropping it must not
	// drop the label with it.
	const render = volumes.slice(
		volumes.indexOf('renderMonitored() {'),
		volumes.indexOf('getProgress()')
	);
	assert.match(render, /'Monitored' : 'Unmonitored'/);
	assert.match(render, /monitored_button\.title = label/);
	assert.match(render, /monitored_button\.ariaLabel = label/);
});

test('no row carries a class nothing matches', () => {
	// Every row and card used to get a `vol-<id>` class. Nothing read it --
	// not a stylesheet, not a selector, not a template -- and a class list
	// unique to each element is one the browser cannot share a computed
	// style across, so 5,000 structurally identical rows resolved their
	// styles 5,000 times.
	for (const [name, source] of [['volumes.js', volumes], ['gallery', gallery]]) {
		assert.ok(
			!source.includes('vol-${volume.id}'),
			`${name} still tags rows with a class nothing selects`
		);
	};
	assert.ok(
		!css.includes('.vol-'),
		'and no stylesheet has started matching one'
	);
});

test('identity still lives on the elements that need it', () => {
	assert.match(volumes, /table_entry\.dataset\.id = volume\.id;/);
	assert.match(gallery, /list_entry\.dataset\.volumeId = volume\.id;/);
});
