const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const general = fs.readFileSync(
	path.join(root, 'frontend/static/js/general.js'),
	'utf8'
);
const libraryImport = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import.js'),
	'utf8'
);

function buildQuery(params) {
	const body = general.slice(
		general.indexOf('function buildQuery'),
		general.indexOf('async function fetchAPI')
	);
	const context = vm.createContext({params});
	return vm.runInContext(`${body}\nbuildQuery(params);`, context);
}

// A manual-search download link is itself a URL with a query:
//   https://prowlarr…/9/download?apikey=…&link=…&file=…
// Joined raw into Kapowarr's own query string, its `&link=` and `&file=`
// became parameters of Kapowarr's request. Flask reads the first `link`,
// which is the URL truncated at its first `&`, and Prowlarr answers
// "Invalid Prowlarr link" because the parts it needs never arrived.
const PROWLARR_LINK =
	'https://prowlarr.example.com/9/download?apikey=KEY&link=ENC&file=n.nzb';

test('a link carrying its own query survives as one parameter', () => {
	const query = buildQuery({link: PROWLARR_LINK, force_match: false});

	// Exactly one `link=` belongs to Kapowarr's request.
	assert.equal((query.match(/(^|&)link=/g) || []).length, 1);
	assert.equal((query.match(/(^|&)file=/g) || []).length, 0);
	assert.ok(!query.includes('?apikey=KEY&link='), 'must not break out');
});

test('the value decodes back to the original link', () => {
	const query = buildQuery({link: PROWLARR_LINK});
	const value = new URLSearchParams(query.slice(1)).get('link');

	assert.equal(value, PROWLARR_LINK);
});

test('the parts Prowlarr requires are still present after decoding', () => {
	// GetDownload(int id, string link, string file) -- all three or it 500s.
	const value = new URLSearchParams(
		buildQuery({link: PROWLARR_LINK}).slice(1)
	).get('link');

	assert.match(value, /[?&]apikey=/);
	assert.match(value, /[?&]link=/);
	assert.match(value, /[?&]file=/);
});

test('spaces and reserved characters are encoded', () => {
	const query = buildQuery({query: 'The Agent & Co #3'});
	const value = new URLSearchParams(query.slice(1)).get('query');

	assert.equal(value, 'The Agent & Co #3');
	assert.ok(!query.includes(' '), 'a raw space would truncate the URL');
});

test('no parameters produces no query fragment', () => {
	assert.equal(buildQuery({}), '');
});

test('null and undefined values are dropped, not stringified', () => {
	// `link=undefined` reaching the backend is worse than no link at all.
	assert.equal(buildQuery({a: null, b: undefined}), '');
	assert.equal(buildQuery({a: null, b: 1}), '&b=1');
});

test('a falsy but real value is kept', () => {
	assert.equal(buildQuery({force_match: false}), '&force_match=false');
	assert.equal(buildQuery({offset: 0}), '&offset=0');
});

test('both helpers go through it rather than joining raw', () => {
	assert.ok(
		!general.includes("p.join('=')"),
		'raw joining is what corrupted the link'
	);
	assert.equal(
		// The definition line matches too, hence three.
		(general.match(/buildQuery\(params\)/g) || []).length, 3,
		'fetchAPI and sendAPI both call it'
	);
	assert.match(general, /const formatted_params = buildQuery\(params\);/);
});

test('no call site encodes a param value a second time', () => {
	// Encoding centrally and at the call site yields a double-encoded value
	// that the backend reads literally.
	assert.doesNotMatch(
		libraryImport,
		/params\.\w+ = encodeURIComponent\(/,
		'buildQuery already encodes'
	);
});


// buildQuery alone is not the thing that broke: the callers were. These run
// sendAPI and fetchAPI with fetch stubbed, and read the URL they construct.
function capturedUrl(invoke) {
	// buildQuery through the end of sendAPI: the two helpers plus the shared
	// builder, with nothing after them that needs a DOM.
	const body = general.slice(
		general.indexOf('function buildQuery'),
		general.indexOf('function getVolumeDownloads')
	);

	let seen = null;
	const context = vm.createContext({
		url_base: '',
		fetch: (url) => {
			seen = url;
			return Promise.resolve({ok: true, json: () => ({})});
		}
	});
	vm.runInContext(`${body}\n(${invoke})();`, context);
	return seen;
}

test('sendAPI sends the whole link, not the part before its first &', () => {
	const url = capturedUrl(
		`() => sendAPI('POST', '/issues/1/download', 'KEYX', ` +
		`{link: ${JSON.stringify(PROWLARR_LINK)}, force_match: false})`
	);

	assert.ok(url, 'sendAPI must have called fetch');
	const query = new URLSearchParams(url.split('?')[1]);
	assert.equal(query.get('link'), PROWLARR_LINK);
	assert.equal(query.get('force_match'), 'false');
	assert.equal(query.get('api_key'), 'KEYX');
});

test('fetchAPI encodes its parameters the same way', () => {
	const url = capturedUrl(
		`() => fetchAPI('/volumes/search', 'KEYX', {query: 'The Agent & Co'})`
	);

	const query = new URLSearchParams(url.split('?')[1]);
	assert.equal(query.get('query'), 'The Agent & Co');
});
