// Poster-gallery renderer for large libraries.
//
// Both views now build their complete skeleton up front; what is different
// here is that a poster card carries a cover and a table row does not. So
// this establishes cheap geometry and text for the whole result set, then
// hydrates the expensive part -- the images -- only around the viewport.
window.installVolumesGalleryRenderer(() => {
	const original_build_library_view = buildLibraryView;
	const original_clear_library_view = clearLibraryView;
	let cover_observer = null;

	function disconnectCoverObserver() {
		if (cover_observer !== null) {
			cover_observer.disconnect();
			cover_observer = null;
		};
	};

	// `rect` comes from the IntersectionObserver entry, never from
	// `getBoundingClientRect`. This used to measure each image itself,
	// which forces a synchronous style and layout flush -- against a
	// document holding a poster card for every volume in the library, all
	// of them materialized up front by design. One observer callback
	// carries every image that entered the 1800px overscan band during a
	// fling, so a single scroll gesture could stall the main thread on
	// dozens of full-document layouts in a row: the scroll visibly waited
	// on the covers.
	//
	// The observer has already measured all of it. `entry.boundingClientRect`
	// is that measurement, computed off the critical path, and it is the
	// same rectangle this asked the DOM for.
	function loadCover(img, rect, bounds) {
		if (!img.dataset.src)
			return;

		if (rect !== null && bounds !== null && 'fetchPriority' in img) {
			// On screen now, or merely inside the overscan band. Measured
			// against the scroller rather than the viewport, so a card hidden
			// behind the tool bar is not mistaken for a visible one.
			img.fetchPriority =
				rect.bottom >= bounds.top && rect.top <= bounds.bottom
					? 'high'
					: 'low';
		};
		img.src = img.dataset.src;
		delete img.dataset.src;
	};

	// The element that actually scrolls, which is not the document. `general.css`
	// gives `main > *:not(.tool-bar-container)` `overflow-y: auto` and a fixed
	// height, so `#library-container` is the scroller and the page behind it
	// never moves.
	//
	// That matters more than it looks. An IntersectionObserver left on the
	// default root observes against the viewport, and `rootMargin` grows the
	// viewport rect -- but the intersection is still clipped by every
	// overflow ancestor, and those clips are not grown by anything. So a card
	// one row below the bottom of `#library-container` is clipped out and
	// reports no intersection no matter how large the margin is: the 1800px
	// overscan band below was silently worth zero pixels, and a cover was
	// requested only once the card was literally on screen. That is the
	// "loads one row at a time before letting me scroll" behaviour exactly.
	//
	// Rooting the observer at the scroller is what makes `rootMargin` mean
	// what it says: the band now expands the clip that was doing the
	// rejecting.
	function coverObserverRoot() {
		const container = library_els.pages.view;
		return container instanceof Element ? container : null;
	};

	// Several screens of overscan means a normal fling should encounter
	// already-requested covers without downloading all 3,000 at once.
	const COVER_OVERSCAN_PX = 1800;

	function observeCovers(images) {
		disconnectCoverObserver();

		if (typeof window.IntersectionObserver !== 'function') {
			// Native loading=lazy is still a useful fallback on older WebViews.
			// Everything is requested at once here, so a priority hint would
			// have nothing to order.
			images.forEach(img => loadCover(img, null, null));
			return;
		};

		cover_observer = new IntersectionObserver(
			entries => {
				// Measure the root once for the whole batch rather than once per
				// image. `rootBounds` is the observer's own measurement, so this
				// still touches no layout.
				// `rootBounds` already has the overscan margin added, so it
				// describes the whole band rather than the visible part of it.
				// Take the margin back off to recover what the user can
				// actually see -- otherwise every card in the band would be
				// requested at high priority and the ordering would be worth
				// nothing.
				const root_bounds = entries.length ? entries[0].rootBounds : null;
				let bounds;
				if (root_bounds !== null) {
					bounds = {
						top: root_bounds.top + COVER_OVERSCAN_PX,
						bottom: root_bounds.bottom - COVER_OVERSCAN_PX
					};
				} else {
					// A root the observer could not measure (a cross-origin
					// frame, or a hidden container).
					bounds = {
						top: 0,
						bottom: window.innerHeight
							|| document.documentElement.clientHeight
					};
				};

				entries.forEach(entry => {
					if (!entry.isIntersecting)
						return;
					loadCover(
						entry.target,
						entry.boundingClientRect,
						bounds
					);
					cover_observer.unobserve(entry.target);
				});
			},
			{
				root: coverObserverRoot(),
				rootMargin: `${COVER_OVERSCAN_PX}px 0px ${COVER_OVERSCAN_PX}px 0px`
			}
		);
		images.forEach(img => cover_observer.observe(img));
	};

	function buildPosterShell(entry, volume, api_key, fragment, images) {
		const list_entry = pre_build_els.list_entry.cloneNode(true);
		list_entry.ariaLabel =
			`View the volume ${volume.title} (${volume.year}) Volume ${volume.volume_number}`;
		list_entry.href = `${url_base}/volumes/${volume.id}`;
		list_entry.dataset.volumeId = volume.id;

		const img = list_entry.querySelector('.list-img');
		// Empty src can request the current document in some browsers. Keep the
		// element completely source-less until the observer chooses to hydrate it.
		img.removeAttribute('src');
		img.loading = 'lazy';
		img.decoding = 'async';
		img.dataset.src =
			`${url_base}/api/volumes/${volume.id}/cover?api_key=${api_key}`;
		images.push(img);

		const list_title = list_entry.querySelector('.list-title');
		list_title.innerText =
		list_title.title =
			`${volume.title} (${volume.year})`;
		list_entry.querySelector('.list-volume').innerText =
			`Volume ${volume.volume_number}`;

		entry.list_entry = list_entry;
		entry.download_status = getVolumeDownloadStatus(volume.id);
		entry.renderMonitored();
		entry.renderProgressBar();
		entry.renderDownloadStatus();
		fragment.appendChild(list_entry);
	};

	function buildCompletePosterIndex(api_key, generation, on_first_batch) {
		clearLibraryView('list');
		library_built_views.list = true;
		library_render_pending.list = true;

		// Give the loading state a paint before doing the one bulk shell build.
		scheduleLibraryPaint(() => {
			if (generation !== library_render_generation)
				return;

			const fragment = document.createDocumentFragment();
			const images = [];
			for (const volume of library_volumes) {
				let entry = library_entries.get(volume.id);
				if (entry === undefined)
					entry = createLibraryEntry(volume, api_key);
				buildPosterShell(entry, volume, api_key, fragment, images);
			};

			library_els.views.list.insertBefore(
				fragment,
				library_els.views.list.querySelector('.space-taker')
			);
			library_render_pending.list = false;
			library_els.mass_edit.button.disabled = false;

			if (on_first_batch !== null)
				on_first_batch();

			// Observe only after the complete shell grid is attached, so geometry and
			// scroll height are final before any cover requests begin.
			observeCovers(images);
		});
	};

	clearLibraryView = function(view) {
		if (view === 'list')
			disconnectCoverObserver();
		return original_clear_library_view(view);
	};

	buildLibraryView = function(view, api_key, generation, on_first_batch=null) {
		if (view !== 'list') {
			return original_build_library_view(
				view,
				api_key,
				generation,
				on_first_batch
			);
		};

		buildCompletePosterIndex(api_key, generation, on_first_batch);
	};
});