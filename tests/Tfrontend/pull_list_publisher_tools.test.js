const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');

function read(relative) {
	return fs.readFileSync(path.join(root, relative), 'utf8');
}

const template = read('frontend/templates/pull_list.html');
const helper = read('frontend/static/js/pull_list_publisher_tools.js');
const styles = read('frontend/static/css/pull_list.css');
const statusRoutes = read('frontend/pull_list_status.py');


test('publisher helper loads after the base pull-list script', () => {
	const base = template.indexOf("js/pull_list.js");
	const tools = template.indexOf("js/pull_list_publisher_tools.js");
	assert.ok(base >= 0 && tools > base);
});


test('publisher automation offers one-click grab-all for listed publishers', () => {
	assert.match(template, /id="publisher-grab-all"/);
	assert.match(template, /Auto-add &amp; grab all listed publishers/);
	assert.match(helper, /sendAPI\('POST', '\/pulllist\/publishers\/grab-all'/);
	assert.match(statusRoutes, /@api\.route\('\/pulllist\/publishers\/grab-all', methods=\['POST'\]\)/);
	assert.match(statusRoutes, /set_all_publisher_subscriptions\(root_folder_id\)/);
	assert.match(helper, /Publishers discovered later will remain off/);
});


test('publisher rows expose question-mark information with hover summaries', () => {
	assert.match(template, /class="publisher-info-button"/);
	assert.match(template, /id="publisher-info-dialog"/);
	assert.match(helper, /const PUBLISHER_PROFILES =/);
	assert.match(helper, /'marvel comics'/);
	assert.match(helper, /'image comics'/);
	assert.match(helper, /'heavy metal'/);
	assert.match(helper, /'comixology originals'/);
	assert.match(helper, /'american mythology productions'/);
	assert.match(helper, /'fire ant entertainment'/);
	assert.match(helper, /publisherProfileKey\(name\)/);
	assert.match(helper, /dataset\.publisherTooltip = description/);
	assert.match(helper, /addEventListener\('pointerover'/);
	assert.match(helper, /addEventListener\('focusin'/);
	assert.match(styles, /data-publisher-tooltip/);
	assert.match(styles, /:hover::after/);
	assert.match(styles, /:focus-visible::after/);
	assert.match(helper, /Special:Search\?search=/);
	assert.match(helper, /publisherAutomationLabel\(publisher\)/);
});


test('unknown publisher labels get an honest curated fallback', () => {
	assert.match(
		helper,
		/Independent or specialist publisher listed by the release catalogue/
	);
	assert.match(helper, /catalogue names can also represent imprints or distribution labels/);
});


test('pull list explains add, want, and grab semantics', () => {
	assert.match(template, /<strong>Add \+ want<\/strong> adds a series to Kapowarr and monitors it/);
	assert.match(template, /<strong>Want<\/strong> monitors a series already in your library/);
	assert.match(template, /<strong>Grab<\/strong> does the same monitoring step and immediately searches/);
});