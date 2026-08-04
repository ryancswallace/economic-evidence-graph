# Devcontainer

This devcontainer is the reproducible contributor environment for the project.

It provides:

* Python 3.14 on Debian Bookworm.
* uv 0.12.1, matching the runtime Dockerfile.
* Node.js 24 for Markdown, spelling, workflow, and Dockerfile checks.
* GitHub CLI for pull request and release workflows.
* Docker-outside-of-Docker for optional local container checks.
* VS Code recommendations for Ruff, basedpyright, Markdown, CSpell, GitHub
    Actions, containers, TOML, YAML, and Makefiles.

The create hook installs the locked Python and Node dependencies and then
installs the pre-commit and pre-push hooks:

```bash
make hooks-install
```

Run the full validation suite explicitly before submitting changes:

```bash
make check
```

Docker-outside-of-Docker exposes the host Docker socket inside the container.
That is useful for `make docker-check`, but it means the devcontainer should be
treated as a trusted development environment.
