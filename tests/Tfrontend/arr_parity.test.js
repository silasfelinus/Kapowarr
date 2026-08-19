const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const parity = fs.readFileSync(path.join(root, 'docs/ARR_PARITY.md'), 'utf8');

test('arr parity baseline keeps shared operational expectations explicit', () => {
	for (const capability of ['Status / health', 'Tasks', 'Logs', 'Backup / restore', 'Events', 'Updates']) {
		assert.match(parity, new RegExp(capability.replace('/', '\\/')));
	}
	assert.match(parity, /supported`, `partial`, `missing`, or `not-applicable`/);
});
