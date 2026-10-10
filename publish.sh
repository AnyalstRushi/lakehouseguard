#!/usr/bin/env bash
# Publish this repo to GitHub and turn on the live dashboard (GitHub Pages).
# Needs: git, GitHub CLI (`gh auth login` done once).
set -euo pipefail
OWNER="${1:-AnyalstRushi}"
REPO="lakehouseguard"

git init -b main
git add .
git commit -m "feat: LakehouseGuard v1.0.0 (contracts, observability, self-healing, AI incident agent)"
gh repo create "$OWNER/$REPO" --public --source=. --remote=origin --push \
  --description "Contract-driven, self-healing Medallion lakehouse with an offline-first AI incident agent"
gh repo edit "$OWNER/$REPO" --add-topic data-engineering --add-topic data-quality --add-topic data-contracts \
  --add-topic data-observability --add-topic medallion-architecture --add-topic lakehouse --add-topic genai --add-topic rag
# Serve Pages from GitHub Actions (the workflow in .github/workflows/pages.yml builds the dashboard)
gh api -X POST "repos/$OWNER/$REPO/pages" -f build_type=workflow || echo "Pages already enabled (or enable it in Settings > Pages > Source: GitHub Actions)"
echo "Done. CI: https://github.com/$OWNER/$REPO/actions   Dashboard: https://$OWNER.github.io/$REPO/"
