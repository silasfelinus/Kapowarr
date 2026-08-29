const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const template = read('frontend/templates/view_volume.html');
const script = read('frontend/static/js/view_volume.js');
const css = read('frontend/static/css/view_volume.css');

// "I do not see where to access the option in mad magazine to keep
// unmatched, and it doesn't appear in the list of imports."
//
// It was there: volume page, Manage Issues, footer of the window. Three
// steps behind an icon whose label said "Issues" while the thing being
// looked for was a file action -- and the window said nothing about how
// many files it would apply to, so even reaching it left the question of
// whether it was the right button.
//
// It cannot appear in the import list either, and that is correct: the MAD
// folder belongs to a volume, so it is never an unimported folder. The
// files are refused by the scanner, not offered for import. The volume
// page is the only place this can live.

test('the window is named for what it lists', () => {
	assert.match(template, /"manage-window", "Manage Files"/);
	assert.match(template, /"Manage Files"\n\s*\) \}\}/,
		'the toolbar label is what someone scans for');
	assert.ok(
		!template.includes('"Manage Issues"'),
		'the window lists files and what each is matched to'
	);
});

test('the toolbar says the action is in there', () => {
	const button = template.slice(
		template.indexOf('"manage-button"'),
		template.indexOf('"files-button"')
	);
	assert.match(button, /keep the ones nothing claims/);
});

test('the window says how many files the action would take', () => {
	assert.match(template, /id="manage-unmatched-summary"/);

	const summary = script.slice(
		script.indexOf('function showUnmatchedSummary'),
		script.indexOf('function showManageIssues')
	);
	assert.notEqual(summary.length, 0, 'showUnmatchedSummary not found');
	assert.match(summary, /Keep \$\{unmatched\} Unmatched/,
		'the count belongs on the button, not only in prose');
	assert.match(summary, /matched to nothing/);
	// The two things it must promise, because they are why it is safe.
	assert.match(summary, /without moving them/);
	assert.match(summary, /stay wanted/);
});

test('and does not offer it when there is nothing to do', () => {
	const summary = script.slice(
		script.indexOf('function showUnmatchedSummary'),
		script.indexOf('function showManageIssues')
	);
	assert.match(summary, /adopt\.disabled = unmatched === 0/);
	assert.match(template, /id="adopt-unmatched-files"[^>]*\sdisabled/,
		'it starts disabled, before any count has been fetched');
});

test('the count comes from the same list the window renders', () => {
	// Not a second request, and not a guess: the mapping list already says
	// what each file is matched to.
	const show = script.slice(
		script.indexOf('function showManageIssues'),
		script.indexOf('function toggleAllManages')
	);
	assert.match(
		show,
		/!mapping\.issue_ids\.length && !mapping\.general_file/,
		'unmatched means matched to no issue and not a general file'
	);
	assert.match(show, /showUnmatchedSummary\(unmatched, json\.result\.length\)/);
});

test('a footer button without a background gets a legible colour', () => {
	// The actual reason the button could not be found. `.window-footer
	// button` sets `color: var(--light-color)`, which is right for the two
	// buttons that carry a solid background -- Cancel is red, Save is green
	// -- and wrong for every other button in a footer, which has none.
	// "Keep Unmatched" shipped as white text on the window's own white
	// background: present, measurable, and invisible. Verified in Chromium
	// afterwards at rgb(17,17,17) enabled and rgb(85,85,85) disabled.
	const rule = css.slice(
		css.indexOf('#show-issue-match,'),
		css.indexOf('#manage-window:has(')
	);
	assert.notEqual(rule.length, 0, 'the shared footer-button rule is gone');
	assert.match(rule, /#adopt-unmatched-files \{/);
	assert.match(rule, /color: var\(--text-color\)/);

	assert.match(css, /#adopt-unmatched-files:disabled \{[^}]*color: var\(--dimmed-text-color\)/);
});
