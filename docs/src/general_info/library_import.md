The 'Library Import' feature makes it possible to import an existing library of media files into Kapowarr. You could have an existing library because you used a different software before, or because you downloaded media manually. In that case, Library Import makes it easy to start using Kapowarr. You can find the Library Import feature in the web-UI at Volumes -> Library Import.

## Proposal

When you run Library Import, it will search for files in your root folders that aren't matched to any issues yet. It will then try to find the volume that the file is for on ComicVine. This list of files and ComicVine matches is presented to you (a.k.a. Library Import proposal). You can then change the matches in case Kapowarr guessed incorrectly. You can choose to apply the changed match to only the file, or to all files for the volume.

On the start screen, there are some settings that change the behaviour of Library Import:

- **Max folders scanned**  
Limit the proposal to this amount of folders (roughly equal to the amount of volumes). Setting this to a large amount increases the chance of hitting the ComicVine rate limit.

- **Apply limit to parent folder**  
Apply the folder limit (see previous bullet) to the parent folder instead of the folder. Enable this when each issue has its own sub-folder.

- **Only match English volumes**  
When Kapowarr is searching on ComicVine for a match to the file, only allow the match when it's an English release; it won't allow translations.

- **Folder(s) to scan**  
Allows you to supply a specific folder in a root folder to scan, instead of all root folders. Supports glob patterns (e.g. `/comics/Star Wars*`).

## Continuous Auto-Import

Continuous Auto-Import is intended for large existing libraries. It saves its folder queue and review holds in the Kapowarr database so browser navigation or an application restart does not throw away completed work.

The two maintenance controls deliberately do different amounts of work. **Reset & Re-evaluate Holds** throws away the current pass snapshot and builds a new pass from only the folders that still have actionable Review Holds, using the latest matching logic without pulling unrelated untracked folders back into the queue. **Rescan Untracked Library** is the explicit whole-library operation: it scans every configured root and builds a fresh pass from every folder Kapowarr currently considers unimported. That full set can be much larger than the Review Holds backlog. Neither operation removes comics that are already imported.

### Keeping files a folder is supposed to have

Some folders hold files on purpose that the matcher will never accept as issues of that volume: every issue of a magazine from 1955 on kept together in the current volume's folder, an omnibus filed with the single issues it collects, an annual filed with its parent run, an unofficial variant. Kapowarr is right to refuse them -- they are not issues of that volume -- but a refused file never enters the file database, and untracked-folder detection asks exactly that question, so the folder came back on every **Rescan Untracked Library** forever. Moving the files out was the only way to quiet it, which is the opposite of what the folder was arranged for.

Open the volume, choose **Manage Issues**, and press **Keep Unmatched**. Every file in the folder that nothing is currently matched to is recorded against the volume as an `adopted` general file. The files do not move or get renamed. They stop being offered for import, and every later file scan skips them.

Adopting claims no issue, so it does not mark anything as had: the issues the volume is genuinely missing stay wanted and stay searchable. Files already matched to an issue are never touched, so the action cannot unbind a run, and running it twice does nothing the second time. To undo it, set the file back to **Automatically Match** in the same window.

Before searching ComicVine, continuous import looks for local series identity in the same folder or comic archive. Mylar-style `series.json`, `metadata.json`, extensionless `cvinfo`, `cvinfo.txt`, and legacy `cvinfo.xml` can supply an exact ComicVine volume ID. Kapowarr also understands standard `ComicInfo.xml` beside the files or embedded in CBZ/ZIP/CBR/RAR archives when its standard `Web` field contains a ComicVine **volume** URL. CBR/RAR metadata is read through Kapowarr's bundled RAR tooling by extracting only the selected ComicInfo member into a temporary directory; the source archive is not modified. ComicInfo does not define a dedicated ComicVine-ID field, so a title/year alone is never silently converted into an external ID.

Current `4050-N`, historical Mylar `49-N`, full ComicVine volume URLs, and bare numeric IDs are understood for Mylar-style sidecars. ComicVine issue/story-arc URLs are not accepted as volume identity. If independent local metadata sources disagree, if embedded ComicInfo files disagree with each other, or if the local metadata title does not safely describe the parsed files in that folder, Kapowarr does not trust the ID unattended and records the reason in the application log. Local and embedded metadata reads are bounded so oversized metadata is ignored rather than loaded as ordinary ComicInfo.

Local metadata avoids an unnecessary ComicVine search, but adding a new volume still needs its volume and issue metadata. Continuous import therefore paces both search requests and those add-time metadata fetches independently. This deliberately leaves room for ordinary Kapowarr activity instead of treating the ComicVine ceiling as a target.

Large organizer folders are handled specially so one folder cannot look frozen for hours. Numeric shelf-order prefixes such as `001.) ElfQuest - Hidden Years #6` are ignored when determining the parsed series, which lets files for the same run group together instead of becoming one pseudo-series per filename. When a folder still contains many distinct parsed titles, Kapowarr first searches ComicVine once using the clean folder name and reuses that result pool for any groups it can resolve confidently. Groups not resolved by that broad pool still receive their normal exact-title search. While this work is happening, the task status includes the folder name and title-level progress even though the durable folder counter advances only when the whole folder reaches a safe checkpoint.

**Stop Import** is cooperative and recovery-safe. The stop request is sent to the worker immediately instead of waiting for a review snapshot first. Kapowarr acknowledges the request in the task status, leaves completed imports intact, returns an in-progress folder to the pending queue, and pauses the durable job so it can be resumed or reset. Long metadata pacing delays are checked for Stop at one-second intervals. If Kapowarr is already inside an actual provider request or committing one volume import, that atomic operation is allowed to finish before the pause boundary rather than being torn apart midway.

For unattended matching, title/language/special-version compatibility and basic issue coverage remain the safety gates. Continuous import then ranks viable candidates using filename evidence. Exact series-year agreement and whether a candidate's issue count can plausibly reach the highest issue number in the files are used as secondary corroboration, which resolves cases such as a 2015 run competing with a same-titled 1999 volume or issue #172 competing with a three-issue namesake. Missing or different year evidence is not by itself a reason to reject a unique candidate, so re-releases remain supported. Candidates that are still genuinely indistinguishable remain Review Holds instead of being selected arbitrarily.

Kapowarr's shared web-request machinery may consult FlareSolverr when supported requests encounter Cloudflare protection. FlareSolverr does not increase or bypass ComicVine API quotas, so ComicVine metadata operations still require their own pacing.

## Importing

When you are happy with the proposal, you have two options: 'Import' and 'Import and Rename'. Clicking 'Import' will make Kapowarr add all the volumes and set their volume folder to the folder that the file is in. Clicking 'Import and Rename' will make Kapowarr add all the volumes and move the files into the automatically generated volume folder, after which it will rename them. If a volume is already added to the library, then clicking 'Import' will move the matched files to the volume folder. Clicking 'Import and Rename' will move the matched files to the volume folder and rename them then.
