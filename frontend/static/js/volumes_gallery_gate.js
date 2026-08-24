// The Volumes page has a small follow-up script that installs its poster-gallery
// renderer after volumes.js has declared the normal page functions. volumes.js
// immediately asks for an API key and starts fetching, so hold only that page's
// usingApiKey() continuation until the renderer is installed. General page setup
// has already called usingApiKey() before this script runs and is unaffected.
const volumes_gallery_using_api_key = usingApiKey;
let resolve_volumes_gallery_ready;
const volumes_gallery_ready = new Promise(
	resolve => resolve_volumes_gallery_ready = resolve
);

usingApiKey = async function(...args) {
	await volumes_gallery_ready;
	return volumes_gallery_using_api_key(...args);
};

window.installVolumesGalleryRenderer = installer => {
	try {
		installer();
	} catch (error) {
		// Fail open: if the optimized renderer ever breaks, the shipped volumes.js
		// implementation still works rather than leaving the library page gated.
		console.error('Unable to install optimized volume gallery renderer', error);
	} finally {
		resolve_volumes_gallery_ready();
		delete window.installVolumesGalleryRenderer;
	};
};
