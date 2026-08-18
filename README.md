<p align="center">
    <img src="./frontend/static/img/favicon.svg" alt="Kapowarr" style="margin: 20px 0; width: 15rem;">
</p>
<p align="center">
    <a href="https://hub.docker.com/r/mrcas/kapowarr"><img src="https://img.shields.io/docker/pulls/mrcas/kapowarr?color=blue"></a>
    <a href="https://github.com/Casvt/Kapowarr"><img src="https://img.shields.io/github/stars/Casvt/Kapowarr?style=flat&color=blue"></a>
    <a href="https://ko-fi.com/casvt"><img src="https://img.shields.io/badge/Donate-Ko--Fi-blue"></a>
    <a href="https://github.com/Casvt/Kapowarr/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Casvt/Kapowarr?color=blue"></a>
</p>

# Kapowarr

> **Personal fork:** This repository is Silas Felinus's fork of [Casvt/Kapowarr](https://github.com/Casvt/Kapowarr). It keeps the upstream GPL-3.0 license and attribution while extending Kapowarr as an aggregation-first comic manager. Fork additions now include large-library continuous import, SABnzbd/Newznab support, notifications and health checks, interface/QoL work, and a lightweight built-in reader. Fork container builds are published as `ghcr.io/silasfelinus/kapowarr:latest`; upstream remains the original project and maintainer. See [`FORK_MAINTENANCE.md`](./FORK_MAINTENANCE.md) for how this fork tracks upstream, and [`AGENT_WORKFLOW_NOTES.md`](./AGENT_WORKFLOW_NOTES.md) for AI-agent workflow hazards.

Kapowarr is a software to build and manage a comic book library, fitting in the *arr suite of software.

Kapowarr allows you to build a digital library of comics. You can add volumes, map them to a folder and start managing! Download, rename, move and convert issues of the volume (including TPBs, One Shots, Hard Covers, and more). The whole process is automated and can be customised in the settings.

Featured on [Noted](https://noted.lol/kapowarr/) and [Respectlytics](https://respectlytics.com/).

## Features

- Run a "Search Monitored" to download whole volumes with one click
- Or use "Manual Search" to decide yourself what to download
- Import your existing library right into Kapowarr
- Support for all major operating systems
- Download using DDL, Pixeldrain, Mega and many other services
- Downloaded files automatically get moved wherever you want and renamed in the format you desire
- Archive files can be extracted and their contents renamed after downloading or with a single click
- The recognisable UI from the *arr suite of software

## Fork highlights

- **Continuous large-library import:** resumable background importing with conservative ComicVine pacing, confidence-gated automatic matches, live review holds, stop/resume controls, durable SQLite checkpoints, reset/re-evaluate support, and JSONL match diagnostics.
- **Usenet acquisition:** SABnzbd is supported as an external download client, and Newznab-compatible indexers participate in Kapowarr search and can hand NZBs into the normal download/import queue.
- **Operational visibility:** Discord/generic-webhook notifications, bounded external-client tests, and a System Status health panel for ComicVine, download clients, and root folders.
- **Large-library UI:** large volume galleries reveal an initial batch immediately and hydrate the remainder during browser-idle time instead of blocking the page.
- **Built-in reading:** downloaded CBZ/ZIP archives, loose images, and PDFs can be opened directly in Kapowarr. CBR/RAR remain supported library/archive formats, with reader support for those formats still to come.
- **Fork personality and deployment:** configurable application title, rotating loading lines, launch flair, automated branch cleanup, and GHCR images at `ghcr.io/silasfelinus/kapowarr:latest`.

### Aggregation roadmap

Aggregation remains the fork's primary focus. The next expansion areas are live hardening of the Usenet path; Torznab with Prowlarr/Jackett-style torrent indexers; more complete torrent lifecycle behavior such as hardlinks and pack handling; weekly release/pull lists and story arcs; Wanted and watched-folder workflows; a GetComics Discover browser with library-informed recommendations; alternate metadata providers such as Metron; and additional source adapters including Anna's Archive and public-download Internet Archive material.

## Installation, support and documentation

- For this fork's Docker/Compose deployment, use `ghcr.io/silasfelinus/kapowarr:latest` (the included `docker-compose.yml` already points there).
- For upstream instructions on how to install Kapowarr, see the [installation documentation](https://casvt.github.io/Kapowarr/installation/installation/).
- For upstream support, a [Discord server](https://discord.gg/5gWtW3ekgZ) and [subreddit](https://www.reddit.com/r/kapowarr/) are available, or [make an issue](https://github.com/Casvt/Kapowarr/issues).
- For upstream planning of features or their progress, check the [project board](https://github.com/users/Casvt/projects/5).
- For upstream documentation, see the [documentation hub](https://casvt.github.io/Kapowarr/).
- For donations to the upstream author, go to the [Ko-Fi page](https://ko-fi.com/casvt).

## Screenshots

![](https://github.com/user-attachments/assets/04656209-288e-4263-a2df-93e06758c443)
![](https://github.com/user-attachments/assets/3fa8177c-f016-4cbd-b73e-6b577840b08e)
![](https://github.com/user-attachments/assets/69d59c21-3983-4acc-8777-ae0c7b65fdff)
