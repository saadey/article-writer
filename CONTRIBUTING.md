# Contributing

Thanks for helping improve Article Writer! Bug reports, ideas and pull requests are all welcome.

## Reporting bugs and ideas

Open an [issue](https://github.com/saadey/article-writer/issues/new/choose). For bugs, include your
operating system, the app version (shown in the footer), what you did, and what the **Activity** page log
says. Remove API keys or anything private before posting.

## Working on the code

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py          # the app, at http://localhost:8000
python main.py --help  # the command line
```

- Keep it simple and readable; match the style of the surrounding code.
- Settings belong in the project settings (`article_pipeline/config.py` + `web/forms.py`), not hardcoded.
- Never commit `.env`, `data/`, `logs/` or `exports/`.
- Describe what you changed and how you tested it in the pull request.

## Building the apps

```bash
pip install -r requirements-dev.txt
python packaging/make_icons.py       # only after changing assets/logo.svg
python packaging/collect_licenses.py
pyinstaller packaging/article_writer.spec
```

Releases are built by GitHub Actions when a version tag is pushed (see the README).

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
