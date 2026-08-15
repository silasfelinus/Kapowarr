//
// Rotating loading-screen flavor text
//
// Personal-fork QoL touch: swaps the default "Loading..." heading for a
// playful, Radarr/Sonarr-style line, picked at random each time a loading
// screen is shown. Centralized here (one data source + one lookup function)
// so any loading screen in the app can opt in via a single data attribute
// instead of scattered per-page copy.
//
// Kept pure and dependency-free on purpose: pickLoadingLine() takes an
// explicit lines array + random function, so it's unit-testable with a
// stubbed randomFn without touching the DOM.
//

const LOADING_LINES = [
	"Uncrinkling the pages...",
	"Waking up the letterer...",
	"Checking the staples...",
	"Flipping to the next issue...",
	"Talking the inker into one more panel...",
	"Dusting off the long boxes...",
	"Reordering the back issues...",
	"Asking the colorist for five more minutes...",
	"Chasing down a missing splash page...",
	"Un-bagging and un-boarding...",
];

// Must exactly match the fallback text already baked into every template's
// <h2>Loading...</h2>, so a slow/blocked script never leaves a blank heading.
const DEFAULT_LOADING_LINE = "Loading...";

function pickLoadingLine(lines = LOADING_LINES, randomFn = Math.random) {
	if (!Array.isArray(lines) || lines.length === 0)
		return DEFAULT_LOADING_LINE;
	const index = Math.floor(randomFn() * lines.length);
	return lines[Math.max(0, Math.min(lines.length - 1, index))];
};

function applyLoadingLines() {
	try {
		document.querySelectorAll('[data-loading-line]').forEach(el => {
			el.textContent = pickLoadingLine();
		});
	} catch (e) {
		// Any failure here leaves the template's own static "Loading..."
		// text in place -- never throw past this.
	}
};

// code run on load
applyLoadingLines();
