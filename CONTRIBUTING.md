# Contributing to Knack

Thanks for your interest in contributing to Knack! Here's how to get started.

## Getting Started

1. Fork the repo and clone your fork
2. Install prerequisites: `docker`, `uv`, `nc` (netcat)
3. Run `uv sync` to install Python dependencies
4. Start infrastructure: `./knack infra up`

## Development Workflow

1. Create a feature branch from `main`
2. Make your changes
3. Test with a quick benchmark run: `./knack run --quick`
4. Commit with a clear message describing the change
5. Open a pull request against `main`

## What to Contribute

- New benchmark scenarios or metrics
- Chart improvements or new visualizations
- Bug fixes and reliability improvements
- Documentation improvements
- Performance optimizations

## Code Style

- Shell scripts: follow the existing style (Bash 4+, `set -euo pipefail`)
- Python: format with `ruff` and type-check where practical
- Keep scripts self-contained — avoid adding external dependencies unless necessary

## Reporting Issues

Open an issue on GitHub with:
- What you expected vs. what happened
- Steps to reproduce
- Your environment (OS, Docker version, Python version)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
