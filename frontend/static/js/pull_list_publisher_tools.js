const PUBLISHER_PROFILES = {
	'ac comics': 'Independent U.S. publisher with roots in the 1980s, especially associated with superhero, adventure, and Golden Age revival material such as Femforce.',
	'ablaze publishing': 'Independent publisher of international, creator-owned, and licensed comics and graphic novels, including translated European and Asian work.',
	'aftershock comics': 'Independent U.S. publisher known for creator-owned genre comics. Its catalogue includes horror, science fiction, fantasy, crime, and superhero-adjacent titles.',
	'ahoy comics': 'Independent U.S. publisher known for creator-owned comics that often mix genre storytelling with satire, humor, and literary extras.',
	'alien books': 'Independent comics publisher working across licensed and creator-owned material, including English-language editions and revival projects.',
	'american mythology productions': 'Independent U.S. publisher known for licensed pop-culture comics, classic-character revivals, humor, horror, and creator-owned releases.',
	'amp comics': 'Small-press comics publisher appearing in the North American direct market, focused on creator-driven releases.',
	'antarctic press': 'Independent U.S. publisher with a long-running catalogue of creator-driven comics across many genres, including action, fantasy, science fiction, and manga-influenced work.',
	'archie comics': 'Long-running U.S. publisher best known for Archie, Betty, Veronica, Jughead, Riverdale, and related characters, alongside occasional genre and licensed projects.',
	'aspen comics': 'Independent U.S. publisher founded around the work of artist Michael Turner, known for fantasy and superhero titles including Fathom and Soulfire.',
	'avatar press': 'Independent U.S. publisher known for mature-reader horror, science fiction, and creator-owned work from prominent comics writers and artists.',
	'black mask studios': 'Independent U.S. publisher focused on creator-owned comics, often with political, punk, crime, science-fiction, and counterculture sensibilities.',
	'blood moon comics': 'Small independent comics publisher focused on creator-owned genre material, including horror, science fiction, fantasy, and action.',
	'boom studios': 'Independent U.S. publisher of original and licensed comics. Its family of imprints has included BOOM!, KaBOOM!, and Archaia.',
	'cex publishing': 'Independent comics publisher focused on creator-owned series and graphic novels across science fiction, fantasy, horror, adventure, and other genres.',
	'coffin comics': 'Independent publisher best known for Brian Pulido’s Lady Death universe and related supernatural, fantasy, and horror comics.',
	'comixology originals': 'Amazon’s digital-first comics imprint for creator-owned and licensed comics and graphic novels, with some titles later receiving print editions.',
	'dark horse comics': 'Independent U.S. publisher known for creator-owned comics, graphic novels, manga, archival editions, and licensed properties.',
	'dc comics': 'Major U.S. superhero publisher behind Superman, Batman, Wonder Woman, the Justice League, and the wider DC universe.',
	'denpa': 'North American publisher specializing in English-language editions of Japanese manga, often emphasizing distinctive, literary, cult, or visually adventurous titles.',
	'drawn and quarterly': 'Independent publisher known for literary comics, graphic novels, memoir, international work, and alternative cartooning.',
	'dstlry': 'Creator-focused comics publisher launched in the 2020s, emphasizing premium-format releases and creator-owned work.',
	'dynamite entertainment': 'U.S. publisher known for licensed characters, pulp and adventure properties, adaptations, and creator-owned comics.',
	'fantagraphics': 'Independent publisher known for alternative comics, art comics, graphic novels, underground work, and archival editions of historically important cartooning.',
	'fire ant entertainment': 'Small independent comics publisher appearing in the direct-market release catalogue, focused on creator-owned material.',
	'first second': 'Graphic-novel imprint of Macmillan known for creator-driven fiction, nonfiction, memoir, young-reader work, and literary comics.',
	'heavy metal': 'Publisher associated with Heavy Metal magazine, the English-language science-fiction and fantasy comics magazine descended from the French Métal Hurlant tradition and known for mature illustrated storytelling.',
	'humanoids': 'Publisher of international and creator-owned comics with roots in the French Métal Hurlant tradition and European graphic storytelling.',
	'idw publishing': 'U.S. publisher of original and licensed comics, with a catalogue that has included major film, television, game, toy, and creator-owned properties.',
	'image comics': 'Creator-owned comics publisher founded by comics artists in 1992. Its catalogue spans superheroes, science fiction, horror, fantasy, crime, and many other genres.',
	'j novel club': 'English-language digital publisher specializing in Japanese light novels and manga, with many series released in serialized digital form before collected editions.',
	'kodansha': 'Major Japanese publisher. Its English-language manga catalogue includes releases through Kodansha USA and related imprints.',
	'mad cave studios': 'Independent U.S. publisher of genre comics and graphic novels, including science fiction, fantasy, horror, crime, adventure, and young-reader material.',
	'magnetic press': 'Publisher specializing in international graphic novels, art-driven comics, and translated works, particularly from European and Asian creators.',
	'marvel comics': 'Major U.S. superhero publisher behind Spider-Man, the X-Men, the Avengers, Daredevil, the Fantastic Four, and many connected Marvel-universe titles.',
	'massive publishing': 'Independent U.S. publisher and publishing partner for creator-owned comics, licensed projects, and boutique direct-market releases.',
	'midnight factory': 'Independent comics publisher focused primarily on horror, dark fantasy, thriller, and other genre material.',
	'oni press': 'Independent U.S. publisher focused on creator-driven comics and graphic novels across many genres, including literary, humor, horror, fantasy, and young-reader work.',
	'papercutz': 'Graphic-novel publisher focused heavily on children, families, licensed properties, humor, adventure, and translated European comics.',
	'rebellion': 'UK publisher and media company behind 2000 AD and Judge Dredd, with a large catalogue of British science-fiction, fantasy, action, and archival comics.',
	'scout comics': 'Independent U.S. publisher and entertainment company focused on creator-owned comics across horror, science fiction, fantasy, crime, and other genres.',
	'seven seas entertainment': 'North American publisher specializing in translated manga, light novels, webtoons, danmei, and related works from Japan, Korea, China, and elsewhere.',
	'skybound': 'Creator-focused entertainment company and Image Comics publishing partner founded by Robert Kirkman, with genre, licensed, and creator-owned titles.',
	'source point press': 'Independent U.S. publisher known for creator-owned comics, especially horror, science fiction, fantasy, crime, and other genre projects.',
	'square enix manga and books': 'English-language publishing line for manga, novels, art books, and other books connected to Square Enix properties and Japanese creators.',
	'titan comics': 'UK-based publisher of original and licensed comics and graphic novels, including many television, film, game, and classic-comics properties.',
	'top cow productions': 'Image-associated comics studio and publisher known for properties including Witchblade, The Darkness, Cyberforce, and other creator-owned genre series.',
	'udon entertainment': 'Canadian publisher and art studio known for manga, art books, video-game properties, and comics including Street Fighter and other licensed franchises.',
	'valiant entertainment': 'U.S. publisher centered on a shared superhero universe including characters such as X-O Manowar, Bloodshot, Harbinger, Ninjak, and Faith.',
	'vault comics': 'Independent U.S. publisher best known for creator-owned science fiction, fantasy, horror, and other speculative comics.',
	'vertical comics': 'English-language manga publishing label associated with Kodansha USA, known for literary, classic, cult, and visually distinctive Japanese works.',
	'viz media': 'English-language publisher and distributor specializing in Japanese manga and related media, with major shonen, shojo, seinen, and family catalogues.',
	'whatnot publishing': 'Independent publisher connected to the Whatnot collectibles marketplace, focused on creator-owned comics, genre projects, and direct-market releases.',
	'yen press': 'English-language publisher specializing in manga, light novels, manhwa, and related Japanese and Korean works.',
	'zenescope entertainment': 'U.S. publisher known for its Grimm Fairy Tales line and connected fantasy, horror, adventure, and licensed titles.'
};

const PUBLISHER_ALIASES = {
	'2000 ad': 'rebellion',
	'2000ad': 'rebellion',
	'ac': 'ac comics',
	'ablaze': 'ablaze publishing',
	'aftershock': 'aftershock comics',
	'ahoy': 'ahoy comics',
	'alien': 'alien books',
	'american mythology': 'american mythology productions',
	'amp': 'amp comics',
	'antarctic': 'antarctic press',
	'archie': 'archie comics',
	'aspen': 'aspen comics',
	'avatar': 'avatar press',
	'black mask': 'black mask studios',
	'blood moon': 'blood moon comics',
	'boom': 'boom studios',
	'boom studios inc': 'boom studios',
	'cex': 'cex publishing',
	'coffin': 'coffin comics',
	'comixology': 'comixology originals',
	'dark horse': 'dark horse comics',
	'dc': 'dc comics',
	'drawn quarterly': 'drawn and quarterly',
	'dynamite': 'dynamite entertainment',
	'fire ant': 'fire ant entertainment',
	'heavy metal magazine': 'heavy metal',
	'heavy metal entertainment': 'heavy metal',
	'idw': 'idw publishing',
	'image': 'image comics',
	'j novel': 'j novel club',
	'kodansha comics': 'kodansha',
	'mad cave': 'mad cave studios',
	'magnetic': 'magnetic press',
	'marvel': 'marvel comics',
	'massive': 'massive publishing',
	'oni': 'oni press',
	'scout': 'scout comics',
	'seven seas': 'seven seas entertainment',
	'source point': 'source point press',
	'square enix': 'square enix manga and books',
	'titan': 'titan comics',
	'top cow': 'top cow productions',
	'udon': 'udon entertainment',
	'valiant': 'valiant entertainment',
	'vault': 'vault comics',
	'vertical': 'vertical comics',
	'viz': 'viz media',
	'whatnot': 'whatnot publishing',
	'yen': 'yen press',
	'zenescope': 'zenescope entertainment'
};

function normalizePublisherName(name) {
	return String(name || '')
		.toLowerCase()
		.replace(/&/g, 'and')
		.replace(/[^a-z0-9]+/g, ' ')
		.trim();
};

function publisherProfileKey(name) {
	const normalized = normalizePublisherName(name);
	if (PUBLISHER_PROFILES[normalized])
		return normalized;
	if (PUBLISHER_ALIASES[normalized])
		return PUBLISHER_ALIASES[normalized];

	const without_company_suffix = normalized
		.replace(/\b(?:llc|inc|incorporated|ltd|limited|company|co)\b/g, '')
		.replace(/\s+/g, ' ')
		.trim();
	if (PUBLISHER_PROFILES[without_company_suffix])
		return without_company_suffix;
	if (PUBLISHER_ALIASES[without_company_suffix])
		return PUBLISHER_ALIASES[without_company_suffix];

	return null;
};

function publisherDescription(name) {
	const key = publisherProfileKey(name);
	if (key)
		return PUBLISHER_PROFILES[key];

	return 'Independent or specialist publisher listed by the release catalogue. Kapowarr does not have a more specific curated profile for this label yet; catalogue names can also represent imprints or distribution labels.';
};

function publisherAutomationLabel(publisher) {
	if (publisher.root_folder_id === null)
		return 'Off';
	return publisher.auto_search ? 'Auto-add & grab' : 'Auto-add & monitor';
};

function publisherForRuleElement(element) {
	const rule = element.closest('.publisher-rule');
	if (!rule)
		return null;
	const name = rule.querySelector('.publisher-rule-name').innerText;
	return pullListState.publishers.find(item => item.publisher === name) || null;
};

function decoratePublisherInfoButton(button) {
	const publisher = publisherForRuleElement(button);
	if (!publisher)
		return null;
	const description = publisherDescription(publisher.publisher);
	button.dataset.publisherTooltip = description;
	button.title = description;
	button.setAttribute(
		'aria-label',
		`About ${publisher.publisher}: ${description}`
	);
	return publisher;
};

function openPublisherInfo(publisher) {
	const dialog = document.querySelector('#publisher-info-dialog');
	if (!dialog)
		return;

	document.querySelector('#publisher-info-name').innerText = publisher.publisher;
	document.querySelector('#publisher-info-description').innerText =
		publisherDescription(publisher.publisher);
	document.querySelector('#publisher-info-stored').innerText =
		String(publisher.release_count || 0);
	document.querySelector('#publisher-info-week').innerText =
		String(publisherWeekCount(publisher));
	document.querySelector('#publisher-info-automation').innerText =
		publisherAutomationLabel(publisher);

	const search = document.querySelector('#publisher-info-search');
	search.href = 'https://en.wikipedia.org/wiki/Special:Search?search='
		+ encodeURIComponent(`${publisher.publisher} comics publisher`);

	if (typeof dialog.showModal === 'function')
		dialog.showModal();
	else
		dialog.setAttribute('open', '');
};

function closePublisherInfo() {
	const dialog = document.querySelector('#publisher-info-dialog');
	if (!dialog)
		return;
	if (typeof dialog.close === 'function')
		dialog.close();
	else
		dialog.removeAttribute('open');
};

function enableGrabAllPublishers(api_key, button) {
	const root_folder_id = parseInt(PullListEls.root_folder.value);
	if (Number.isNaN(root_folder_id)) {
		alert('Add a root folder before enabling publisher automation.');
		return;
	};

	const count = pullListState.publishers.length;
	if (!count) {
		setCheckStatus('There are no listed publishers to enable yet.', true);
		return;
	};

	const confirmed = window.confirm(
		`Set all ${count} currently listed publishers to Auto-add & grab using `
		+ 'the selected root folder? Publishers discovered later will remain off '
		+ 'until you apply this again.'
	);
	if (!confirmed)
		return;

	button.disabled = true;
	sendAPI('POST', '/pulllist/publishers/grab-all', api_key, {}, {root_folder_id})
		.then(response => response.json())
		.then(json => loadPublishers(api_key).then(() => json))
		.then(json => setCheckStatus(
			`Auto-add & grab enabled for ${json.result.updated} publishers.`
		))
		.catch(error => {
			reportPullListClientError(api_key, 'publisher grab all', error);
			setCheckStatus(
				`Could not enable all publishers: ${pullListErrorMessage(error)}`,
				true
			);
		})
		.finally(() => {
			button.disabled = false;
		});
};

usingApiKey().then(api_key => {
	const rule_list = document.querySelector('#publisher-rule-list');
	const grab_all = document.querySelector('#publisher-grab-all');
	const close = document.querySelector('#publisher-info-close');
	const dialog = document.querySelector('#publisher-info-dialog');

	if (rule_list) {
		rule_list.addEventListener('pointerover', event => {
			const info_button = event.target.closest('.publisher-info-button');
			if (info_button)
				decoratePublisherInfoButton(info_button);
		});
		rule_list.addEventListener('focusin', event => {
			const info_button = event.target.closest('.publisher-info-button');
			if (info_button)
				decoratePublisherInfoButton(info_button);
		});
		rule_list.addEventListener('click', event => {
			const info_button = event.target.closest('.publisher-info-button');
			if (!info_button)
				return;
			const publisher = decoratePublisherInfoButton(info_button);
			if (publisher)
				openPublisherInfo(publisher);
		});
	};

	if (grab_all)
		grab_all.onclick = () => enableGrabAllPublishers(api_key, grab_all);
	if (close)
		close.onclick = closePublisherInfo;
	if (dialog) {
		dialog.addEventListener('click', event => {
			if (event.target === dialog)
				closePublisherInfo();
		});
	};
});