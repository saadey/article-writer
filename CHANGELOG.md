# Changelog

All notable changes to Article Writer. Versions follow [Semantic Versioning](https://semver.org).

## [1.1.0] - 2026-10-18
### Added
- Simpler navigation: Dashboard, Find & pick and Drafts with live counts; everything else in one Menu.
- Getting started checklist on the dashboard and a Help page with a guide, tips, limits and FAQs.
- Version shown next to the app name (header, page titles, Mac menu bar).
- Terms of use page, shown once on first launch (and again if the terms change).
- App icon and logo (app, browser tab, Mac menu bar).
- About page with version, author, privacy statement, services and open-source credits.
- Full license texts of bundled open-source software (About → Licenses), included in every download.
- Footer with version, copyright and license; "About Article Writer" in the Mac menu bar menu.
- Version and copyright details in the Mac and Windows app files.
- MIT license, responsible-use guidance, changelog, security policy and contribution guide.

### Changed
- Transcripts now come from Gemini by default (Google's official API). Reading YouTube captions directly is an
  unofficial method YouTube's terms don't permit; it's still available as an option at your own risk.
- To follow YouTube's API policies, search results you haven't acted on are removed after 30 days and video
  statistics are erased after 30 days.
- Platform-neutral wording: works with any blog or newsletter editor ("Copy formatted article").
- New installs start with a neutral example project.

### Fixed
- Command line `status` crashed when a project had found or dismissed videos.
- "Save and test keys" crashed if no project had been opened yet.
- Unexpected errors now show a friendly page and are written to the log.
- Very long videos: transcripts are no longer cut short, and the "cut off" message gives the right advice.
- Menus close when you click elsewhere or press Escape.
- An open page now tells you when the app has been closed, instead of retrying silently.

## [1.0.2] - 2026-10-18
### Added
- Transcript source setting. **Auto** (default) uses YouTube captions and, when YouTube blocks the
  connection or a video has no captions, Gemini transcribes the video from its link.

## [1.0.1] - 2026-10-18
### Fixed
- Mac: opening the app again no longer shows "The application is not open anymore". Added a menu bar icon
  with Open and Quit.

## [1.0.0] - 2026-10-18
### Added
- First release: find YouTube videos by keyword, review them with their stats, and turn the ones you pick
  into articles with title options, editor notes, flagged claims and image prompts. Projects per niche,
  local database, downloadable apps for Windows and Mac.

[1.1.0]: https://github.com/saadey/article-writer/releases/tag/v1.1.0
[1.0.2]: https://github.com/saadey/article-writer/releases/tag/v1.0.2
[1.0.1]: https://github.com/saadey/article-writer/releases/tag/v1.0.1
[1.0.0]: https://github.com/saadey/article-writer/releases/tag/v1.0.0
