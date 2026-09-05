function showWindow(id) {
	// Deselect all windows
	document.querySelectorAll('.window > section').forEach(window => {
		window.removeAttribute('show-window');
	});

	// Select the correct window
	document.querySelector(`.window > section#${id}`).setAttribute('show-window', '');

	// Show the window
	document.querySelector('.window').setAttribute('show-window', '');
};

function showLoadWindow(id) {
	// Deselect all windows
	document.querySelectorAll('.window > section').forEach(window => {
		window.removeAttribute('show-window');
	});

	// Select the correct window
	const loading_window = document.querySelector(`.window > section#${id}`).dataset.loading_window;
	if (loading_window !== undefined) document.querySelector(`.window > section#${loading_window}`).setAttribute('show-window', '');

	// Show the window
	document.querySelector('.window').setAttribute('show-window', '');
};

function closeWindow() {
	// A window that started a request owns it. Hiding the dialog used to
	// leave the request running on one of the browser's six connections to
	// this host, with no way from the page to stop it -- so Cancel looked
	// like it did nothing and reopening handed back the same stuck search.
	//
	// Declared by whichever page has such a request; pages without one are
	// unaffected.
	if (typeof window.abandonMatchSearch === 'function')
		window.abandonMatchSearch();

	document.querySelector('.window').removeAttribute('show-window');
};

// code run on load

document.querySelector('body').onkeydown = e => {
	if (
		e.code === "Escape"
		&&
		document.querySelector('.window[show-window]')
	) {
		e.stopImmediatePropagation();
		closeWindow();
	};
};

document.querySelector('.window').onclick = e => {
	e.stopImmediatePropagation();
	closeWindow();
};

document.querySelectorAll('.window > section').forEach(
	el => el.onclick = e => e.stopImmediatePropagation()
);

document.querySelectorAll(
	'.window > section :where(button[title="Cancel"], button.cancel-window)'
).forEach(e => {
	e.onclick = f => closeWindow();
});
