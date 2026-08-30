const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(
	path.join(root, 'frontend/static/js/volumes_gallery.js'), 'utf8'
);

function harness({query = 'green arrow', filter = 'wanted'} = {}) {
	let onSubmit = null;
	const saved = [];
	const context = {
		window: {
			installVolumesGalleryRenderer() {}
		},
		library_els: {
			search: {
				container: {
					addEventListener(type, handler) {
						if (type === 'submit') onSubmit = handler;
					}
				},
				input: {value: query}
			},
			view_options: {
				filter: {value: filter}
			}
		},
		setLocalStorage(value) {
			saved.push(value);
		}
	};

	vm.createContext(context);
	vm.runInContext(source, context);
	assert.equal(typeof onSubmit, 'function');
	return {context, saved, submit: onSubmit};
};

test('a non-empty library search clears an active filter', () => {
	const h = harness();
	h.submit();

	assert.equal(h.context.library_els.view_options.filter.value, '');
	assert.deepEqual(h.saved, [{lib_filter: ''}]);
});

test('an empty search leaves the active filter alone', () => {
	const h = harness({query: '   '});
	h.submit();

	assert.equal(h.context.library_els.view_options.filter.value, 'wanted');
	assert.deepEqual(h.saved, []);
});

test('search does not rewrite storage when no filter is active', () => {
	const h = harness({filter: ''});
	h.submit();

	assert.equal(h.context.library_els.view_options.filter.value, '');
	assert.deepEqual(h.saved, []);
});
