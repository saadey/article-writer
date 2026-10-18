# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Instead, report them privately through
GitHub: open the repository's **Security** tab and click **Report a vulnerability**
(<https://github.com/saadey/article-writer/security/advisories/new>).

Include what you found, how to reproduce it, and what an attacker could do with it. You'll get a reply
as soon as possible, and fixes are credited in the changelog unless you'd rather stay anonymous.

## How the app protects you

- It only listens on your own computer (`127.0.0.1`) and rejects requests addressed to any other host name.
- Every action needs a per-launch security token, so other websites can't trigger actions in it.
- API keys are stored in a local `.env` file readable only by your user account, never in the database
  or sent anywhere except to Google's APIs.

## Supported versions

Only the latest release receives fixes. Update from the
[Releases page](https://github.com/saadey/article-writer/releases).
