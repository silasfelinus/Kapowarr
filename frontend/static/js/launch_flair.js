//
// Database-driven launch flair
//
// Personal-fork QoL touch, sibling to loading_lines.js: on each page load,
// shows the installation name plus one playful line built from a random comic
// title already in the local library -- e.g. "Acrocat Comics · Meanwhile, in
// Saga..." -- or a generic fallback line when the library is empty or the title
// couldn't be fetched. Centralized here so the personality-layer copy stays in
// one place next to its static-line sibling.
//
// Kept pure and dependency-free on purpose: buildLaunchFlair() takes the
// title, templates, fallback lines, and random function as explicit arguments,
// so it's unit-testable without the DOM or a real fetch.
//

const LAUNCH_FLAIR_TEMPLATES = [
	"Currently rereading {title}...",
	"Dusting off {title}...",
	"Fresh off the shelf: {title}.",
	"Tonight's pick: {title}.",
	"Keeping an eye on {title}...",
	"Meanwhile, in {title}...",
	"Previously, in {title}...",
	"Pulling {title} from the longbox...",
	"Opening carefully to {title}...",
	"Checking the continuity in {title}...",
	"No spoilers. Probably. {title} is up next.",
	"Saving a spot on the spinner rack for {title}.",
	"Putting {title} back in reading order...",
	"A dramatic caption box points toward {title}.",
	"The pull list insists on {title}.",
	"Bagged, boarded, and thinking about {title}.",
	"One more issue of {title} couldn't hurt...",
	"Consulting the back-issue oracle about {title}...",
	"Somewhere, an editor is explaining {title}.",
	"Checking whether {title} survived the last reboot...",
	"Reshelving {title} by vibes instead of chronology...",
	"Holding {title} by the edges...",
	"Adding an unnecessary dramatic pause before {title}...",
	"The longbox has chosen {title}.",
	"Tracking down the variant cover for {title}...",
	"Checking the indicia on {title}...",
	"Trying to remember which universe {title} is in...",
	"Making room for {title} between two crossovers...",
	"Rechecking the issue numbers on {title}...",
	"Pretending the timeline for {title} is perfectly straightforward...",
	"Pulling the next chapter of {title} into the light...",
	"Asking the sidekick where we left {title}...",
];

// Shown when the library is empty or the title couldn't be fetched.
const DEFAULT_FLAIR_LINES = [
	"Ready for your first longbox.",
	"The spinner rack is suspiciously tidy.",
	"Waiting for the pull list to get interesting.",
	"Plenty of shelf space. For now.",
	"No continuity errors detected. There is also no continuity yet.",
];

const MAX_TITLE_LENGTH = 80;
const DEFAULT_APP_TITLE = "Kapowarr";

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

		const [flairResponse, settingsResponse] = await Promise.all([
			fetchAPI('/system/launchflair', api_key),
			fetchAPI('/settings', api_key),
		]);

		const appTitle = sanitizeTitle(settingsResponse.result.app_title) || DEFAULT_APP_TITLE;
		const flair = buildLaunchFlair(flairResponse.result.title);
		el.textContent = `${appTitle} · ${flair}`;
	} catch (e) {
		// Any failure here leaves the header exactly as it was rendered
		// (empty) -- never throw past this, never leave a half-built line.
	}
}

// code run on load
applyLaunchFlair();
