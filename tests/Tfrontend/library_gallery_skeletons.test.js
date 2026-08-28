const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const template = read('frontend/templates/volumes.html');
const gate = read('frontend/static/js/volumes_gallery_gate.js');
const gallery = read('frontend/static/js/volumes_gallery.js');
const css = read('frontend/static/css/volumes.css');

test('the gallery renderer is installed before the Volumes API continuation can run', () => {
	const gateIndex = template.indexOf('volumes_gallery_gate.js');
	const volumesIndex = template.indexOf('volumes.js');
	const galleryIndex = template.indexOf('volumes_gallery.js');
	assert.ok(gateIndex >= 0 && volumesIndex >= 0 && galleryIndex >= 0);
	assert.ok(gateIndex < volumesIndex && volumesIndex < galleryIndex);
	assert.match(gate, /const volumes_gallery_using_api_key = usingApiKey/);
	assert.match(gate, /await volumes_gallery_ready/);
	assert.match(gate, /resolve_volumes_gallery_ready\(\)/);
});

test('poster view materializes the complete metadata index instead of a scroll runway', () => {
	assert.match(gallery, /for \(const volume of library_volumes\)/);
	assert.match(gallery, /buildCompletePosterIndex/);
	assert.ok(
		!gallery.includes('LIBRARY_RENDER_BATCH_SIZE'),
		'poster shells should not stop at the old incremental append boundary'
	);
});

test('poster shells reserve image geometry without requesting every cover', () => {
	assert.match(gallery, /img\.removeAttribute\('src'\)/);
	assert.match(gallery, /img\.dataset\.src =/);
	assert.match(gallery, /new IntersectionObserver/);
	assert.match(gallery, /rootMargin: `\$\{COVER_OVERSCAN_PX\}px 0px \$\{COVER_OVERSCAN_PX\}px 0px`/);
	assert.match(gallery, /img\.src = img\.dataset\.src/);
	assert.match(css, /\.list-img:not\(\[src\]\)/);
	assert.match(css, /aspect-ratio: 2\/3/);
});

test('off-screen poster shells opt into browser rendering containment', () => {
	assert.match(css, /content-visibility: auto/);
	assert.match(css, /contain-intrinsic-size: auto 15rem/);
});
