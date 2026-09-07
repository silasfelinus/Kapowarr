<p align="center">
    <img src="./frontend/static/img/favicon.svg" alt="Kapowarr" style="margin: 20px 0; width: 15rem;">
</p>
<p align="center">
    <a href="https://github.com/silasfelinus/Kapowarr/pkgs/container/kapowarr"><img src="https://img.shields.io/badge/ghcr.io-silasfelinus%2Fkapowarr-blue"></a>
    <a href="https://github.com/silasfelinus/Kapowarr/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/silasfelinus/Kapowarr/tests.yml?branch=main&label=tests"></a>
    <a href="https://github.com/silasfelinus/Kapowarr/blob/main/LICENSE"><img src="https://img.shields.io/github/license/silasfelinus/Kapowarr?color=blue"></a>
    <a href="https://github.com/Casvt/Kapowarr"><img src="https://img.shields.io/badge/fork%20of-Casvt%2FKapowarr-blue"></a>
    <a href="https://ko-fi.com/casvt"><img src="https://img.shields.io/badge/Donate%20to%20upstream-Ko--Fi-blue"></a>
</p>

# Kapowarr

**Build and manage a comic book library, in the *arr tradition.** Add volumes, point them at folders, and let Kapowarr find, download, rename, convert and file the issues.

This repository is a fork of **[Casvt/Kapowarr](https://github.com/Casvt/Kapowarr)** — the original project, and the reason this one exists. Everything upstream does, this does. The fork adds acquisition breadth, tools for libraries too large to shepherd by hand, and a reader.

Fork images are published at `ghcr.io/silasfelinus/kapowarr:latest`.

---

## Why this fork

Upstream Kapowarr is built around adding volumes one at a time and downloading them from direct sources. That works well. It gets harder when you arrive with 6,000 volumes and 57,000 files already on disk, organised by a decade of different tools, and you want Usenet and torrents in the mix.

That's the gap this fork works on.

**Getting an existing library in.** Continuous Library Import walks every root folder in the background — resumable, checkpointed in SQLite, paced conservatively against the metadata API. It imports what it is confident about and holds the rest for review rather than guessing. A single pass across a 490-folder library imported 456 volumes and held 40.

**Getting files in that arrived some other way.** A watched folder imports anything dropped into it. Orphan recovery sweeps the download folder for files that finished but never made it to the library — including ones whose filename matches two volumes, which it resolves against what Kapowarr actually asked for.

**More places to get comics from.** SABnzbd and NZBGet for Usenet, qBittorrent and Transmission for torrents, Newznab and Torznab indexers, plus the direct sources upstream supports.

**More than one opinion about metadata.** ComicVine remains the default, with Metron and the Grand Comics Database alongside it. Identity is tracked per provider rather than pretending every ID is a ComicVine ID, so a volume can be resolved through Metron when ComicVine is rate-limited.

**Knowing what it did.** Health checks, Discord and webhook notifications, a built-in log viewer, scheduled database backups, and diagnostics that name the volumes competing for a file instead of saying "more than one".

---

## Features

### Library

- Import an existing library — one pass, in the background, resumable, with review holds for anything ambiguous
- Watched-folder import for files that arrive outside Kapowarr
- Orphan recovery for downloads that finished but never got filed
- Manual import and per-file match editing when you want the last word
- Rename, move and convert on import or on demand; archive extraction and repacking
- Reading lists, and a built-in reader for CBZ/ZIP, loose images and PDFs

### Acquisition

- **Usenet:** SABnzbd, NZBGet, Newznab indexers
- **Torrents:** qBittorrent, Transmission, Torznab indexers
- **Direct:** GetComics, Pixeldrain, Mega and others
- Search monitored volumes in bulk, or search manually and choose yourself
- Release feeds, weekly pull lists, import lists and a GetComics Discover browser
- Quality preferences, size limits, provenance tracking and archive integrity checks
- Blocklist for releases you never want offered again

### Metadata

- ComicVine, Metron and Grand Comics Database, with per-provider identity
- Cross-referenced fallback when one provider is unavailable or rate-limited
- Portable metadata written alongside your files

### Operations

- System status for metadata providers, download clients and root folders
- Discord and generic-webhook notifications
- Scheduled database backups
- Built-in log viewer with level filtering
- Large libraries stay responsive: galleries hydrate progressively rather than blocking the page

---

## Installation

Docker, using the fork image:

```yaml
services:
  kapowarr:
    container_name: kapowarr
    image: ghcr.io/silasfelinus/kapowarr:latest
    environment:
      - PUID=0
      - PGID=0
      - TZ=Etc/UTC
    volumes:
      - "kapowarr-db:/app/db"
      - "/path/to/download_folder:/app/temp_downloads"
      - "/path/to/comics:/comics"
    ports:
      - 5656:5656

volumes:
  kapowarr-db:
```

The included `docker-compose.yml` already points at this image. For everything else — bare-metal installation, first-run setup, settings reference — upstream's [installation documentation](https://casvt.github.io/Kapowarr/installation/installation/) applies to this fork unchanged.

---

## Relationship to upstream

Upstream is the original project and the maintainer of record. This fork keeps its GPL-3.0 license and attribution, tracks its changes, and sends nothing back it hasn't tested.

If Kapowarr is useful to you, [support the upstream author](https://ko-fi.com/casvt).

- Upstream issues, docs and community: [repository](https://github.com/Casvt/Kapowarr) · [documentation](https://casvt.github.io/Kapowarr/) · [Discord](https://discord.gg/5gWtW3ekgZ) · [r/kapowarr](https://www.reddit.com/r/kapowarr/)
- Fork-specific problems belong on [this repository's issues](https://github.com/silasfelinus/Kapowarr/issues) — please don't take fork bugs to upstream.
- [`FORK_MAINTENANCE.md`](./FORK_MAINTENANCE.md) describes how this fork tracks upstream.
- [`docs/`](./docs) covers metadata providers, acquisition quality and provenance, and *arr feature parity.

## Contributing

Issues and pull requests are welcome. The test suites run on every push:

```bash
python -m unittest discover -s ./tests -p '*.py'
node --test tests/Tfrontend/*.test.js
```

Tests are named for the behaviour they protect, and most of them exist because something went wrong in a real library first.

## Screenshots

![](https://github.com/user-attachments/assets/04656209-288e-4263-a2df-93e06758c443)
![](https://github.com/user-attachments/assets/3fa8177c-f016-4cbd-b73e-6b577840b08e)
![](https://github.com/user-attachments/assets/69d59c21-3983-4acc-8777-ae0c7b65fdff)
