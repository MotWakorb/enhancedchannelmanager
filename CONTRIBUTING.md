# Contributing to Enhanced Channel Manager

Thanks for your interest in contributing. This guide is for **human contributors**. If you are using Claude or another AI coding agent on this project, see [CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md) — those files contain agent-specific workflow instructions that do not apply to human PRs.

## Ways to Contribute

- **Report bugs** — file a [GitHub issue](https://github.com/MotWakorb/enhancedchannelmanager/issues) with reproduction steps, expected vs. actual behavior, and your ECM version
- **Report security issues** — **do not** use GitHub issues. See [SECURITY.md](SECURITY.md) for the private disclosure flow
- **Request features** — file a GitHub issue with the `enhancement` label describing the problem you're trying to solve (not just the solution)
- **Send a pull request** — see below

## Local Development

Setup instructions (Docker Compose, development mode, environment variables) live in the [README](README.md#installation). Don't duplicate that here — follow the README for first-run setup.

## Project Structure & Architecture

Before making non-trivial changes, skim [docs/project_architecture.md](docs/project_architecture.md) for the overall system layout (backend routers, frontend split, container/volume layout, test harnesses).

Additional reference material in `docs/`:

- [testing.md](docs/testing.md) — test strategy and conventions
- [pytest_conventions.md](docs/pytest_conventions.md) — backend test patterns
- [css_guidelines.md](docs/css_guidelines.md) — frontend styling conventions
- [api.md](docs/api.md) — HTTP API reference (Swagger UI is served at `/api/docs` on a running instance)
- [dispatcharr_api.md](docs/dispatcharr_api.md) — upstream Dispatcharr integration notes

## Branching & PR Process

- Target branch: **`dev`**. `main` is the release branch; tags and GitHub Releases are cut from `main`
- Branch off `dev` with a descriptive name, e.g. `fix/ffmpeg-metadata-parsing` or `feat/bulk-profile-assign`
- Keep PRs focused — one logical change per PR. Large, multi-concern PRs are hard to review and hard to revert
- Write a clear PR description: what changed, why, how to test, and any user-visible impact
- Link the GitHub issue your PR addresses (e.g. "closes #54")
- PRs to `dev` are **squash-merged**. Write a commit title that would be a usable changelog line

## Required Local Checks

Run these before opening or updating a PR. CI will run them too, but it is faster to catch issues locally.

**Frontend** (from `frontend/`):

```bash
npm install         # first time / after dependency changes
npm run lint
npm run typecheck
npm test
npm run build
```

**Backend** (from `backend/`):

```bash
pip install -r requirements.txt     # or `uv pip install -r requirements.txt`
python -m pytest tests/ -q
```

If a check fails, fix it before pushing. Do not rely on CI to tell you about obvious local failures.

## Code Style

- **Frontend** — TypeScript strict mode; follow the existing Vite/React patterns. Lint and typecheck must pass
- **Backend** — Python 3.12, FastAPI. Keep routers in `backend/routers/` modular and focused
- **Naming** — describe what the thing *is*, not what it is *not*. Avoid overloading a single term across multiple concepts
- **Comments** — explain *why*, not *what*. The code already says *what*

## Documentation Changes

- User-visible feature or behavior change → update the [README](README.md) in the same PR
- API change → make sure the FastAPI route definition still produces correct Swagger output; update [docs/api.md](docs/api.md) if it has hand-written supplements
- Architectural change → update [docs/project_architecture.md](docs/project_architecture.md)
- Release notes happen at release time (GitHub Releases), not per-PR

## Commit Messages

Squash merges use the PR title/description as the final commit message, so put care into both. A good PR title is short (≤70 characters) and action-oriented, e.g. `Fix black screen scan erasing manual-probe findings on timeout`.

## Getting Help

- **Questions on an issue or PR** — comment on the issue/PR directly
- **Broader discussion** — open a GitHub Discussion (if enabled) or a `question`-labeled issue

## Code of Conduct

Be constructive and respectful. Treat other contributors how you would want to be treated when you're the one asking for help or having your code reviewed.

## License

By submitting a pull request, you agree that your contribution will be licensed under the same license as the rest of the project.
