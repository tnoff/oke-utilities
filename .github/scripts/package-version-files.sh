#!/usr/bin/env bash
# Prints packages/<pkg>/VERSION paths, one per line, for the shared workflows'
# `version_files_command` input (bump-version.yml, assemble-changelog.yml).
#
#   all      every package that has a VERSION file (release-time fold)
#   changed  packages this PR touches, diffed against origin/$BASE_REF.
#            A core change bumps core only: its dependents pin a core-vX.Y.Z
#            tarball, so they move when Renovate bumps that pin, which touches
#            (and therefore bumps) each of them.
#
# Prints nothing when no package is affected; bump-version then bumps nothing.
set -euo pipefail

all_files() {
  find packages -mindepth 2 -maxdepth 2 -name VERSION | sort
}

case "${1:-}" in
  all)
    all_files
    ;;
  changed)
    : "${BASE_REF:?BASE_REF must be set}"
    PKGS="$(git diff --name-only "origin/${BASE_REF}...HEAD" \
      | sed -nE 's#^packages/([^/]+)/.*#\1#p' | sort -u)"
    for p in ${PKGS}; do
      if [ -f "packages/${p}/VERSION" ]; then
        echo "packages/${p}/VERSION"
      fi
    done
    ;;
  *)
    echo "usage: $0 all|changed" >&2
    exit 2
    ;;
esac
