const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/logs.html'),
	'utf8'
);
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/logs.js'),
	'utf8'
);
const status = fs.readFileSync(
	path.join(root, 'frontend/templates/status.html'),
	'utf8'
);
const base = fs.readFileSync(
	path.join(root, 'frontend/templates/base.html'),
	'utf8'
);
const css = fs.readFileSync(
	path.join(root, 'frontend/static/css/logs.css'),
	'utf8'
);
const general = fs.readFileSync(
	path.join(root, 'frontend/static/css/general.css'),
	'utf8'
);

test('the toolbar is actions on the left and filters on the right', () => {
	const actions = template.indexOf('class="logs-actions"');
	const filters = template.indexOf('class="logs-filters"');
	assert.notEqual(actions, -1);
	assert.notEqual(filters, -1);
	assert.ok(actions < filters, 'actions come before filters in the toolbar');

	assert.match(template, /id="refresh-logs"/);
	assert.match(template, /id="clear-logs"/);
	assert.match(template, /id="log-page-size"/);
	assert.match(template, /id="log-view-level"/);
});

test('the level filter selects one level rather than a minimum', () => {
	assert.match(template, /value="ALL" selected/);
	assert.match(template, /All levels/);
	// "Info +" style options promised a minimum, which buried the level you
	// picked under everything less severe than it.
	assert.doesNotMatch(template, /Debug \+/);
	assert.doesNotMatch(template, /Errors \+/);
});

test('capture level is no longer a control on this page', () => {
	// Persisting debug output is a settings decision, not something to change
	// while reading; leaving it here invited turning it on and forgetting.
	assert.doesNotMatch(template, /id="log-capture-level"/);
	assert.doesNotMatch(script, /log_level/);
});

test('the table shows time, type and message', () => {
	assert.match(template, />Time</);
	assert.match(template, />Type</);
	assert.match(template, />Message</);
});

test('pagination controls sit below the table', () => {
	const table = template.indexOf('logs-table-wrap');
	const pager = template.indexOf('logs-pagination');
	assert.ok(table !== -1 && pager !== -1);
	assert.ok(table < pager, 'the pager follows the table');
	assert.match(template, /id="log-first"/);
	assert.match(template, /id="log-prev"/);
	assert.match(template, /id="log-next"/);
	assert.match(template, /id="log-last"/);
});

test('clearing the log asks first and goes through its own endpoint', () => {
	assert.match(script, /if \(!confirm\(/);
	assert.match(script, /sendAPI\('POST', '\/system\/logs\/clear'/);
});

test('the log page is a document, not a stack of scroll panes', () => {
	// general.css makes every child of <main> an equal-height pane with its own
	// scrollbar, which suits a page holding one viewport under a tool bar. Here
	// it gave the toolbar, the table and the pager a third of the screen each,
	// so the log came out as a sliver of rows surrounded by blank space.
	assert.match(template, /<main class="page-flow">/);
	assert.doesNotMatch(css, /max-height: calc\(100vh/);
	assert.doesNotMatch(
		css,
		/\.logs-table-wrap \{[^}]*overflow: auto/,
		'the entry list is the page and scrolls with it'
	);
});

test('page-flow opts a page out of the shared pane layout', () => {
	assert.match(general, /main\.page-flow \{[^}]*display: block/);
	assert.match(general, /main\.page-flow > \*:not\(\.tool-bar-container\)/);
	assert.match(general, /flex: none/);
});

test('controls do not paint text on a background of the same colour', () => {
	// --nav-background-color and --text-color are the same value in the light
	// theme, so pairing them rendered an unreadable black-on-black block.
	const pairs = css.match(/background: var\(--nav-background-color\)/g) || [];
	assert.equal(pairs.length, 0);
});

test('logs viewer renders log content as text instead of injecting markup', () => {
	assert.match(script, /body\.textContent = details/);
	assert.match(script, /label\.textContent = summary/);
	assert.doesNotMatch(script, /innerHTML\s*=\s*(entry|summary|details)/);
});

test('a stack trace collapses behind its summary line', () => {
	// One log line and one stack trace are read differently. Every entry
	// rendered in full turned a page with a few exceptions into a wall of
	// frames with the sequence of events buried in it.
	assert.match(script, /function splitLogMessage/);
	assert.match(script, /classList\.add\('log-details', 'hidden'\)/);
	assert.match(script, /setAttribute\('aria-expanded', 'false'\)/);
	assert.match(script, /more line/);
});

test('a single-line entry gets no expander', () => {
	assert.match(script, /if \(!details\) \{/);
});

test('levels are rendered as badges the eye can sort on', () => {
	assert.match(script, /classList\.add\('log-level-badge'\)/);
	assert.match(css, /\.log-error \.log-level-badge/);
	assert.match(css, /\.log-warning \.log-level-badge/);
});

test('logs are a system tab and are not also listed on status', () => {
	assert.match(base, /href="\{\{url_base\}\}\/system\/logs"/);
	// Listed once. Two entries read as two destinations rather than one.
	assert.doesNotMatch(status, /\/system\/logs/);
});

test('logs are reachable from the system navigation, not only from status', () => {
	// The page existed and was only linked from inside Status, so finding it
	// meant knowing it was there.
	assert.match(base, /href="\{\{url_base\}\}\/system\/logs"/);
	assert.match(base, />Logs<\/a>/);
	assert.match(
		base,
		/request\.path == '\/system\/logs' %\}current-nav/,
		'the nav entry has to mark itself current on the logs page'
	);
});
