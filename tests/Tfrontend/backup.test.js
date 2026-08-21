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
const base = fs.readFileSync(
	path.join(root, 'frontend/templates/base.html'),
	'utf8'
);
const status = fs.readFileSync(
	path.join(root, 'frontend/templates/status.html'),
	'utf8'
);
const startup = fs.readFileSync(path.join(root, 'Kapowarr.py'), 'utf8');

test('backup page exposes familiar automatic and manual backup behavior', () => {
	assert.match(template, /automatically creates a database backup/i);
	assert.match(template, /id="backup-now"/);
	assert.match(template, /pre-restore backup/i);
});

test('the schedule is adjustable rather than baked into the copy', () => {
	// The frequency and retention used to be hardcoded constants described in
	// prose on the page, so the only way to change either was to edit source.
	assert.match(template, /id="backup-interval"/);
	assert.match(template, /id="backup-keep"/);
	assert.match(template, /id="backup-schedule-save"/);
	assert.match(script, /backup_interval_days: interval/);
	assert.match(script, /backup_keep_count: keep/);
	assert.match(script, /sendAPI\('PUT', '\/settings'/);
});

test('pre-restore backups are described as exempt from the retention count', () => {
	// They are the undo for a restore, so retention pressure from ordinary
	// scheduled backups must not be able to delete one.
	assert.match(template, /Pre-restore\s+backups are kept outside this count/i);
});

test('restore requires user confirmation and reports preserved current state', () => {
	assert.match(script, /if \(!confirm\(/);
	assert.match(script, /pre_restore_backup/);
	assert.match(script, /\/restore/);
});

test('backup page supports restoring a previously downloaded file', () => {
	assert.match(template, /id="backup-upload"/);
	assert.match(template, />Restore from File<\/label>/);
	assert.match(script, /new FormData\(\)/);
	assert.match(script, /\/api\/system\/backups\/restore\?api_key=/);
});

test('backup API responses are decoded before using result fields', () => {
	assert.match(script, /return response\.json\(\)/);
	assert.match(script, /sendBackupPost\('\/system\/backups'\)/);
});

test('backup downloads remain authenticated', () => {
	assert.match(script, /api_key=\$\{encodeURIComponent\(backup_api_key\)\}/);
});

test('backup is a system tab rather than a link buried in status', () => {
	assert.match(base, /href="\{\{url_base\}\}\/system\/backup"/);
	assert.match(base, />Backup<\/a>/);
	assert.match(base, /request\.path == '\/system\/backup' %\}current-nav/);
	// Listed once. It was reachable from two places, which reads as two
	// different destinations rather than one.
	assert.doesNotMatch(status, /\/system\/backup/);
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
