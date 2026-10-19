"""So `python -m audit_watch` invokes main()."""

import sys  # pragma: no cover

from .main import main  # pragma: no cover

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
