const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/backup.html'),
	'utf8'
);
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/backup.js'),
	'utf8'
);
const status = fs.readFileSync(
	path.join(root, 'frontend/templates/status.html'),
	'utf8'
);
const startup = fs.readFileSync(path.join(root, 'Kapowarr.py'), 'utf8');

test('backup page exposes familiar automatic and manual backup behavior', () => {
	assert.match(template, /automatically creates a database backup every 7 days/i);
	assert.match(template, /keeps backups for 28 days/i);
	assert.match(template, /id="backup-now"/);
	assert.match(template, /pre-restore backup/i);
});

test('restore requires user confirmation and reports preserved current state', () => {
	assert.match(script, /if \(!confirm\(/);
	assert.match(script, /pre_restore_backup/);
	assert.match(script, /\/restore/);
});

test('backup downloads remain authenticated', () => {
	assert.match(script, /api_key=\$\{encodeURIComponent\(backup_api_key\)\}/);
});

test('system status exposes backup management', () => {
	assert.match(status, /aria-label="Backup"/);
	assert.match(status, /href="\{\{url_base\}\}\/system\/backup"/);
	assert.match(status, />Manage Backups<\/a>/);
});

test('startup applies staged restore before setup_db and stops backup scheduler', () => {
	const restore_call = startup.indexOf('\n        apply_pending_restore()\n');
	const setup_call = startup.indexOf('\n        setup_db()\n');
	assert.notEqual(restore_call, -1);
	assert.notEqual(setup_call, -1);
	assert.ok(restore_call < setup_call);
	assert.match(startup, /backup_scheduler\.start\(SERVER\.app\)/);
	assert.match(startup, /backup_scheduler\.stop\(\)/);
});
