const test = require('node:test');
const assert = require('node:assert/strict');

global.url_base = '/kapowarr';
const {
	manualSearchHeading,
	finishManualSearchAction
} = require('../../frontend/static/js/manual_search.js');

function actionButton() {
	const classes = new Set(['spinning']);
	const img = {
		src: '',
		classList: {
			add: value => classes.add(value),
			remove: value => classes.delete(value),
			contains: value => classes.has(value)
		}
	};
	return {
		button: {
			title: 'Download',
			classList: {
				add: value => classes.add(value),
				remove: value => classes.delete(value),
				contains: value => classes.has(value)
			},
			querySelector: () => img
		},
		img
	};
};

test('manual search heading identifies the selected target', () => {
	assert.equal(
		manualSearchHeading('Gwar: Orgasmageddon', 'TPB'),
		'Manual search — Gwar: Orgasmageddon — TPB'
	);
});

test('failed torrent add always exits the loading state', () => {
	const {button, img} = actionButton();
	finishManualSearchAction(button, 'Torrent metadata request timed out');

	assert.equal(img.classList.contains('spinning'), false);
	assert.equal(button.classList.contains('error'), true);
	assert.equal(button.title, 'Torrent metadata request timed out');
	assert.equal(img.src, '/kapowarr/static/img/download.svg');
});

test('successful torrent add exits loading and shows queued state', () => {
	const {button, img} = actionButton();
	finishManualSearchAction(button);

	assert.equal(img.classList.contains('spinning'), false);
	assert.equal(button.classList.contains('error'), false);
	assert.equal(button.title, 'Added to download queue');
	assert.equal(img.src, '/kapowarr/static/img/check.svg');
});
