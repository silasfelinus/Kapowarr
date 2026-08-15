//
// Database-driven launch flair
//
// Personal-fork QoL touch, sibling to loading_lines.js: on each page load,
// shows one playful line in the header built from a random comic title
// already in the local library -- e.g. "Currently rereading Saga..." -- or
// a generic fallback line when the library is empty or the title couldn't
// be fetched. Centralized here (one template array + one pure build
// function) so the personality-layer copy stays in one place next to its
// static-line sibling.
//
// Kept pure and dependency-free on purpose: buildLaunchFlair() takes the
// title, templates, fallback lines, and random function as explicit
// arguments, so it's unit-testable without the DOM or a real fetch.
//

const LAUNCH_FLAIR_TEMPLATES = [
	"Currently rereading {title}...",
	"Dusting off {title}...",
	"Fresh off the shelf: {title}.",
	"Tonight's pick: {title}.",
	"Keeping an eye on {title}...",
];

// Shown when the library is empty or the title couldn't be fetched --
// deterministic, no randomness, so an empty/failed state never looks broken.
const DEFAULT_FLAIR_LINES = [
	"Ready for your first longbox.",
	"No comics yet -- add a volume to get started.",
];

const MAX_TITLE_LENGTH = 80;

// Strips control characters and truncates. The result is always written via
// textContent (never innerHTML) by applyLaunchFlair(), so this is a display
// safeguard against garbled/oversized data, not the injection defense --
// textContent is what actually makes injection impossible.
function sanitizeTitle(title) {
	if (typeof title !== 'string')
		return '';

	// eslint-disable-next-line no-control-regex
	const cleaned = title.replace(/[\x00-\x1F\x7F]/g, '').trim();

	if (cleaned.length <= MAX_TITLE_LENGTH)
		return cleaned;

	return cleaned.slice(0, MAX_TITLE_LENGTH - 1).trimEnd() + '…';
}

function pickRandom(list, randomFn = Math.random) {
	const index = Math.floor(randomFn() * list.length);
	return list[Math.max(0, Math.min(list.length - 1, index))];
}

function buildLaunchFlair(
	title,
	templates = LAUNCH_FLAIR_TEMPLATES,
	fallbackLines = DEFAULT_FLAIR_LINES,
	randomFn = Math.random
) {
	const clean = sanitizeTitle(title);

	if (!clean)
		return pickRandom(fallbackLines, randomFn);

	return pickRandom(templates, randomFn).replace('{title}', clean);
}

async function applyLaunchFlair() {
	const el = document.querySelector('#launch-flair');
	if (!el) return;

	try {
		const api_key = await usingApiKey(false);
		if (!api_key) return;

		const response = await fetchAPI('/system/launchflair', api_key);
		el.textContent = buildLaunchFlair(response.result.title);
	} catch (e) {
		// Any failure here leaves the header exactly as it was rendered
		// (empty) -- never throw past this, never leave a half-built line.
	}
}

// code run on load
applyLaunchFlair();
