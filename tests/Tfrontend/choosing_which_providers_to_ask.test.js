const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(
	path.join(__dirname, '../..', name), 'utf8'
);

const source = read('frontend/static/js/library_import_match_search.js');
const markup = read('frontend/templates/library_import.html');
const css = read('frontend/static/css/library_import.css');

// The provider-choice helpers, run for real rather than read.
const helpers = () => {
	const body = source.slice(
		source.indexOf("const PROVIDER_CHOICE_KEY"),
		source.indexOf('function renderProviderToggles')
	);
	assert.notEqual(body.length, 0, 'the provider helpers were not found');

	const store = {};
	const localStorage = {
		getItem: k => (k in store ? store[k] : null),
		setItem: (k, v) => { store[k] = String(v); },
		removeItem: k => { delete store[k]; }
	};
	const factory = new Function('localStorage', body + `
		return {
			set: p => { providers = p; },
			chosenProviders, rememberProviderChoice, recallProviderChoice
		};
	`);
	return factory(localStorage);
};

test('asking for everyone is sent as no preference at all', () => {
	// The shorter URL is the one worth sending, and it is also what every
	// caller before this change sent.
	const h = helpers();
	h.set([{id: 'comicvine', on: true}, {id: 'gcd', on: true}]);

	assert.equal(h.chosenProviders(), null);
});

test('a narrowed choice is sent as itself', () => {
	const h = helpers();
	h.set([{id: 'comicvine', on: false}, {id: 'gcd', on: true}]);

	assert.deepEqual(h.chosenProviders(), ['gcd']);
});

test('before the providers are known, nothing is claimed about them', () => {
	const h = helpers();

	assert.equal(h.chosenProviders(), null);
});

test('the choice is remembered and read back', () => {
	// Someone who has switched a slow provider off does not want it back on
	// the next file.
	const h = helpers();
	h.set([{id: 'comicvine', on: false}, {id: 'gcd', on: true}]);

	h.rememberProviderChoice();

	assert.deepEqual(h.recallProviderChoice(), ['gcd']);
});

test('nothing remembered is not the same as remembering nothing', () => {
	const h = helpers();

	assert.equal(h.recallProviderChoice(), null);
});

test('a storage that refuses does not break the dialog', () => {
	// Private windows and locked-down browsers throw on access rather than
	// returning null, and a search dialog is not worth a crash.
	const body = source.slice(
		source.indexOf("const PROVIDER_CHOICE_KEY"),
		source.indexOf('function renderProviderToggles')
	);
	const hostile = {
		getItem() { throw new Error('denied'); },
		setItem() { throw new Error('denied'); }
	};
	const h = new Function('localStorage', body + `
		return {set: p => { providers = p; },
			rememberProviderChoice, recallProviderChoice};
	`)(hostile);

	h.set([{id: 'gcd', on: true}]);
	assert.doesNotThrow(() => h.rememberProviderChoice());
	assert.equal(h.recallProviderChoice(), null);
});

test('the last provider cannot be switched off', () => {
	// A search that asks nobody is not a search, and the backend would
	// quietly fall back to all of them anyway -- which would look like the
	// toggle doing the opposite of what it says.
	const toggles = source.slice(
		source.indexOf('function renderProviderToggles'),
		source.indexOf('function loadProviders')
	);

	assert.match(toggles, /!providers\.some\(p => p\.on\)/);
	assert.match(toggles, /box\.checked = true/);
});

test('one provider means no toggles to choose between', () => {
	const toggles = source.slice(
		source.indexOf('function renderProviderToggles'),
		source.indexOf('function loadProviders')
	);

	assert.match(toggles, /providers\.length < 2/);
});

test('a remembered set naming nothing configured is discarded', () => {
	// Otherwise removing a provider from settings leaves the dialog unable
	// to search at all.
	const load = source.slice(
		source.indexOf('function loadProviders'),
		source.indexOf('function getMatchSearchStatus')
	);

	assert.match(load, /if \(!providers\.some\(p => p\.on\)\)/);
});

test('the toggles are on screen before the first search runs', () => {
	// The dialog searches the proposed title as soon as it opens, and that
	// search has to honour the choice the toggles are about to show as
	// already made.
	const open = source.slice(
		source.indexOf('window.openEditCVMatch'),
		source.indexOf('window.buildProposalRow')
	);

	assert.match(open, /loadProviders\(\)\.then/);
	assert.match(open, /searchCV\(\)/);
});

test('the search sends the choice with the query', () => {
	const search = source.slice(
		source.indexOf('window.searchCV'),
		source.indexOf('window.openEditCVMatch')
	);

	assert.match(search, /providers: chosen\.join\(','\)/);
	assert.match(search, /chosen === null \? \{query\}/);
});

test('the dialog has somewhere to put them', () => {
	assert.match(markup, /id="match-search-providers"/);
	assert.match(markup, /aria-label="Which metadata providers to search"/);
	// Hidden until there is something to show.
	assert.match(markup, /id="match-search-providers"[^>]*class="hidden"/);
});

test('a toggle is big enough for a thumb', () => {
	// This dialog is most often reached from a phone.
	const rule = css.slice(
		css.indexOf('.provider-toggle {'),
		css.indexOf('.provider-toggle input {')
	);
	assert.notEqual(rule.length, 0, '.provider-toggle rule not found');

	assert.match(rule, /min-height: 2rem/);
	assert.match(css, /\.provider-toggle input \{[^}]*width: 1\.1rem/);
});
