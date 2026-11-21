#!/usr/bin/env bash
# Prints packages/<pkg>/VERSION paths, one per line, for the shared workflows'
# `version_files_command` input (bump-version.yml, assemble-changelog.yml).
#
#   all      every package that has a VERSION file (release-time fold)
#   changed  packages this PR touches, diffed against origin/$BASE_REF.
#            A change under packages/core also lists every package whose
#            pyproject depends on oke-scanner-core: their images bake core in,
#            so a core change ships a new build of each of them.
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
    if printf '%s\n' "${PKGS}" | grep -qx core; then
      DEPENDENTS="$(grep -l '"oke-scanner-core' packages/*/pyproject.toml | cut -d/ -f2 || true)"
      PKGS="$(printf '%s\n%s\n' "${PKGS}" "${DEPENDENTS}" | sed '/^$/d' | sort -u)"
    fi
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
